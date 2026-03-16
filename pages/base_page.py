import logging
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import allure
from playwright.sync_api import Page, Locator, TimeoutError as PlaywrightTimeoutError

from utils.config_loader import cfg

logger = logging.getLogger(__name__)

SCREENSHOTS_DIR = Path("screenshots")
SCREENSHOTS_DIR.mkdir(exist_ok=True)

# Keywords that indicate an error / unavailable page
_ERROR_URL_SIGNALS = ("errorpage", "notfound", "error404", "gone")
_ERROR_TITLE_SIGNALS = ("page not found", "404", "item not found", "not available", "gone", "no longer available")
_ERROR_DOM_SELECTORS = [
    "#errorpage-title",
    "[class*='errorPage']",
    "[class*='notFound']",
    "xpath=//h1[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'not found')]",
    "xpath=//h1[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'no longer available')]",
]

# Keywords that indicate eBay's human-validation / CAPTCHA page
_VALIDATION_URL_SIGNALS = ("veri", "captcha", "challenge", "security", "validate")
_VALIDATION_TITLE_SIGNALS = (
    "verify", "human", "robot", "security check", "are you", "captcha",
)
_VALIDATION_PAUSE_TIMEOUT = 120  # seconds to wait for manual resolution


class BasePage:
    def __init__(self, page: Page) -> None:
        self.page = page

    # ------------------------------------------------------------------
    # Human-validation detection & pause
    # ------------------------------------------------------------------

    def _is_validation_page(self) -> bool:
        """Return True if the current page looks like a bot-check page."""
        url = self.page.url.lower()
        if any(signal in url for signal in _VALIDATION_URL_SIGNALS):
            return True

        try:
            title = self.page.title().lower()
            if any(signal in title for signal in _VALIDATION_TITLE_SIGNALS):
                return True
        except Exception:
            pass

        # Check for common CAPTCHA/challenge DOM markers
        challenge_selectors = [
            "#captcha_form",
            "[id*='captcha']",
            "[class*='captcha']",
            "iframe[src*='captcha']",
            "iframe[src*='recaptcha']",
            "[data-testid='vh-notice']",          # eBay's "Verify you're human"
            "xpath=//h1[contains(translate(.,"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
            "'abcdefghijklmnopqrstuvwxyz'),'verify')]",
            "xpath=//h1[contains(translate(.,"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
            "'abcdefghijklmnopqrstuvwxyz'),'human')]",
        ]
        for sel in challenge_selectors:
            try:
                if self.page.locator(sel).is_visible():
                    return True
            except Exception:
                pass

        return False

    def _handle_validation_page(self) -> None:
        """
        Detected a human-validation page.

        - CI / headless: fail immediately with a clear error so the pipeline
          doesn't hang.  Fix: run utils/save_auth_state.py locally, store
          data/auth_state.json as a CI secret, and set AUTH_STATE_PATH.

        - Local / headed: pause and poll until the user solves the challenge
          manually in the open browser window, or the timeout expires.
        """
        import os
        screenshot = self._take_screenshot("human_validation_detected")

        is_ci = os.environ.get("CI", "false").lower() == "true"
        if is_ci:
            raise RuntimeError(
                "Human validation page detected in CI — cannot proceed headlessly.\n"
                "Fix: generate data/auth_state.json with utils/save_auth_state.py,\n"
                "     store it as a CI secret, and set the AUTH_STATE_PATH env var.\n"
                f"Screenshot: {screenshot}"
            )

        logger.warning(
            "\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "  HUMAN VALIDATION PAGE DETECTED\n"
            f"  URL      : {self.page.url}\n"
            f"  Screenshot: {screenshot}\n"
            "  → Please solve the challenge in the browser window.\n"
            f"  → Waiting up to {_VALIDATION_PAUSE_TIMEOUT}s for resolution…\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        deadline = time.time() + _VALIDATION_PAUSE_TIMEOUT
        while time.time() < deadline:
            time.sleep(2)
            if not self._is_validation_page():
                logger.info("Human validation resolved — resuming test.")
                return

        screenshot = self._take_screenshot("human_validation_timeout")
        raise RuntimeError(
            f"Human validation was not resolved within {_VALIDATION_PAUSE_TIMEOUT}s. "
            f"Screenshot: {screenshot}"
        )

    def _check_for_validation(self) -> None:
        """Call after every navigation; blocks until the challenge is solved."""
        if self._is_validation_page():
            self._handle_validation_page()

    # ------------------------------------------------------------------
    # Human-like timing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _random_delay(min_ms: int = 100, max_ms: int = 300) -> None:
        """Sleep for a random duration to mimic human think-time."""
        time.sleep(random.randint(min_ms, max_ms) / 1000)

    def _human_type(self, locator: Locator, text: str) -> None:
        """
        Type text character-by-character with randomised per-keystroke delays
        instead of the instant paste that `fill()` performs.
        """
        locator.click()
        for char in text:
            locator.press(char)
            time.sleep(random.uniform(0.05, 0.18))

    # ------------------------------------------------------------------
    # Smart Locator core
    # ------------------------------------------------------------------

    def _try_locator(self, selector: str, timeout: int = 5000) -> Optional[Locator]:
        """Return the locator if the element is visible, else None.
        Uses is_visible() on .first to avoid strict mode crashes when multiple
        hidden elements match the same selector."""
        try:
            locator = self.page.locator(selector)
            if locator.first.is_visible():
                logger.info(f"Locator succeeded: '{selector}'")
                return locator
            logger.warning(f"Locator not visible: '{selector}'")
            return None
        except Exception:
            logger.warning(f"Locator failed: '{selector}'")
            return None

    def smart_locator(self, element_name: str, *selectors: str, timeout: int = 5000) -> Locator:
        """
        Try each selector in order.  Return the first that resolves to a
        visible element.  Take a screenshot and raise if all selectors fail.
        """
        for index, selector in enumerate(selectors):
            label = "primary" if index == 0 else f"fallback-{index}"
            logger.info(f"[{element_name}] Trying {label} selector: '{selector}'")
            locator = self._try_locator(selector, timeout=timeout)
            if locator is not None:
                logger.info(f"[{element_name}] Resolved with {label} selector: '{selector}'")
                return locator

        screenshot_path = self._take_screenshot(element_name)
        message = (
            f"[{element_name}] All {len(selectors)} locator(s) failed. "
            f"Screenshot saved to: {screenshot_path}"
        )
        logger.error(message)
        raise RuntimeError(message)

    def _take_screenshot(self, element_name: str) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = element_name.replace(" ", "_").lower()
        path = SCREENSHOTS_DIR / f"{safe_name}_{timestamp}.png"
        png_bytes = self.page.screenshot(path=str(path), full_page=True)
        allure.attach(
            png_bytes or path.read_bytes(),
            name=safe_name,
            attachment_type=allure.attachment_type.PNG,
        )
        logger.error(f"Screenshot saved: {path}")
        return path

    # ------------------------------------------------------------------
    # Common page interactions built on smart_locator
    # ------------------------------------------------------------------

    def navigate(self, url: str) -> None:
        with allure.step(f"Navigate to {url}"):
            logger.info(f"Navigating to: {url}")
            self._random_delay(100, 300)
            self.page.goto(url, timeout=cfg("timeouts", "navigation_ms"))
            self.page.wait_for_load_state("domcontentloaded", timeout=cfg("timeouts", "navigation_ms"))
            self._check_for_validation()

    def click(self, element_name: str, *selectors: str, timeout: int = 3000, **kwargs) -> None:
        with allure.step(f"Click: {element_name}"):
            self._random_delay(100, 300)
            locator = self.smart_locator(element_name, *selectors, timeout=timeout)
            locator.first.click(**kwargs)
            logger.info(f"[{element_name}] Clicked.")
            self._check_for_validation()

    def fill(self, element_name: str, value: str, *selectors: str, timeout: int = 3000) -> None:
        with allure.step(f"Fill '{element_name}' with '{value}'"):
            locator = self.smart_locator(element_name, *selectors, timeout=timeout)
            self._human_type(locator, value)
            logger.info(f"[{element_name}] Filled with: '{value}'")

    def get_text(self, element_name: str, *selectors: str, timeout: int = 5000) -> str:
        locator = self.smart_locator(element_name, *selectors, timeout=timeout)
        text = locator.inner_text()
        logger.info(f"[{element_name}] Text: '{text}'")
        return text

    def is_visible(self, element_name: str, *selectors: str, timeout: int = 5000) -> bool:
        for index, selector in enumerate(selectors):
            label = "primary" if index == 0 else f"fallback-{index}"
            logger.info(f"[{element_name}] Checking visibility with {label}: '{selector}'")
            locator = self._try_locator(selector, timeout=timeout)
            if locator is not None:
                return True
        logger.warning(f"[{element_name}] Not visible with any selector.")
        return False

    def wait_for_url(self, url_pattern: str, timeout: int = 10000) -> None:
        logger.info(f"Waiting for URL matching: '{url_pattern}'")
        self.page.wait_for_url(url_pattern, timeout=timeout)
