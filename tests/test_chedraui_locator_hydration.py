from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scraper.retailers.chedraui_polanco import ChedrauiScraper


class _Button:
    def __init__(self, page):
        self.page = page
    def is_visible(self):
        return True
    def inner_text(self, timeout=None):
        return "Chedraui Selecto México Polanco"
    def get_attribute(self, name):
        return None
    def click(self, timeout=None):
        self.page.clicked += 1


class _Nodes:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector
    def count(self):
        # The VTEX header hydrates only on the third page wait.
        if self.page.waits >= 2 and self.selector == (
            "button.chedrauimx-locator-2-x-labelTextAddress"
        ):
            return 1
        return 0
    def nth(self, index):
        return _Button(self.page)


class _Page:
    url = "https://www.chedraui.com.mx/"
    def __init__(self):
        self.waits = 0
        self.clicked = 0
    def locator(self, selector):
        return _Nodes(self, selector)
    def wait_for_timeout(self, timeout):
        self.waits += 1


class LocatorHeaderTests(unittest.TestCase):
    def test_waits_for_visible_button_without_skipping_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            scraper = ChedrauiScraper(diagnostics_dir=folder)
            page = _Page()
            self.assertTrue(scraper._open_locator(page))
            self.assertEqual(page.clicked, 1)
            self.assertEqual(
                scraper.run_meta["location_button_selector"],
                "button.chedrauimx-locator-2-x-labelTextAddress"
            )
            self.assertGreaterEqual(
                len(scraper.run_meta["location_locator_attempts"]), 3
            )

    def test_no_visible_locator_remains_unverified(self):
        with tempfile.TemporaryDirectory() as folder:
            scraper = ChedrauiScraper(diagnostics_dir=folder)
            page = _Page()
            page.waits = -1000
            scraper._click_text = lambda page, labels, timeout=4000: False
            self.assertFalse(scraper._open_locator(page))
            self.assertEqual(page.clicked, 0)
            self.assertEqual(len(scraper.run_meta["location_locator_attempts"]), 10)
            self.assertFalse(scraper.run_meta["location_locator_fallback_clicked"])


if __name__ == "__main__":
    unittest.main()
