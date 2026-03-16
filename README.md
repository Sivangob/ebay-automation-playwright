# eBay E2E Test Suite

Playwright-based end-to-end tests for the eBay shopping flow (search → add to
cart → verify totals), with parallel execution via pytest-xdist and live Allure
reports deployed to GitHub Pages.

---

## Quick start

```bash
# Install dependencies
pip install -r requirements.txt
playwright install --with-deps chrome firefox

# Run with default profile (Chrome, headless in CI / headed locally)
pytest

# Run two specific profiles in parallel
pytest --browser-profile chrome_127 --browser-profile edge_latest -n 2
```

---

## Browser matrix & profiles

### How profiles work

Every test run is parametrized by a **browser profile** — a named configuration
that controls which Playwright engine is launched, which browser channel is used,
and what User-Agent string is sent to the site.

Profiles are defined in `_PROFILE_REGISTRY` inside `conftest.py`. Each entry
contains:

| Key | Type | Purpose |
|---|---|---|
| `engine` | `"chromium" \| "firefox" \| "webkit"` | Playwright engine to launch |
| `label` | `str` | Display name shown in Allure titles and log lines |
| `channel` | `str \| None` | Playwright channel (`"chrome"`, `"msedge"`) or `None` for the bundled build |
| `user_agent` | `str \| None` | UA override presented to the site, or `None` to use the browser default |
| `extra_args` | `list[str]` | Additional CLI flags forwarded to `browser.launch(args=[…])` |

### Available profiles

| Profile key | Label | Engine | Channel | Version simulation |
|---|---|---|---|---|
| `chromium` | Chrome | chromium | `chrome` | latest (binary default) |
| `firefox` | Firefox | firefox | — | latest (binary default) |
| `webkit` | WebKit | webkit | — | latest (binary default) |
| `chrome_latest` | Chrome Latest | chromium | `chrome` | latest (binary default) |
| `chrome_128` | Chrome 128 | chromium | `chrome` | UA override → `Chrome/128.0.0.0` |
| `chrome_127` | Chrome 127 | chromium | `chrome` | UA override → `Chrome/127.0.0.0` |
| `edge_latest` | Edge Latest | chromium | `msedge` | latest (binary default) |
| `firefox_latest` | Firefox Latest | firefox | — | latest (binary default) |

### Version simulation

Playwright does not distribute multiple Chrome versions. Version-specific
profiles (`chrome_127`, `chrome_128`, …) run against the installed Chrome binary
but override the `User-Agent` header so the site sees the target version. This
covers the vast majority of compatibility tests that rely on UA sniffing.

If you need to test against an actual older binary, add an `"executable_path"`
key to the profile entry and call `browser.launch(executable_path=…)` — the
`_launch_browser()` helper in `conftest.py` is the single place to extend.

### Headless mode

`conftest.py` reads the `CI` environment variable:

```python
headless = os.environ.get("CI", "false").lower() == "true"
```

Set `CI=true` in your shell to force headless mode locally.  GitHub Actions sets
this automatically.

### Allure labelling

Each test title and every log line is stamped with the profile label:

```
[Chrome 127][gw0][INFO]  pages.search_page: Searching for "laptop"
[Edge Latest][gw1][INFO] pages.product_page: Adding item to cart
```

In the Allure report, two labels are attached per test:

- **browser** — human-readable label (e.g. `Chrome 128`)
- **profile** — raw profile key (e.g. `chrome_128`)

Use the Allure sidebar to filter by either label across a multi-profile run.

---

## Running locally

```bash
# Single profile
pytest --browser-profile chrome_127

# Multiple profiles, 4 workers
pytest --browser-profile chrome_127 --browser-profile chrome_128 -n 4

# All Chrome profiles + Edge in parallel
pytest \
  --browser-profile chrome_latest \
  --browser-profile chrome_128 \
  --browser-profile chrome_127 \
  --browser-profile edge_latest \
  -n auto

# Legacy --browser shorthand (still supported)
pytest --browser chromium --browser firefox

# View the Allure report after a run
allure serve allure-results/run_<timestamp>
```

---

## Adding a new profile

1. Open `conftest.py` and add an entry to `_PROFILE_REGISTRY`:

```python
"chrome_126": {
    "engine": "chromium",
    "label": "Chrome 126",
    "channel": "chrome",
    "user_agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "extra_args": _CHROMIUM_ARGS,
},
```

2. The profile key (`"chrome_126"`) is immediately available as a
   `--browser-profile` value — no other changes are required.

3. To include it in CI, add it to the relevant `profiles:` line in
   `.github/workflows/main.yml` under the `chrome-profiles` matrix entry.

---

## CI/CD pipeline

The GitHub Actions workflow (`.github/workflows/main.yml`) runs on every push
and pull request to `main`.

### Matrix jobs

Tests are split across three parallel jobs, each installing only the browser
binary it needs:

| Job | Profiles | Browser installed |
|---|---|---|
| `chrome-profiles` | `chrome_latest`, `chrome_128`, `chrome_127` | `chrome` |
| `edge-profile` | `edge_latest` | `msedge` |
| `firefox-profile` | `firefox_latest` | `firefox` |

Each job runs its profiles with `-n auto` (all CPU cores) and uploads its raw
Allure results as a workflow artifact.

### Report job

After all matrix jobs complete a `report` job:

1. Downloads and merges every `allure-results-*` artifact into one directory
   (Allure result files are UUID-named so there are no collisions).
2. Generates a single combined HTML report with `allure generate`.
3. Uploads the HTML report as a `allure-report-run<N>` artifact (30-day
   retention).
4. Deploys the report to the `gh-pages` branch via
   `peaceiris/actions-gh-pages` — **only on pushes to `main`**, never on PRs.

### One-time GitHub Pages setup

> Repo → **Settings** → **Pages** → Source: **Deploy from a branch** →
> Branch: `gh-pages` / `/ (root)` → **Save**

### Auth state secret

Store your eBay session cookie as a base64-encoded GitHub secret:

```bash
base64 -w0 data/auth_state.json   # copy output
# GitHub → Settings → Secrets → Actions → New: AUTH_STATE_JSON
```

Tests run without a saved session if the secret is absent (useful for smoke
testing the pipeline itself).
