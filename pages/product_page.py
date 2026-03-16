import logging
import random
from typing import Any, Dict, List

import allure
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from pages.base_page import BasePage
from utils.config_loader import cfg, locators, selectors

logger = logging.getLogger(__name__)


class ProductPage(BasePage):
    def __init__(self, page: Page) -> None:
        super().__init__(page)
        self._locs: Dict[str, Any] = locators("product_page")

    # ------------------------------------------------------------------
    # Price parsing (shared by cart-total reading and any future need)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_price(text: str) -> float:
        """Extract the first numeric value from a price string."""
        if not text:
            return 0.0
        cleaned = text.lower()
        if " to " in cleaned:
            cleaned = cleaned.split(" to ")[0]
        cleaned = "".join(c for c in cleaned if c.isdigit() or c == ".")
        try:
            return float(cleaned) if cleaned else 0.0
        except ValueError:
            return 0.0

    # ------------------------------------------------------------------
    # Variant selection
    # ------------------------------------------------------------------

    def _select_first_available_variant(self) -> None:
        """Select a random available variant (size / color / style) if the
        product requires one before "Add to cart" becomes active."""

        # ── Listbox-button (eBay's MSKU UI) ───────────────────────────
        listbox_containers: list = []
        for sel in selectors("product_page", "listbox_variants"):
            listbox_containers = [
                loc for loc in self.page.locator(sel).all() if loc.is_visible()
            ]
            if listbox_containers:
                logger.info(f"Found {len(listbox_containers)} visible listbox variant(s)")
                break

        if listbox_containers:
            for container in listbox_containers:
                try:
                    container_text = container.inner_text().lower()
                    parent_loc = container.locator(
                        "xpath=./ancestor::div[contains(@class, 'section')]"
                    ).first
                    parent_text = (
                        parent_loc.inner_text().lower()
                        if parent_loc.count() > 0
                        else ""
                    )
                    combined_text = container_text + " " + parent_text

                    if not any(
                        k in combined_text
                        for k in ["size", "color", "colour", "select", "style", "format"]
                    ):
                        continue

                    # Click the listbox trigger to open the dropdown
                    trigger_clicked = False
                    for trigger_sel in selectors("product_page", "listbox_trigger"):
                        trigger = container.locator(trigger_sel).first
                        if trigger.count() > 0 and trigger.is_visible():
                            current_val = trigger.inner_text()
                            if not any(x in current_val for x in ["Select", "-", "בחרו"]):
                                trigger_clicked = True  # already selected
                                break
                            trigger.click(timeout=3000)
                            logger.info(f"Clicked listbox trigger: '{trigger_sel}'")
                            trigger_clicked = True
                            break

                    if not trigger_clicked:
                        continue

                    # Collect all available options and pick one at random
                    available_options = []
                    for option_sel in selectors("product_page", "listbox_first_option"):
                        candidates = (
                            self.page.locator(option_sel)
                            .filter(has_not_text="-")
                            .filter(has_not_text="Select")
                            .all()
                        )
                        for opt in candidates:
                            if opt.is_visible():
                                available_options.append(opt)
                        if available_options:
                            break

                    if available_options:
                        chosen = random.choice(available_options)
                        name = (
                            chosen.get_attribute("data-sku-value-name")
                            or chosen.inner_text()
                        )
                        chosen.click(timeout=3000)
                        logger.info(f"Randomly selected variant: '{name}'")
                        # Close the dropdown so it doesn't block "Add to cart"
                        self.page.mouse.click(0, 0)
                        self.page.wait_for_timeout(500)

                except Exception as exc:
                    logger.warning(f"Skipping a listbox variant due to error: {exc}")
            return

    # ------------------------------------------------------------------
    # Pop-up / overlay dismissal
    # ------------------------------------------------------------------

    def _dismiss_popups(self) -> None:
        for popup in self._locs["popups"]:
            name: str = popup["name"]
            popup_selectors: List[str] = popup["selectors"]
            if self.is_visible(name, *popup_selectors,
                               timeout=cfg("timeouts", "popup_ms")):
                try:
                    self.click(name, *popup_selectors,
                               timeout=cfg("timeouts", "default_ms"))
                    logger.info(f"Dismissed overlay: '{name}'")
                except RuntimeError:
                    logger.warning(f"Could not dismiss overlay: '{name}'")

    # ------------------------------------------------------------------
    # Cart reading
    # ------------------------------------------------------------------

    def _read_cart_total(self) -> float:
        """Return the cart subtotal by trying every configured price selector."""
        all_selectors = (
            selectors("cart_page", "total_price_primary")
            + selectors("cart_page", "total_price_fallback")
        )
        for sel in all_selectors:
            locator = self.page.locator(sel).first
            try:
                locator.wait_for(state="visible", timeout=cfg("timeouts", "cart_total_ms"))
                raw = locator.inner_text()
                price = self._parse_price(raw)
                if price > 0:
                    logger.info(f"Cart total via '{sel}': ${price:.2f}")
                    return price
            except Exception:
                continue
        logger.error("Could not read cart total with any selector.")
        return 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def clear_cart(self, cart_url: str) -> None:
        """Navigate to the cart and remove all items before starting a new order."""
        logger.info("Clearing cart before starting order...")
        self.navigate(cart_url)
        self.page.wait_for_load_state(
            "domcontentloaded", timeout=cfg("timeouts", "navigation_ms")
        )

        remove_selectors = selectors("cart_page", "remove_item_button")

        while True:
            removed_any = False
            for sel in remove_selectors:
                try:
                    buttons = self.page.locator(sel).all()
                    visible_buttons = [b for b in buttons if b.is_visible()]
                    if not visible_buttons:
                        continue
                    btn = visible_buttons[0]
                    btn.click()
                    logger.info(f"Removed a cart item using selector: '{sel}'")
                    # eBay removes items via AJAX — wait for the element to detach
                    # rather than waiting for a page-load event.
                    try:
                        btn.wait_for(state="detached", timeout=cfg("timeouts", "navigation_ms"))
                    except PlaywrightTimeoutError:
                        self.page.wait_for_load_state(
                            "domcontentloaded", timeout=cfg("timeouts", "navigation_ms")
                        )
                    removed_any = True
                    break
                except Exception as exc:
                    logger.warning(f"Remove selector '{sel}' failed: {exc}")
                    continue

            if not removed_any:
                break

        logger.info("Cart cleared.")

    def add_items_to_cart(self, item_urls: List[str]) -> None:
        """Navigate to each product URL, select a random variant when required,
        click 'Add to cart', and attach a screenshot for every successfully
        added item."""
        add_to_cart_selectors = selectors("product_page", "add_to_cart")

        for index, url in enumerate(item_urls, start=1):
            logger.info(f"[{index}/{len(item_urls)}] Processing: {url[:80]}...")
            try:
                self.navigate(url)
                self._select_first_available_variant()

                self.click(
                    "Add to cart button",
                    *add_to_cart_selectors,
                    timeout=cfg("timeouts", "default_ms"),
                    force=True,
                )
                logger.info(f"  'Add to cart' clicked for item {index}.")

                try:
                    self.page.wait_for_load_state("domcontentloaded", timeout=5000)
                except PlaywrightTimeoutError:
                    pass

                self._dismiss_popups()

                # Screenshot of the page after every successful cart addition
                png = self.page.screenshot(full_page=True)
                allure.attach(
                    png,
                    name=f"item_{index}_added_to_cart",
                    attachment_type=allure.attachment_type.PNG,
                )
                logger.info(f"  Item {index} successfully added to cart.")

            except RuntimeError as exc:
                logger.error(
                    f"  Failed to add item {index} to cart — "
                    f"all locators exhausted. Detail: {exc}"
                )
            except Exception as exc:
                screenshot_path = self._take_screenshot(f"unexpected_error_item_{index}")
                logger.error(
                    f"  Unexpected error on item {index}: {exc}. "
                    f"Screenshot: {screenshot_path}"
                )

    def assert_cart_total_not_exceeds(
        self, budget_per_item: float, items_count: int
    ) -> None:
        """Navigate to the cart, take a full-page screenshot, extract the order
        total, and assert it does not exceed budget_per_item * items_count.

        Matches the assignment requirement:
            assertCartTotalNotExceeds(budgetPerItem, itemsCount)
        """
        cart_url = cfg("cart_url")
        self.navigate(cart_url)
        self.page.wait_for_load_state(
            "domcontentloaded", timeout=cfg("timeouts", "navigation_ms")
        )

        # Required cart-page screenshot
        png = self.page.screenshot(full_page=True)
        allure.attach(
            png,
            name="cart_page_screenshot",
            attachment_type=allure.attachment_type.PNG,
        )

        total = self._read_cart_total()
        expected_max = budget_per_item * items_count

        assert total > 0, (
            "Cart total is $0.00 — could not read the subtotal or the cart is empty."
        )
        assert total <= expected_max, (
            f"Cart total ${total:.2f} exceeds the allowed budget "
            f"${expected_max:.2f} "
            f"({items_count} item(s) × ${budget_per_item:.2f} each)."
        )
        logger.info(
            f"assertCartTotalNotExceeds passed: ${total:.2f} ≤ ${expected_max:.2f}"
        )
