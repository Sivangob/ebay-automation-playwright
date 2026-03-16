import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Generator

import allure
import pytest
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
from playwright_stealth import Stealth
from utils.config_loader import cfg

_PROJECT_ROOT = Path(__file__).parent
_RESULTS_BASE = _PROJECT_ROOT / "allure-results"   # base dir for per-run subdirs
_REPORT_DIR   = _PROJECT_ROOT / "allure-report"    # generated HTML report (persisted)
AUTH_STATE_PATH = Path(
    os.environ.get("AUTH_STATE_PATH", str(_PROJECT_ROOT / "data" / "auth_state.json"))
)

# ── Browser profile registry ───────────────────────────────────────────────────
#
# Each entry maps a profile key to:
#   engine      – Playwright engine:  "chromium" | "firefox" | "webkit"
#   label       – Display name used in Allure titles and log lines
#   channel     – Playwright channel string ("chrome", "msedge") or None
#                 to launch the bundled engine build instead
#   user_agent  – Override UA string presented to the site, or None to let
#                 the browser report its own UA
#   extra_args  – Additional CLI flags forwarded to browser.launch(args=[…])
#
# Version simulation note
# ───────────────────────
# Playwright does not ship multiple Chrome versions; all "chrome" channel
# profiles share the same installed binary.  Version-specific profiles
# (chrome_127, chrome_128, …) simulate the target version by overriding the
# User-Agent string, which is what most sites inspect for compatibility.
# If you need the actual binary for a specific version, add an
# "executable_path" key here and extend _launch_browser() below.
#
# Adding a new profile
# ────────────────────
# Add one dict entry below.  The key becomes a valid --browser-profile value
# immediately — no other changes required.

_CHROMIUM_ARGS: list[str] = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-infobars",
    "--disable-extensions",
]

_PROFILE_REGISTRY: dict[str, dict] = {
    # ── Backwards-compatible engine aliases (--browser chromium / firefox / webkit)
    "chromium": {
        "engine": "chromium",
        "label": "Chrome",
        "channel": "chrome",
        "user_agent": None,
        "extra_args": _CHROMIUM_ARGS,
    },
    "firefox": {
        "engine": "firefox",
        "label": "Firefox",
        "channel": None,
        "user_agent": None,
        "extra_args": [],
    },
    "webkit": {
        "engine": "webkit",
        "label": "WebKit",
        "channel": None,
        "user_agent": None,
        "extra_args": [],
    },
    # ── Named Chrome profiles ────────────────────────────────────────────────
    "chrome_latest": {
        "engine": "chromium",
        "label": "Chrome Latest",
        "channel": "chrome",
        "user_agent": None,
        "extra_args": _CHROMIUM_ARGS,
    },
    "chrome_128": {
        "engine": "chromium",
        "label": "Chrome 128",
        "channel": "chrome",
        "user_agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "extra_args": _CHROMIUM_ARGS,
    },
    "chrome_127": {
        "engine": "chromium",
        "label": "Chrome 127",
        "channel": "chrome",
        "user_agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/127.0.0.0 Safari/537.36"
        ),
        "extra_args": _CHROMIUM_ARGS,
    },
    # ── Microsoft Edge profiles ──────────────────────────────────────────────
    "edge_latest": {
        "engine": "chromium",
        "label": "Edge Latest",
        "channel": "msedge",
        "user_agent": None,
        "extra_args": _CHROMIUM_ARGS,
    },
    # ── Firefox profiles ─────────────────────────────────────────────────────
    "firefox_latest": {
        "engine": "firefox",
        "label": "Firefox Latest",
        "channel": None,
        "user_agent": None,
        "extra_args": [],
    },
}

_PROFILE_CHOICES: list[str] = sorted(_PROFILE_REGISTRY.keys())


def _resolve_profile(profile_key: str) -> dict:
    """Return the registry entry for *profile_key*, raising clearly if unknown."""
    try:
        return _PROFILE_REGISTRY[profile_key]
    except KeyError:
        valid = ", ".join(_PROFILE_CHOICES)
        raise ValueError(
            f"Unknown browser profile {profile_key!r}. Valid choices: {valid}"
        ) from None


# ── Browser-aware logging ─────────────────────────────────────────────────────
# setLogRecordFactory is the only hook that injects attributes into every
# LogRecord regardless of which child logger created it.

_log_ctx: dict = {"browser": "?", "worker": "main"}
_original_log_factory = logging.getLogRecordFactory()


def _browser_aware_factory(*args, **kwargs) -> logging.LogRecord:
    record = _original_log_factory(*args, **kwargs)
    record.browser = _log_ctx["browser"]  # type: ignore[attr-defined]
    record.worker = _log_ctx["worker"]    # type: ignore[attr-defined]
    return record


logging.setLogRecordFactory(_browser_aware_factory)


# ── Timestamped Allure results directory ─────────────────────────────────────
#
# Goal:
#   • Every run lands in  allure-results/run_YYYYMMDD_HHMMSS/  (flat – no
#     gw0/gw1 sub-directories regardless of how many xdist workers are used).
#   • allure-results/latest  is a symlink that always points to the most
#     recent run directory.
#   • history/*.json from the previous generated report is seeded into each
#     new run directory so the Allure Trend chart works out of the box.
#   • After every run a full HTML report is regenerated to allure-report/
#     (updates the history for the next run) and the serve command printed
#     at the end points straight at that report.
#
# Per-run flow (controller process):
#   pytest_configure  → create run dir, seed history, store path in env
#   pytest_sessionstart → safety-net: hard-patch AllureFileLogger._report_dir
#   [tests run]
#   pytest_sessionfinish → generate HTML report, update 'latest' symlink,
#                          print allure open / allure serve commands
#
# xdist workers:
#   pytest_configure (tryfirst=True) → read ALLURE_RUN_DIR from env, patch
#                                      config.option before allure-pytest reads it
#   pytest_sessionstart → same safety-net patch as controller

_ENV_KEY = "ALLURE_RUN_DIR"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _seed_history(run_dir: str) -> None:
    """Copy history JSON files from the last generated report into *run_dir*.

    Allure looks for  <results>/history/*.json  when generating a report.
    Seeding them here means the Trend chart shows data from previous runs
    without any manual steps.
    """
    history_src = _REPORT_DIR / "history"
    if not history_src.is_dir():
        return
    history_dst = Path(run_dir) / "history"
    history_dst.mkdir(exist_ok=True)
    for src_file in history_src.iterdir():
        if src_file.is_file():
            shutil.copy2(src_file, history_dst / src_file.name)


def _generate_report(run_dir: str) -> None:
    """Re-generate the HTML report from *run_dir* into allure-report/.

    This keeps allure-report/history/ up-to-date so the next run can seed
    from it.  Silently skipped when the allure CLI is not installed.
    """
    try:
        subprocess.run(
            ["allure", "generate", run_dir, "-o", str(_REPORT_DIR), "--clean"],
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # allure CLI absent or timed out – history won't be refreshed


def _update_latest_symlink(run_dir: str) -> None:
    """Point  allure-results/latest  at the just-finished run directory.

    Uses a relative symlink (just the folder name) so the link stays valid
    even if the project is moved to a different path.
    """
    latest = Path(run_dir).parent / "latest"
    target = Path(run_dir).name          # e.g.  run_20260316_153000
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(target)


# ── Hooks ─────────────────────────────────────────────────────────────────────

@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "")

    if worker_id:
        # ── Worker process ──────────────────────────────────────────────
        # The controller already created the directory and stored its path
        # in the environment before spawning us.  Patch the allure option
        # before allure-pytest's own pytest_configure reads it.
        run_dir = os.environ.get(_ENV_KEY)
        if run_dir and hasattr(config, "option"):
            config.option.allure_report_dir = run_dir
    else:
        # ── Controller (or plain non-xdist) process ─────────────────────
        # Build a timestamped sub-directory inside whatever base dir the
        # user supplied via --alluredir (or fall back to allure-results/).
        base_dir = (
            getattr(config.option, "allure_report_dir", None)
            or str(_RESULTS_BASE)
        )
        ts = datetime.now().strftime("run_%Y%m%d_%H%M%S")
        run_dir = str(Path(base_dir) / ts)

        # Create the directory now so allure-pytest finds it already there
        # when its own pytest_configure runs immediately after ours.
        Path(run_dir).mkdir(parents=True, exist_ok=True)

        # Seed history from the last generated report so Trend data carries over.
        _seed_history(run_dir)

        # Patch the option so allure-pytest uses our timestamped path.
        if hasattr(config, "option"):
            config.option.allure_report_dir = run_dir

        # Store in the environment so worker subprocesses inherit it.
        os.environ[_ENV_KEY] = run_dir


def pytest_sessionstart(session: pytest.Session) -> None:
    """Safety net: hard-patch AllureFileLogger._report_dir after all configure hooks settle.

    Corrects the rare ordering edge-case where allure-pytest initialised its
    file logger before our pytest_configure hook ran (which would point it at
    the wrong path and produce gw0/gw1 sub-directories or missing results).
    Runs on both the controller and every xdist worker.
    """
    run_dir = os.environ.get(_ENV_KEY)
    if not run_dir:
        return

    import allure_commons

    run_path = Path(run_dir).absolute()
    run_path.mkdir(parents=True, exist_ok=True)

    for plugin in allure_commons.plugin_manager.get_plugins():
        if hasattr(plugin, "_report_dir"):
            plugin._report_dir = run_path


# ── CLI options ───────────────────────────────────────────────────────────────

def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--browser",
        action="append",
        default=[],
        choices=["chromium", "firefox", "webkit"],
        help=(
            "Browser engine(s) to run against (backwards-compatible shorthand). "
            "Prefer --browser-profile for named version profiles. "
            "May be repeated: --browser chromium --browser firefox"
        ),
    )
    parser.addoption(
        "--browser-profile",
        action="append",
        default=[],
        choices=_PROFILE_CHOICES,
        metavar="PROFILE",
        help=(
            "Named browser profile(s) to run against. "
            "Takes precedence over --browser when both are supplied. "
            "May be repeated: --browser-profile chrome_127 --browser-profile edge_latest. "
            f"Available profiles: {', '.join(_PROFILE_CHOICES)}"
        ),
    )


# ── Cross-browser / cross-profile parametrization ────────────────────────────

def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize every test that uses 'browser_name' with the selected profiles.

    Resolution order (first non-empty list wins):
      1. --browser-profile  (named profiles)
      2. --browser          (engine aliases, backwards-compatible)
      3. default            ["chromium"]
    """
    if "browser_name" not in metafunc.fixturenames:
        return

    profiles = metafunc.config.getoption("--browser-profile")
    browsers = metafunc.config.getoption("--browser")
    params = profiles or browsers or ["chromium"]

    metafunc.parametrize("browser_name", params, indirect=True, scope="session")


# ── Playwright session fixtures ───────────────────────────────────────────────

@pytest.fixture(scope="session")
def playwright_instance():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser_name(request: pytest.FixtureRequest) -> str:
    """Returns the parametrized profile key injected by pytest_generate_tests."""
    return request.param  # type: ignore[return-value]


def _launch_browser(playwright_instance, profile_key: str, headless: bool) -> Browser:
    """Resolve *profile_key* and launch the corresponding Playwright browser."""
    profile = _resolve_profile(profile_key)
    engine = getattr(playwright_instance, profile["engine"])

    launch_kwargs: dict = {"headless": headless}
    if profile["channel"]:
        launch_kwargs["channel"] = profile["channel"]
    if profile["extra_args"]:
        launch_kwargs["args"] = profile["extra_args"]

    return engine.launch(**launch_kwargs)


@pytest.fixture(scope="session")
def browser(playwright_instance, browser_name: str) -> Generator[Browser, None, None]:
    """One browser process per (worker × profile) session."""
    headless = os.environ.get("CI", "false").lower() == "true"
    b = _launch_browser(playwright_instance, browser_name, headless)
    yield b
    b.close()


# ── Per-test context / page ───────────────────────────────────────────────────

def _build_context(browser: Browser, profile_key: str) -> BrowserContext:
    """Isolated BrowserContext per test, configured from the profile registry."""
    profile = _resolve_profile(profile_key)

    # נשתמש ב-User Agent של 127 כברירת מחדל כי הוא עבר את ה-Captcha ב-CI
    fallback_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"

    kwargs: dict = dict(
        viewport={"width": 1920, "height": 1080},  # הגדלנו ל-Full HD
        locale="en-US",
        timezone_id="America/New_York",
        java_script_enabled=True,
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        user_agent=profile["user_agent"] if profile["user_agent"] else fallback_ua,
        device_scale_factor=1,
    )

    if AUTH_STATE_PATH.exists():
        kwargs["storage_state"] = str(AUTH_STATE_PATH)
        print(f"\n[conftest] Loaded auth state from: {AUTH_STATE_PATH}")
    else:
        print(
            f"\n[conftest] WARNING: auth state not found at {AUTH_STATE_PATH}"
            " — running without session"
        )

    context = browser.new_context(**kwargs)

    # הזרקת ה-Stealth לכל קונטקסט שנוצר
    from playwright_stealth import Stealth
    Stealth().apply_stealth_sync(context)

    return context


@pytest.fixture(scope="function")
def page(browser: Browser, browser_name: str) -> Generator[Page, None, None]:
    """Fresh BrowserContext + Page per test; context is always closed on teardown."""
    context = _build_context(browser, browser_name)
    pg = context.new_page()
    Stealth().apply_stealth_sync(pg)
    pg.set_default_navigation_timeout(cfg("timeouts", "navigation_ms"))
    pg.set_default_timeout(cfg("timeouts", "default_ms"))
    yield pg
    context.close()


# ── Allure title, labels & log-context stamp ──────────────────────────────────

@pytest.fixture(autouse=True)
def _test_context_stamp(
    request: pytest.FixtureRequest,
    browser_name: str,
) -> None:
    """
    Runs before every test (autouse):
      • Stamps _log_ctx so every log line carries [Profile Label][worker].
      • Sets an Allure dynamic title: [Profile Label] test_name
      • Attaches Allure labels for 'browser' and 'profile' for report filtering.
    """
    profile = _resolve_profile(browser_name)
    label = profile["label"]

    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
    _log_ctx["browser"] = label
    _log_ctx["worker"] = worker_id

    allure.dynamic.title(f"[{label}] {request.node.originalname}")
    allure.dynamic.label("browser", label)
    allure.dynamic.label("profile", browser_name)


# ── Post-run: generate report, update symlink, print commands ────────────────

def pytest_sessionfinish(session: pytest.Session, exitstatus: object) -> None:
    """Controller-only post-run hook.

    1. Regenerates the HTML report to allure-report/ so history/*.json is
       refreshed for the next run's Trend chart.
    2. Updates the allure-results/latest symlink.
    3. Prints ready-to-run allure commands.
    """
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return

    run_dir = os.environ.get(_ENV_KEY)
    if not run_dir:
        return

    _generate_report(run_dir)
    _update_latest_symlink(run_dir)

    sep = "=" * 62
    print(f"\n{sep}")
    print(f"  Results  : {run_dir}")
    print(f"  Latest   : {_RESULTS_BASE / 'latest'}  →  {Path(run_dir).name}")
    print(f"  Report   : allure open {_REPORT_DIR}")
    print(f"  Or serve : allure serve {_RESULTS_BASE / 'latest'}")
    print(f"{sep}\n")


# ── Allure failure screenshot ─────────────────────────────────────────────────

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call) -> None:
    """Attach a full-page screenshot to the Allure report on test failure."""
    outcome = yield
    report = outcome.get_result()

    if report.when == "call" and report.failed:
        pg: Page | None = item.funcargs.get("page")
        if pg is not None:
            try:
                png_bytes = pg.screenshot(full_page=True)
                allure.attach(
                    png_bytes,
                    name="screenshot_on_failure",
                    attachment_type=allure.attachment_type.PNG,
                )
            except Exception:
                pass
