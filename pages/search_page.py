import logging
from typing import List

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from pages.base_page import BasePage
from utils.config_loader import cfg, selectors

logger = logging.getLogger(__name__)


class SearchPage(BasePage):
    def __init__(self, page: Page) -> None:
        super().__init__(page)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_and_search(self, query: str, max_price: float) -> None:
        import urllib.parse
        search_url = (
            f"{cfg('base_url')}/sch/i.html"
            f"?_nkw={urllib.parse.quote_plus(query)}"
            f"&_udhi={int(max_price)}"
        )
        logger.info(f"Navigating directly to search URL: {search_url}")
        self.navigate(search_url)
        self.page.wait_for_load_state("domcontentloaded", timeout=cfg("timeouts", "navigation_ms"))

    @staticmethod
    def _parse_price(price_text: str) -> float:
        # 1. מטפלים קודם בטווח מחירים (למשל "10 to 20")
        if " to " in price_text.lower():
            price_text = price_text.lower().split(" to ")[0].strip()

        # 2. משאירים רק תווים שהם ספרות או נקודה עשרונית
        cleaned = ''.join(c for c in price_text if c.isdigit() or c == '.')

        try:
            return float(cleaned)
        except ValueError:
            logger.warning(f"Could not parse price text: '{price_text}'")
            return float("inf")


    def _collect_items_on_page(self, max_price: float, remaining: int) -> List[str]:
        urls: List[str] = []

        # שליפת רשימות הלוקייטורים מה-JSON
        card_selectors = selectors("search_page", "item_cards_primary")

        item_cards = []
        for sel in card_selectors:
            # הגנה מפני קידומת css= שעלולה לגרום לשגיאות
            clean_sel = sel.replace("css=", "") if sel.startswith("css=") else sel
            found_cards = self.page.locator(clean_sel).all()
            if found_cards:
                item_cards = found_cards
                logger.info(f"Found {len(item_cards)} cards using selector: {clean_sel}")
                break

        if not item_cards:
            logger.warning("No item cards found with any primary selector.")
            return []

        for card in item_cards:
            if len(urls) >= remaining:
                break

            try:
                # 1. Extract price and check it is within budget
                price_text = ""
                for price_sel in selectors("search_page", "item_price"):
                    clean_price_sel = price_sel.replace("css=", "")
                    try:
                        price_text = card.locator(clean_price_sel).inner_text(timeout=2000)
                        if price_text:
                            break
                    except Exception:
                        continue

                price = self._parse_price(price_text)
                if price > max_price:
                    continue

                # 2. Extract the item URL — try every configured selector,
                #    CSS first then XPath, stopping at the first valid /itm/ href.
                raw_href = None
                for link_sel in selectors("search_page", "item_link"):
                    clean_link_sel = link_sel.replace("css=", "")
                    try:
                        raw_href = card.locator(clean_link_sel).first.get_attribute(
                            "href", timeout=2000
                        )
                        if raw_href and "/itm/" in raw_href:
                            break
                        raw_href = None
                    except Exception:
                        continue

                if raw_href:
                    # 3. Strip query-string noise and validate
                    clean_url = raw_href.split("?")[0]
                    if "/itm/" in clean_url and "www.ebay.com" in clean_url:
                        item_id = clean_url.split("/")[-1]
                        if len(item_id) >= 10 and clean_url not in urls:
                            urls.append(clean_url)
                            logger.info(f"  + Collected item {len(urls)}/{remaining} at ${price:.2f}")

            except Exception as exc:
                logger.debug(f"Skipping item card due to error: {exc}")

        return urls

    def _has_next_page(self) -> bool:
        return self.is_visible(
            "Next page button",
            *selectors("search_page", "next_page"),
            timeout=cfg("timeouts", "price_filter_ms"),
        )

    def _go_to_next_page(self) -> None:
        self.click("Next page button", *selectors("search_page", "next_page"))
        self.page.wait_for_load_state("domcontentloaded", timeout=cfg("timeouts", "navigation_ms"))
        logger.info("Navigated to next results page.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search_items_by_name_under_price(
        self,
        query: str,
        max_price: float,
        limit: int = 5,
    ) -> List[str]:
        """
        Search eBay for *query*, apply a price filter when available, then
        walk result pages collecting item URLs whose price <= max_price.
        """
        logger.info(
            f"search_items_by_name_under_price | "
            f"query='{query}' | max_price={max_price} | limit={limit}"
        )

        self._open_and_search(query, max_price)

        collected: List[str] = []
        page_number = 1

        while len(collected) < limit:
            logger.info(f"--- Scanning results page {page_number} ---")
            # מחשבים כמה עוד חסר לנו כדי להגיע ל-limit
            needed = limit - len(collected)
            # שולחים את המספר הזה לפונקציית האיסוף
            page_items = self._collect_items_on_page(max_price, needed)

            collected.extend(page_items)
            logger.info(f"Progress: {len(collected)}/{limit} items collected.")

            if len(collected) >= limit:
                break

            if not self._has_next_page():
                logger.info("No further pages — stopping pagination.")
                break

            self._go_to_next_page()
            page_number += 1

        logger.info(f"Search complete. Returning {len(collected)} item URL(s).")
        return collected
