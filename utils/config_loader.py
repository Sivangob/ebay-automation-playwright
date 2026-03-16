import json
import os
from pathlib import Path
from typing import Any, Dict, List

_DATA_DIR = Path(__file__).parent.parent / "data"


def _load_json(filename: str) -> Dict[str, Any]:
    path = _DATA_DIR / filename
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ── Cached singletons ──────────────────────────────────────────────────────────

_locators: Dict[str, Any] = {}
_config: Dict[str, Any] = {}


def get_locators() -> Dict[str, Any]:
    global _locators
    if not _locators:
        _locators = _load_json("locators.json")
    return _locators


def get_config() -> Dict[str, Any]:
    global _config
    if not _config:
        _config = _load_json("test_config.json")
    return _config


# ── Typed helpers ──────────────────────────────────────────────────────────────

def locators(page_key: str) -> Dict[str, Any]:
    """Return the locator block for a given page key, e.g. 'search_page'."""
    return get_locators()[page_key]


def selectors(page_key: str, element_key: str) -> List[str]:
    """
    Return the list of selector strings for an element.
    Handles both plain-string and list values in locators.json.
    """
    value = locators(page_key)[element_key]
    if isinstance(value, list):
        return value
    return [value]


def selector(page_key: str, element_key: str) -> str:
    """Return the single (XPath / CSS) selector string for an element."""
    return locators(page_key)[element_key]


def cfg(*keys: str) -> Any:
    """
    Drill into test_config.json with dot-path keys.
    e.g. cfg('search', 'max_price')  →  50.0
    """
    node: Any = get_config()
    for key in keys:
        node = node[key]
    return node


def get_credentials() -> Dict[str, str]:
    """
    Read eBay credentials exclusively from environment variables.
    Set EBAY_USERNAME and EBAY_PASSWORD before running the tests.

    Raises:
        EnvironmentError: if either variable is not set.
    """
    username = os.environ.get("EBAY_USERNAME")
    password = os.environ.get("EBAY_PASSWORD")

    if not username:
        raise EnvironmentError(
            "EBAY_USERNAME environment variable is not set. "
            "Export it before running the tests:\n"
            "  export EBAY_USERNAME='your_ebay_email@example.com'"
        )
    if not password:
        raise EnvironmentError(
            "EBAY_PASSWORD environment variable is not set. "
            "Export it before running the tests:\n"
            "  export EBAY_PASSWORD='your_password'"
        )

    return {"username": username, "password": password}
