import logging
from typing import List

import allure
import pytest
from playwright.sync_api import Page

from pages.login_page import LoginPage
from pages.search_page import SearchPage
from pages.product_page import ProductPage
from utils.config_loader import cfg, get_credentials

logger = logging.getLogger(__name__)


# ── Test ───────────────────────────────────────────────────────────────────────

@allure.feature("eBay Shopping Flow")
class TestEbayFlow:

    @allure.story("Search, Add to Cart, and Verify")
    def test_search_add_to_cart_and_verify(self, page: Page) -> None:
        """
        End-to-end flow:
          1. Log in to eBay using credentials from environment variables.
          2. Empty the cart.
          3. Search for the configured query under the configured max price
             (up to 5 items, using the site's price filter and XPath).
          4. Add every collected item to the cart (screenshot per item).
          5. Assert the cart total does not exceed max_price × item count
             (navigates to cart, screenshots it, and asserts).
        """
        # ── Load config ────────────────────────────────────────────────
        query: str       = cfg("search", "query")
        max_price: float = cfg("search", "max_price")
        item_limit: int  = cfg("search", "item_limit")
        cart_url: str    = cfg("cart_url")
        credentials      = get_credentials()

        # ── Step 1: Login ──────────────────────────────────────────────
        with allure.step("Login to eBay"):
            login_page = LoginPage(page)
            login_page.login(
                username=credentials["username"],
                password=credentials["password"],
            )

        # ── Step 2: Empty cart ─────────────────────────────────────────
        with allure.step("Empty cart before starting order"):
            product_page = ProductPage(page)
            product_page.clear_cart(cart_url)

        # ── Step 3: Search and collect URLs ───────────────────────────
        with allure.step(f"Search for '{query}' under ${max_price} (limit {item_limit})"):
            search_page = SearchPage(page)
            logger.info(
                f"Searching for '{query}' | max_price=${max_price} | limit={item_limit}"
            )
            item_urls: List[str] = search_page.search_items_by_name_under_price(
                query=query,
                max_price=max_price,
                limit=item_limit,
            )
            assert item_urls, (
                f"No items found for query='{query}' under ${max_price}. "
                "Check network connectivity or adjust max_price in test_config.json."
            )
            logger.info(f"Collected {len(item_urls)} URL(s).")

        # ── Step 4: Add all collected items to the cart ───────────────
        with allure.step(f"Add {len(item_urls)} item(s) to cart"):
            product_page.add_items_to_cart(item_urls)

        # ── Step 5: Assert cart total does not exceed budget ──────────
        with allure.step(
            f"assertCartTotalNotExceeds(${max_price:.2f}, {len(item_urls)} items)"
        ):
            product_page.assert_cart_total_not_exceeds(max_price, len(item_urls))
