from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scraper.retailers.chedraui_polanco import ChedrauiScraper


class _Radio:
    def __init__(self, page):
        self.page = page
    def is_checked(self):
        return self.page.selected


class _Button:
    def __init__(self, page):
        self.page = page
    def is_visible(self):
        return True
    def is_enabled(self):
        return self.page.ticks >= self.page.enable_at
    def click(self, timeout=None):
        self.page.clicks += 1


class _Nodes:
    def __init__(self, page, kind, matched=True):
        self.page = page
        self.kind = kind
        self.matched = matched
    def count(self):
        return 1 if self.matched else 0
    def nth(self, index):
        return _Radio(self.page) if self.kind == "radio" else _Button(self.page)


class _Page:
    def __init__(self, selected=True, enable_at=3):
        self.selected = selected
        self.enable_at = enable_at
        self.ticks = 0
        self.clicks = 0
    def locator(self, selector):
        return _Nodes(self, "radio", "chedrauimx0173_PP0173" in selector)
    def get_by_role(self, role, name=None):
        assert role == "button"
        assert name.search("Recoger en esta tienda")
        return _Nodes(self, "button")
    def get_by_text(self, text, exact=None):
        return _Nodes(self, "button", matched=False)
    def wait_for_timeout(self, ms):
        self.ticks += 1


class PickupConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.scraper = SimpleNamespace(
            run_meta={},
            _expected_pickup_value=lambda location: "chedrauimx0173_PP0173",
        )
        self.location = SimpleNamespace(store_id="173")

    def confirm(self, page):
        return ChedrauiScraper._click_enabled_pickup_button(
            self.scraper, page, self.location
        )

    def test_waits_for_button_to_be_enabled(self):
        page = _Page(selected=True, enable_at=3)
        self.assertTrue(self.confirm(page))
        self.assertEqual(page.clicks, 1)
        self.assertEqual(page.ticks, 3)
        self.assertEqual(
            self.scraper.run_meta["pickup_confirmation_method"],
            "exact_visible_button_role",
        )

    def test_never_confirms_wrong_or_unselected_radio(self):
        page = _Page(selected=False, enable_at=0)
        self.assertFalse(self.confirm(page))
        self.assertEqual(page.clicks, 0)
        self.assertEqual(
            len(self.scraper.run_meta["pickup_confirmation_observations"]),
            12,
        )

    def test_disabled_button_never_counts_as_confirmed(self):
        page = _Page(selected=True, enable_at=99)
        self.assertFalse(self.confirm(page))
        self.assertEqual(page.clicks, 0)
        self.assertIsNone(self.scraper.run_meta["pickup_confirmation_method"])
        obs = self.scraper.run_meta["pickup_confirmation_observations"]
        self.assertEqual(len(obs), 12)
        self.assertEqual(obs[-1]["enabled_buttons"], 0)


if __name__ == "__main__":
    unittest.main()
