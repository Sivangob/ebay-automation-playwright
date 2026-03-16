"""
One-time helper: log in to eBay manually in a real browser, then save the
authenticated session to data/auth_state.json.

Usage (run once locally, never in CI):
    python utils/save_auth_state.py

The saved file contains cookies and localStorage.  Treat it like a password:
  - Add data/auth_state.json to .gitignore
  - In CI: store it as an encrypted secret and set AUTH_STATE_PATH to its path
"""

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

_PROJECT_ROOT = Path(__file__).parent.parent
AUTH_STATE_PATH = Path(
    os.environ.get("AUTH_STATE_PATH", str(_PROJECT_ROOT / "data" / "auth_state.json"))
)
LOGIN_URL = "https://signin.ebay.co.uk/signin/"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def main() -> None:
    AUTH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = context.new_page()
        Stealth().apply_stealth_sync(page)

        page.goto(LOGIN_URL)
        print("\n" + "=" * 60)
        print("  Browser is open — please log in to eBay manually.")
        print("  The script will save your session once you reach")
        print("  the eBay home page after login.")
        print("=" * 60 + "\n")

        # Wait until the user is redirected away from the sign-in domain
        try:
            page.wait_for_url(
                lambda url: "signin.ebay" not in url,
                timeout=180_000,   # 3 minutes to log in manually
            )
        except Exception:
            print("Timed out waiting for login. Exiting without saving.")
            browser.close()
            sys.exit(1)

        # Wait for the home page to fully load so all session cookies are set
        page.wait_for_load_state("networkidle", timeout=15_000)
        print("Login detected — waiting 3s for all session cookies to settle…")
        page.wait_for_timeout(3000)

        context.storage_state(path=str(AUTH_STATE_PATH))
        print(f"Auth state saved to: {AUTH_STATE_PATH}")
        browser.close()


if __name__ == "__main__":
    main()
