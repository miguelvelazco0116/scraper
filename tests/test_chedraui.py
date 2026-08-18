from scraper.config import Location
from scraper.retailers.chedraui import ChedrauiScraper


POLANCO = Location(
    id="chedraui-polanco",
    city="Miguel Hidalgo",
    state="CDMX",
    country="México",
    postal_code="11500",
    store="Chedraui Selecto México Polanco",
    store_id="232",
)


def test_chedraui_paged_url():
    base = "https://www.chedraui.com.mx/cuidado-e-higiene-personal/higiene-bucal"
    assert ChedrauiScraper._paged_url(base, 1) == base
    assert ChedrauiScraper._paged_url(base, 2).endswith("?page=2")


def test_chedraui_store_context_from_page_text():
    text = "Recoger en Chedraui Selecto México Polanco 11500"
    assert ChedrauiScraper._store_context_in_text(text, POLANCO)


def test_chedraui_store_context_from_browser_state():
    blob = '{"storeName":"Selecto Polanco","storeId":"232"}'
    assert ChedrauiScraper._store_context_in_state_blob(blob, POLANCO)


def test_chedraui_rejects_unrelated_store():
    text = "Recoger en Chedraui Selecto México Santa Fe 01219"
    assert not ChedrauiScraper._store_context_in_text(text, POLANCO)
