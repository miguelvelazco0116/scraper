from scraper.config import Location
from scraper.retailers.chedraui import (
    ChedrauiScraper,
    STORE_CONTEXT_CONTRACT_VERSION,
)


def _location() -> Location:
    return Location(
        id="chedraui-cancun-kabah",
        city="Cancún",
        state="Quintana Roo",
        country="México",
        postal_code="77501",
        store="Chedraui Cancún Kabah",
        store_id="255",
        directory_area="Cancún",
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


def test_store_contract_is_directory_ui_v3():
    assert STORE_CONTEXT_CONTRACT_VERSION == (
        "chedraui-directory-ui-v3"
    )


def test_preexisting_store_is_ignored_and_directory_ui_is_forced():
    scraper = ChedrauiScraper(require_store_context=False)
    location = _location()
    page = _FakePage()

    calls = []

    def select_ui(*_args):
        calls.append("directory_ui")
        scraper.run_meta.update(
            {
                "location_button_opened": True,
                "pickup_clicked": False,
                "directory_clicked": True,
                "state_selected": True,
                "city_selected": True,
                "store_clicked": True,
                "pickup_confirmed": True,
            }
        )
        return True, "visible_store_control_after_directory_ui"

    scraper._try_select_store_ui = select_ui
    scraper._visible_store_control_text = (
        lambda *_: "Chedraui Cancún Kabah"
    )
    scraper._vtex_session_snapshot = lambda *_: {
        "namespaces": {
            "checkout": {"regionId": {"value": "selected"}}
        }
    }

    verified, method = scraper._establish_store_before_category(
        page,
        location,
    )

    assert verified is True
    assert method == "visible_store_control_after_directory_ui"
    assert calls == ["directory_ui"]
    assert scraper.run_meta["preexisting_store_context_ignored"] is True
    assert scraper.run_meta["vtex_region_mutation_skipped"] is True
    assert scraper.run_meta["store_selection_strategy"] == (
        "directory_ui_state_city_store"
    )
    assert scraper.run_meta["directory_ui_store_verified"] is True


def test_directory_ui_requires_every_state_city_store_step():
    scraper = ChedrauiScraper(require_store_context=False)
    location = _location()
    page = _FakePage()

    def select_ui(*_args):
        scraper.run_meta.update(
            {
                "location_button_opened": True,
                "pickup_clicked": True,
                "directory_clicked": True,
                "state_selected": True,
                "city_selected": False,
                "store_clicked": True,
                "pickup_confirmed": True,
            }
        )
        return True, "visible_store_control_after_directory_ui"

    scraper._try_select_store_ui = select_ui
    scraper._visible_store_control_text = (
        lambda *_: "Chedraui Cancún Kabah"
    )
    scraper._save_diagnostics = lambda *_: None

    verified, method = scraper._establish_store_before_category(
        page,
        location,
    )

    assert verified is False
    assert method is None
    assert scraper.run_meta["directory_ui_store_verified"] is False
    assert scraper.run_meta["directory_selection_failure"] == (
        "incomplete_directory_ui_steps"
    )


def test_directory_ui_requires_visible_exact_store_after_confirmation():
    scraper = ChedrauiScraper(require_store_context=False)
    location = _location()
    page = _FakePage()

    def select_ui(*_args):
        scraper.run_meta.update(
            {
                "location_button_opened": True,
                "pickup_clicked": True,
                "directory_clicked": True,
                "state_selected": True,
                "city_selected": True,
                "store_clicked": True,
                "pickup_confirmed": True,
            }
        )
        return True, "browser_state_after_directory_ui"

    scraper._try_select_store_ui = select_ui
    scraper._visible_store_control_text = (
        lambda *_: "Chedraui Cancún Centro"
    )
    scraper._save_diagnostics = lambda *_: None

    verified, method = scraper._establish_store_before_category(
        page,
        location,
    )

    assert verified is False
    assert method is None
    assert scraper.run_meta["directory_ui_store_verified"] is False
    assert scraper.run_meta["directory_selection_failure"] == (
        "visible_store_control_mismatch"
    )


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



def test_direct_directory_entry_does_not_require_pickup_preclick():
    scraper = ChedrauiScraper(require_store_context=False)
    location = _location()
    page = _FakePage()

    def select_ui(*_args):
        scraper.run_meta.update(
            {
                "location_button_opened": True,
                "pickup_clicked": False,
                "directory_clicked": True,
                "state_selected": True,
                "city_selected": True,
                "store_clicked": True,
                "pickup_confirmed": True,
            }
        )
        return True, "visible_store_control_after_directory_ui"

    scraper._try_select_store_ui = select_ui
    scraper._visible_store_control_text = (
        lambda *_: "Chedraui Cancún Kabah"
    )
    scraper._vtex_session_snapshot = lambda *_: {}

    verified, _ = scraper._establish_store_before_category(
        page,
        location,
    )

    assert verified is True
    assert "pickup_clicked" not in scraper.run_meta["directory_ui_steps"]


def test_store_card_matching_supports_split_name_nodes():
    from pathlib import Path

    source = Path(
        "scraper/retailers/chedraui_polanco.py"
    ).read_text(encoding="utf-8")

    method = source.split(
        "def _click_configured_store", 1
    )[1].split(
        "def _try_select_store_directory_generic", 1
    )[0]

    assert "ancestor_card" in method
    assert "all(token in text for token in tokens)" in method
    assert "no_matching_store_card" in method



def test_exact_pickup_value_is_derived_from_store_id():
    location = _location()
    scraper = ChedrauiScraper(require_store_context=False)

    assert scraper._expected_pickup_value(location) == (
        "chedrauimx0255_PP0255"
    )


def test_pickup_value_padding_is_generic_for_other_store_ids():
    from scraper.config import Location

    scraper = ChedrauiScraper(require_store_context=False)
    location = Location(
        id="chedraui-example",
        city="Puebla",
        state="Puebla",
        country="México",
        postal_code="72490",
        store="Chedraui Puebla Centro Sur",
        store_id="76",
    )

    assert scraper._expected_pickup_value(location) == (
        "chedrauimx0076_PP0076"
    )
