from scraper.config import load_categories, load_locations
from scraper.parsers import extract_sku
from scraper.retailers.walmart import WalmartScraper


def test_walmart_categories_hierarchy():
    categories = {x.id: x for x in load_categories("config/walmart/categories.yaml")}

    oral = categories["cuidado-bucal"]
    assert oral.department == "Belleza y cuidado personal"
    assert oral.name == "Higiene y cuidado personal"
    assert oral.subcategory == "Cuidado bucal"
    assert oral.url.endswith("/browse/cuidado-personal/cuidado-bucal/264479_950014")

    laundry = categories["cuidado-de-la-ropa"]
    assert laundry.department == "Limpieza del hogar y cuidado personal"
    assert laundry.name == "Cuidado de la ropa"
    assert laundry.url.endswith("/browse/cuidado-de-la-ropa/3680083")


def test_walmart_sc_toreo_config():
    locations = {x.id: x for x in load_locations()}
    store = locations["sc-toreo"]
    assert store.store == "SC Toreo"
    assert store.store_id == "2344"
    assert store.postal_code == "11220"


def test_walmart_sku_parser():
    url = "https://www.walmart.com.mx/ip/pasta-dental-colgate/00750954607184"
    assert extract_sku(url) == "00750954607184"


def test_walmart_pagination_url():
    url = "https://www.walmart.com.mx/browse/cuidado-de-la-ropa/3680083?facet=x"
    assert WalmartScraper._paged_url(url, 1).endswith("?facet=x")
    assert "page=2" in WalmartScraper._paged_url(url, 2)


def test_store_context_accepts_header_with_postal_code():
    store = {x.id: x for x in load_locations()}["sc-toreo"]
    text = "Walmart SC TOREO Blvd Manuel Avila Camacho 641 Miguel Hidalgo, CMX 11220"
    assert WalmartScraper._store_context_in_text(text, store)


def test_store_context_accepts_pdp_pickup_store():
    store = {x.id: x for x in load_locations()}["sc-toreo"]
    text = "Pickup sin costo, hoy en SC TOREO. Envío a Miguel Hidalgo."
    assert WalmartScraper._store_context_in_text(text, store)


def test_store_context_rejects_generic_pickup_without_store():
    store = {x.id: x for x in load_locations()}["sc-toreo"]
    text = "Pickup sin costo hoy. Envío, llega hoy."
    assert not WalmartScraper._store_context_in_text(text, store)


def test_store_context_accepts_browser_state_with_store_and_postal():
    store = {x.id: x for x in load_locations()}["sc-toreo"]
    blob = '{"pickupStore":{"id":"2344","name":"SC Toreo"},"postalCode":"11220"}'
    assert WalmartScraper._store_context_in_state_blob(blob, store)


def test_store_context_rejects_state_with_only_postal_code():
    store = {x.id: x for x in load_locations()}["sc-toreo"]
    blob = '{"postalCode":"11220"}'
    assert not WalmartScraper._store_context_in_state_blob(blob, store)


def test_walmart_headed_waits_for_manual_verification(monkeypatch):
    scraper = WalmartScraper(headless=False, manual_verification_timeout_ms=5_000)

    states = iter([True, True, False])
    monkeypatch.setattr(scraper, "_is_blocked", lambda page, status=None: next(states))
    monkeypatch.setattr(scraper, "_save_diagnostics", lambda page, prefix: None)

    class FakePage:
        def wait_for_timeout(self, ms):
            return None

    scraper._assert_not_blocked(FakePage(), 403)

    assert scraper.run_meta["manual_verification_required"] is True
    assert scraper.run_meta["manual_verification_resolved"] is True


def test_walmart_headless_does_not_wait_for_manual_verification(monkeypatch):
    import pytest
    from scraper.retailers.walmart import WalmartBlocked

    scraper = WalmartScraper(headless=True)
    monkeypatch.setattr(scraper, "_is_blocked", lambda page, status=None: True)
    monkeypatch.setattr(scraper, "_save_diagnostics", lambda page, prefix: None)

    with pytest.raises(WalmartBlocked):
        scraper._assert_not_blocked(object(), 403)


def test_walmart_store_location_input_rejects_global_search():
    assert not WalmartScraper._is_store_location_input_metadata(
        input_type="search",
        role="searchbox",
        placeholder="Buscar en Walmart",
        aria_label="Buscar",
        name="q",
        element_id="global-search-input",
    )


def test_walmart_store_location_input_accepts_postal_code_field():
    assert WalmartScraper._is_store_location_input_metadata(
        input_type="text",
        role=None,
        placeholder="Ingresa tu código postal",
        aria_label="Código postal",
        name="postalCode",
        element_id="postal-code-input",
    )


def test_walmart_store_location_input_rejects_store_word_in_product_search():
    assert not WalmartScraper._is_store_location_input_metadata(
        input_type="text",
        role=None,
        placeholder="Buscar productos en la tienda",
        aria_label="Buscar productos",
        name="search",
        element_id="search-input",
    )
