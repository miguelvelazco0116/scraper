"""Offline evidence that unrelated/duplicate store widgets cannot certify pickup."""
from scraper.config import Location
from scraper.retailers.chedraui import ChedrauiScraper as BaseScraper
from scraper.retailers.chedraui_polanco import ChedrauiScraper


def location():
    return Location(
        id="chedraui-cancun-kabah", city="Cancún", state="Quintana Roo",
        country="México", postal_code="77501", store="Chedraui Cancún Kabah",
        store_id="255", directory_area="Cancún",
    )


class Node:
    def __init__(self, value):
        self.value = value

    def is_visible(self):
        return True

    def inner_text(self, **_):
        return self.value

    def get_attribute(self, _):
        return None


class Nodes:
    def __init__(self, nodes):
        self.nodes = nodes

    def count(self):
        return len(self.nodes)

    def nth(self, index):
        return self.nodes[index]


class Page:
    def locator(self, selector):
        if selector == "button.chedrauimx-locator-2-x-labelTextAddress":
            return Nodes([Node("Chedraui Cancún Centro")])
        if selector == '[data-testid*="store" i]':
            return Nodes([Node("Chedraui Cancún Kabah")])
        return Nodes([])


def test_only_real_store_header_is_authoritative():
    scraper = BaseScraper(require_store_context=False)
    current = scraper._visible_store_control_text(Page())
    assert current == "Chedraui Cancún Centro"
    assert scraper._store_control_matches_location(current, location()) is False
    assert scraper.run_meta["visible_store_control_selector"] == (
        "button.chedrauimx-locator-2-x-labelTextAddress"
    )


class RadioPage:
    def locator(self, selector):
        if '[value="chedrauimx0255_PP0255"]' in selector:
            return Nodes([])
        if selector == 'input[name="pickup-point-list"][type="radio"]':
            return Nodes([Node("other_store")])
        return Nodes([])


def test_other_store_radio_blocks_ambiguous_name_fallback():
    scraper = ChedrauiScraper(require_store_context=False)
    assert scraper._click_configured_store(RadioPage(), location()) is False
    assert scraper.run_meta["directory_store_match_method"] == (
        "exact_store_radio_not_verified"
    )
    assert scraper.run_meta["directory_selection_failure"] == (
        "store_radio_value_or_check_mismatch"
    )
