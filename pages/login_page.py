import logging

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from pages.base_page import BasePage
from utils.config_loader import cfg, selectors, selector

logger = logging.getLogger(__name__)


class LoginPage(BasePage):
    def __init__(self, page: Page) -> None:
        super().__init__(page)

    # ------------------------------------------------------------------
    # Step 1 – Enter email / username
    # ------------------------------------------------------------------

    def _enter_username(self, username: str) -> None:
        self.fill("Username / email field", username, *selectors("login_page", "username_field"))
        logger.info("Username entered.")

    # ------------------------------------------------------------------
    # Step 2 – Click "Continue"
    # ------------------------------------------------------------------

    def _click_continue(self) -> None:
        self.click("Continue button", *selectors("login_page", "continue_button"))
        logger.info("Continue button clicked — waiting for password field.")
        try:
            self.page.wait_for_selector(
                selector("login_page", "password_field_wait"),
                state="visible",
                timeout=cfg("timeouts", "password_wait_ms"),
            )
        except PlaywrightTimeoutError:
            screenshot = self._take_screenshot("continue_transition_timeout")
            raise RuntimeError(
                f"Password field did not appear after clicking Continue. "
                f"Screenshot: {screenshot}"
            )

    # ------------------------------------------------------------------
    # Step 3 – Enter password
    # ------------------------------------------------------------------

    def _enter_password(self, password: str) -> None:
        self.fill("Password field", password, *selectors("login_page", "password_field"))
        logger.info("Password entered.")

    # ------------------------------------------------------------------
    # Step 4 – Submit login form
    # ------------------------------------------------------------------

    def _submit_login(self) -> None:
        self.click("Sign in button", *selectors("login_page", "sign_in_button"))
        logger.info("Sign-in button clicked.")

    # ------------------------------------------------------------------
    # Post-login verification
    # ------------------------------------------------------------------

    def _verify_login_success(self) -> None:
        try:
            self.page.wait_for_url(
                lambda url: "signin.ebay.com" not in url,
                timeout=cfg("timeouts", "navigation_ms"),
            )
            logger.info(f"Login successful — current URL: {self.page.url}")
        except PlaywrightTimeoutError:
            screenshot = self._take_screenshot("login_failed")
            raise RuntimeError(
                f"Login appears to have failed — still on sign-in page. "
                f"Screenshot: {screenshot}"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def login(self, username: str, password: str) -> None:
        """
        Perform eBay's multi-step login flow:
          1. Navigate to the sign-in page.
          2. Enter the email / username and click Continue.
          3. Enter the password and submit.
          4. Verify a successful redirect away from the sign-in domain.

        Args:
            username: Registered eBay email address or username.
            password: Account password (read from environment — never hard-coded).

        Raises:
            RuntimeError: If any step's locators are exhausted or the
                          post-login redirect does not occur.
        """
        logger.info(f"Starting eBay login for user: '{username}'")
        self.navigate(cfg("login_url"))
        self._enter_username(username)
        self._click_continue()
        self._enter_password(password)
        self._submit_login()
        self._verify_login_success()
        logger.info("Login flow completed successfully.")
