from scraper.config import Location
from scraper.retailers.chedraui import ChedrauiScraper


def _location() -> Location:
    return Location(
        id="chedraui-cancun-kabah",
        city="Cancún",
        state="Quintana Roo",
        country="México",
        postal_code="77501",
        store="Chedraui Cancún Kabah",
        store_id="255",
        enabled=True,
    )


class _FakePage:
    def wait_for_timeout(self, _ms):
        return None


def test_visible_store_control_matches_exact_configured_store():
    scraper = ChedrauiScraper(require_store_context=False)
    location = _location()

    assert scraper._store_control_matches_location(
        "Recoger en Chedraui Cancún Kabah",
        location,
    )
    assert not scraper._store_control_matches_location(
        "Chedraui Cancún Centro",
        location,
    )


def test_exact_preselected_store_skips_postal_region_mutation():
    scraper = ChedrauiScraper(require_store_context=False)
    location = _location()
    page = _FakePage()

    calls = []
    scraper._verify_store_context = lambda *_: (
        True,
        "visible_store_control",
    )
    scraper._prepare_structured_store_context = (
        lambda *_: calls.append("regionalize") or (True, "vtex_session")
    )
    scraper._try_select_store_ui = (
        lambda *_: calls.append("ui") or (True, "ui")
    )
    scraper._vtex_session_snapshot = lambda *_: {
        "namespaces": {"checkout": {"regionId": {"value": "exact"}}}
    }

    verified, method = scraper._establish_store_before_category(
        page,
        location,
    )

    assert verified is True
    assert method == "visible_store_control"
    assert calls == []
    assert scraper.run_meta["vtex_region_mutation_skipped"] is True
    assert "vtex_session_before_category" in scraper.run_meta


def test_missing_exact_store_uses_region_only_for_discovery_then_ui():
    scraper = ChedrauiScraper(require_store_context=False)
    location = _location()
    page = _FakePage()

    calls = []
    scraper._verify_store_context = lambda *_: (False, None)
    scraper._prepare_structured_store_context = (
        lambda *_: calls.append("regionalize")
        or (True, "vtex_session")
    )
    scraper._try_select_store_ui = (
        lambda *_: calls.append("ui")
        or (True, "visible_store_control_after_ui")
    )
    scraper._vtex_session_snapshot = lambda *_: {
        "namespaces": {"checkout": {"regionId": {"value": "selected"}}}
    }

    verified, method = scraper._establish_store_before_category(
        page,
        location,
    )

    assert verified is True
    assert method == "visible_store_control_after_ui"
    assert calls == ["regionalize", "ui"]
    assert scraper.run_meta["vtex_region_mutation_skipped"] is False
    assert "vtex_session_after_store_selection" in scraper.run_meta


def test_whole_page_text_is_diagnostic_not_store_certification():
    scraper = ChedrauiScraper(require_store_context=False)
    location = _location()
    page = _FakePage()

    scraper._visible_store_control_text = lambda *_: ""
    scraper._browser_state_blob = lambda *_: ""
    scraper._orderform_snapshot = lambda *_: None
    scraper._body_text = lambda *_: (
        "Directorio de tiendas. Chedraui Cancún Kabah. "
        "Recoger en tienda."
    )

    verified, method = scraper._verify_store_context(
        page,
        location,
    )

    assert verified is False
    assert method is None
    assert scraper.run_meta["page_text_store_hint"] is True
