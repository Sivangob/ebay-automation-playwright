# eBay E2E Test Suite

Playwright-based end-to-end tests for the eBay shopping flow
(**search → add to cart → assert total budget**), with parallel execution via
`pytest-xdist`, structured Allure reports persisted across runs, and a fully
automated CI/CD pipeline that publishes a live HTML report to GitHub Pages on
every push to `main`.

---
## 📺 Demo
Check out the full end-to-end flow in action (recorded in Headed mode):

![eBay Automation Demo](path/to/your/video.mp4)

> **Note:** The video demonstrates session-based login, search with filters, and cart validation.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start](#quick-start)
3. [Project Structure](#project-structure)
4. [Browser Matrix & Profiles](#browser-matrix--profiles)
5. [Running Locally](#running-locally)
6. [Adding a New Profile](#adding-a-new-profile)
7. [CI/CD Pipeline](#cicd-pipeline)
8. [Viewing Live Reports (GitHub Pages)](#viewing-live-reports-github-pages)
9. [Allure Report Architecture](#allure-report-architecture)

---

## Prerequisites

| Requirement | Minimum version | Notes |
|---|---|---|
| Python | **3.12** | Required for `match`-statement typing used in helpers |
| pip | bundled with Python 3.12 | `python -m pip install --upgrade pip` before setup |
| Allure CLI | **2.32+** | Only needed for local `allure open` / `allure serve` — not required for CI |
| Java | 11+ | Runtime dependency of the Allure CLI |

### Installing Allure CLI locally

**macOS (Homebrew)**
```bash
brew install allure
```

**Linux / WSL**
```bash
# Download and install the same version the CI pipeline uses
ALLURE_VERSION="2.32.2"
wget -qO allure.tgz \
  "https://github.com/allure-framework/allure2/releases/download/${ALLURE_VERSION}/allure-${ALLURE_VERSION}.tgz"
sudo tar -xzf allure.tgz -C /opt
sudo ln -sf "/opt/allure-${ALLURE_VERSION}/bin/allure" /usr/local/bin/allure
allure --version
```

**Windows**
```powershell
choco install allure   # or scoop install allure
```

---

## Quick Start

```bash
# 1. Clone and enter the repository
git clone <repo-url> && cd ebay

# 2. Create and activate a virtual environment
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install Playwright browsers and their OS-level system libraries
#    (libnss3, libatk, libgbm, etc. — required on Linux / WSL)
playwright install --with-deps chrome
playwright install-deps             # explicit system-dependency pass for Linux/WSL

# 5. Provide credentials (required for the login step)
export EBAY_USERNAME="your@email.com"
export EBAY_PASSWORD="yourpassword"

# 6. Run the suite (Chrome, headed, 4 parallel workers)
pytest --browser-profile chrome_latest -n 4

# 7. Open the generated HTML report
allure open allure-report
# — or serve the raw results directly:
allure serve allure-results/latest
```

> **Linux / WSL note** — `playwright install-deps` installs the OS packages
> that Chromium, Firefox, and WebKit require (libgtk, libnss3, libgbm, etc.).
> Without this step the browser process will crash silently on headless Linux.

---

## Project Structure

```
ebay/
├── conftest.py              # Session fixtures, xdist routing, Allure dir management
├── pytest.ini               # addopts: -n auto, live log format
│
├── tests/
│   └── test_ebay_flow.py    # Single end-to-end scenario
│
├── pages/
│   ├── base_page.py         # Smart-locator core, validation-page detection, helpers
│   ├── login_page.py        # eBay sign-in flow
│   ├── search_page.py       # searchItemsByNameUnderPrice (price filter + XPath)
│   └── product_page.py      # addItemsToCart, clearCart, assertCartTotalNotExceeds
│
├── utils/
│   └── config_loader.py     # cfg(), selectors(), locators(), get_credentials()
│
├── data/
│   ├── test_config.json     # URLs, timeouts, search parameters
│   ├── locators.json        # All CSS / XPath selectors (no selectors in page objects)
│   └── auth_state.json      # Playwright storage state (gitignored)
│
├── allure-results/          # Raw JSON results — one run_YYYYMMDD_HHMMSS/ per run
│   └── latest -> run_…/     # Symlink updated after every run
├── allure-report/           # Generated HTML report (updated locally after each run)
│
└── .github/workflows/
    └── main.yml             # Matrix test jobs + report generation + Pages deployment
```

---

## Browser Matrix & Profiles

### How profiles work

Every test run is parametrized by a **browser profile** — a named configuration
that controls which Playwright engine is launched, which browser channel is used,
and what User-Agent string is presented to the site.

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
key to the profile entry — `_launch_browser()` in `conftest.py` is the single
place to extend for this.

### Headless mode

`conftest.py` reads the `CI` environment variable:

```python
headless = os.environ.get("CI", "false").lower() == "true"
```

Set `CI=true` in your shell to force headless mode locally. GitHub Actions sets
this automatically via the workflow-level `env` block.

### Allure labelling

Each test title and every log line is stamped with the profile label:

```
[Chrome 127][gw0][INFO]  pages.search_page: Searching for "shoes"
[Edge Latest][gw1][INFO] pages.product_page: Adding item to cart
```

In the Allure report, two labels are attached per test:

- **browser** — human-readable label (e.g. `Chrome 128`)
- **profile** — raw profile key (e.g. `chrome_128`)

Use the Allure sidebar to filter and group results by either label across a
multi-profile run.

---

## Running Locally

```bash
# Single profile
pytest --browser-profile chrome_127

# Multiple profiles, 4 workers
pytest --browser-profile chrome_127 --browser-profile chrome_128 -n 4

# All Chrome profiles + Edge in parallel (auto-detect CPU count)
pytest \
  --browser-profile chrome_latest \
  --browser-profile chrome_128 \
  --browser-profile chrome_127 \
  --browser-profile edge_latest \
  -n auto

# Legacy --browser shorthand (still supported)
pytest --browser chromium --browser firefox

# Open the auto-generated HTML report (rebuilt after each run)
allure open allure-report

# Or serve the raw results for the most recent run
allure serve allure-results/latest
```

After every run `conftest.py` automatically:

1. Generates a fresh HTML report to `allure-report/` (updates the Trend chart
   history for the next run).
2. Updates the `allure-results/latest` symlink to point at the new
   `run_YYYYMMDD_HHMMSS/` directory.
3. Prints a summary block:

```
══════════════════════════════════════════════════════════════
  Results  : allure-results/run_20260316_153000
  Latest   : allure-results/latest  →  run_20260316_153000
  Report   : allure open allure-report
  Or serve : allure serve allure-results/latest
══════════════════════════════════════════════════════════════
```

---

## Adding a New Profile

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
   `--browser-profile` value — no other changes are required locally.

3. To include the profile in CI, add it to the `profiles:` line in
   `.github/workflows/main.yml` under the `chrome-profiles` matrix entry.

---
## 🛡️ Anti-Bot & Security Strategy

Running automation on live retail sites like eBay presents unique challenges due to aggressive anti-bot mechanisms (e.g., Distil Networks, Captcha, and MFA).

### Challenges Encountered
* **Account Locking:** Frequent login attempts from diverse IP addresses (especially GitHub Actions cloud IPs) can trigger automated account protection.
* **MFA / Captcha:** eBay often challenges non-browser-like behavior with "Press and Hold" or SMS verification.

### My Approach as a Senior Engineer
To ensure stability and protect account integrity, I implemented the following:
1. **Session Persistence:** Instead of active login on every run, the framework uses `auth_state.json`. If a valid session is detected, the UI login flow is skipped entirely.
2. **Playwright Stealth:** Integration of stealth techniques to minimize the automation footprint (User-Agent spoofing, fingerprinting protection).
3. **Human-like Interaction:** Added smart waits and specific navigation patterns to mimic real user behavior.
4. **Graceful Degradation:** The framework is designed to detect when a Captcha or MFA is presented. Instead of hanging, it captures a screenshot, logs the event clearly in Allure, and terminates the run safely.

---
## CI/CD Pipeline

The GitHub Actions workflow (`.github/workflows/main.yml`) triggers on every
push and pull request to `main`.

### Required GitHub Secrets

All three secrets must be configured before the workflow can execute a
meaningful test run. Navigate to:

> **Repository → Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Purpose | How to obtain |
|---|---|---|
| `EBAY_USERNAME` | eBay account email passed to the login step | Your eBay login email |
| `EBAY_PASSWORD` | eBay account password passed to the login step | Your eBay login password |
| `AUTH_STATE_JSON` | Base64-encoded Playwright storage-state JSON (session cookies + localStorage) | See encoding instructions below |

#### Encoding the auth state

`AUTH_STATE_JSON` is stored as Base64 so that GitHub Secrets can hold the full
JSON payload (which contains special characters) as a single opaque string.

```bash
# 1. Generate the session file locally (opens a browser for you to log in once)
python utils/save_auth_state.py

# 2. Base64-encode the result — -w0 disables line-wrapping (Linux/WSL)
base64 -w0 data/auth_state.json

# macOS equivalent (no -w0 needed)
base64 data/auth_state.json | tr -d '\n'

# 3. Copy the entire output and paste it as the value of AUTH_STATE_JSON
```

#### Automated decoding in the workflow

The workflow decodes the secret at runtime before any test step runs:

```yaml
- name: Write eBay auth state from secret
  run: |
    if [ -n "$AUTH_STATE_JSON" ]; then
      mkdir -p data
      printf '%s' "$AUTH_STATE_JSON" | base64 -d > data/auth_state.json
      echo "Auth state written to data/auth_state.json"
    else
      echo "WARNING: AUTH_STATE_JSON secret not set — running without saved session."
    fi
  env:
    AUTH_STATE_JSON: ${{ secrets.AUTH_STATE_JSON }}
```

`printf '%s'` (rather than `echo`) is used deliberately — it avoids appending a
trailing newline that would corrupt the Base64 input and cause `base64 -d` to
fail. If the secret is absent the workflow continues without a session, which is
useful for smoke-testing the pipeline structure itself.

### Matrix jobs

Tests are split across three parallel jobs, each installing only the browser
binary it needs:

| Job | Profiles | Browser installed |
|---|---|---|
| `chrome-profiles` | `chrome_latest`, `chrome_128`, `chrome_127` | `chrome` |
| `edge-profile` | `edge_latest` | `msedge` |
| `firefox-profile` | `firefox_latest` | `firefox` |

Each job runs its assigned profiles with `-n auto` (all available CPU cores) and
uploads its raw Allure results as a uniquely named workflow artifact
(`allure-results-<group>-run<N>`). Setting `fail-fast: false` ensures all groups
complete even when one matrix entry has failures.

### Report job

After all matrix jobs complete (or fail), a dedicated `report` job:

1. **Downloads and merges** every `allure-results-*-run<N>` artifact into a
   single directory using `actions/download-artifact@v4` with
   `merge-multiple: true`. Because all result files are UUID-named, there are
   no filename collisions across groups.
2. **Installs the Allure CLI** from the official GitHub release
   (`ALLURE_VERSION: "2.32.2"` in the workflow env).
3. **Generates** a combined HTML report with `allure generate … --clean`.
4. **Uploads** the HTML report as `allure-report-run<N>` (30-day retention).
5. **Deploys** the report to the `gh-pages` branch via
   `peaceiris/actions-gh-pages@v4` — **only on pushes to `main`**, never on
   pull requests, preventing draft work from overwriting the live report.

---

## Viewing Live Reports (GitHub Pages)

### One-time setup

1. Navigate to **Repository → Settings → Pages**.
2. Under **Source**, select **Deploy from a branch**.
3. Set the branch to **`gh-pages`** and the folder to **`/ (root)`**.
4. Click **Save**.

GitHub Pages will be enabled at:

```
https://<org-or-username>.github.io/<repository-name>/
```

### How it works

Every successful push to `main` runs the CI pipeline end-to-end. The `report`
job generates a fresh Allure HTML report and force-pushes it to the `gh-pages`
branch (overwriting the previous report). GitHub Pages serves whatever is at the
root of that branch, so the URL above always reflects the most recent run.

Pull requests do **not** trigger a Pages deployment — they upload the report as
a downloadable workflow artifact instead. This keeps the live URL stable and
only updated by reviewed, merged code.

### Downloading a specific run's report

Every test run produces a downloadable artifact regardless of whether it was a
push or a PR:

1. Go to **Actions** → select the workflow run.
2. Under **Artifacts**, download `allure-report-run<N>`.
3. Unzip and open `index.html` in a browser, or serve it locally:

```bash
unzip allure-report-run42.zip -d allure-report-run42
allure open allure-report-run42
```

---

## Allure Report Architecture

### Timestamped results directories

`conftest.py` creates a unique subdirectory for every run to prevent results
from different runs from overwriting each other:

```
allure-results/
├── run_20260316_090000/    ← run 1 (raw JSON + attachments)
├── run_20260316_143000/    ← run 2
├── run_20260316_153000/    ← run 3 (most recent)
└── latest -> run_20260316_153000   ← symlink, always points to latest
```

### xdist worker isolation

With `-n auto`, pytest-xdist spawns one worker process per CPU core
(`gw0`, `gw1`, …). Without intervention each worker would write its Allure
results into its own subdirectory (`allure-results/run_.../gw0/`), producing
separate mini-reports instead of one unified report.

The `conftest.py` solution uses two complementary mechanisms:

| Mechanism | How it works |
|---|---|
| `@pytest.hookimpl(tryfirst=True)` on `pytest_configure` | Guarantees our hook patches `config.option.allure_report_dir` with the shared timestamped path **before** `allure-pytest`'s own `pytest_configure` reads it — on both the controller and every worker |
| `pytest_sessionstart` safety net | After all configure hooks settle, iterates `allure_commons.plugin_manager` and hard-sets `AllureFileLogger._report_dir` to the correct path, catching any edge-case ordering race |

The controller stores the shared path in `os.environ["ALLURE_RUN_DIR"]` before
spawning workers; workers inherit this environment variable and use it to set
the same directory, resulting in all workers writing flat JSON files directly
into the single timestamped folder with no `gw0/gw1` subdirectories.

### Trend chart and history

The Allure Trend chart requires `history/*.json` from the previous generated
report to be present in the current results directory before generation.
`conftest.py` handles this automatically:

```
pytest_configure  →  copy allure-report/history/*.json  →  run_NEW/history/
[tests run]
pytest_sessionfinish  →  allure generate run_NEW  →  allure-report/
                      →  allure-report/history/ now ready for the next run
                      →  update allure-results/latest symlink
```

This means the Trend chart is populated from the second run onwards with no
manual steps required.
