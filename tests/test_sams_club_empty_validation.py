from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from scraper.retailers.sams_club import SamsClubScraper
from test_sams_club import _summary_is_acceptable, run_category


def fake_source(rows, *, product_links, source_url="https://www.sams.com.mx/browse/test"):
    return (
        rows,
        {
            "source_url": source_url,
            "rows": len(rows),
            "new_rows": len(rows),
            "product_links": product_links,
            "stabilized": True,
            "explicit_pages_stabilized": True,
            "pagination_complete": True,
            "pages_visited": 1,
            "pagination_method": "ui_numeric_next",
            "no_product_links": not bool(product_links),
        },
    )


class SamsEmptyCategoryTests(unittest.TestCase):
    def test_visible_product_links_without_extracted_rows_do_not_prove_pagination(self):
        scraper = SamsClubScraper()
        saved = []
        scraper._collect_source = lambda *args: fake_source([], product_links=12)
        scraper._save_diagnostics = lambda page, prefix: saved.append(prefix)
        category = SimpleNamespace(
            id="cuidado-bucal", url="https://www.sams.com.mx/browse/test",
            source_urls=("https://www.sams.com.mx/browse/test",),
        )
        page = SimpleNamespace(url="https://www.sams.com.mx/browse/test")
        result = scraper.scrape_category_on_page(page, category, None)
        self.assertEqual(result, [])
        self.assertFalse(scraper.run_meta["pagination_verified"])
        self.assertEqual(scraper.run_meta["status"], "EMPTY")
        self.assertTrue(any(x.startswith("empty_cuidado-bucal") for x in saved))

    def test_one_valid_and_one_empty_source_is_partial(self):
        scraper = SamsClubScraper()
        row = {"sku": "1", "price_current": 50, "url": "https://www.sams.com.mx/ip/1"}
        sources = [
            fake_source([row], product_links=1),
            fake_source([], product_links=0),
        ]
        scraper._collect_source = lambda *args: sources.pop(0)
        scraper._save_diagnostics = lambda *args: None
        category = SimpleNamespace(
            id="cuidado-de-la-ropa", url="https://www.sams.com.mx/browse/test",
            source_urls=(
                "https://www.sams.com.mx/browse/a",
                "https://www.sams.com.mx/browse/b",
            ),
        )
        page = SimpleNamespace(url="https://www.sams.com.mx/browse/b")
        result = scraper.scrape_category_on_page(page, category, None)
        self.assertEqual(len(result), 1)
        self.assertFalse(scraper.run_meta["pagination_verified"])
        self.assertEqual(scraper.run_meta["status"], "PARTIAL")

    def test_summary_rejects_no_pagination_proof(self):
        summary = {
            "status": "SUCCESS", "products": 18,
            "sku_complete": 18, "price_complete": 18,
            "url_complete": 18, "sources_discovered": 1,
            "sources_visited": 1, "catalogue_gate_ok": True,
            "pagination_verified": False, "price_validation_errors": 0,
            "blocked_detected": False,
        }
        self.assertFalse(_summary_is_acceptable(summary))

    def test_run_category_does_not_approve_empty_with_false_pager(self):
        category = SimpleNamespace(
            id="cuidado-bucal", url="https://www.sams.com.mx/browse/test",
            department="Belleza", name="Cuidado bucal", subcategory="Pasta dental",
        )
        def fake_scrape(self, page, category, location):
            self.run_meta = {
                "target_products": None, "coverage": None, "status": "EMPTY",
                "pagination_verified": True, "blocked_detected": False,
                "sources_discovered": 1, "sources": [
                    {"pages_visited": 1, "pagination_method": "ui_numeric_next"}
                ],
            }
            return []
        with patch.object(SamsClubScraper, "scrape_category_on_page", fake_scrape):
            _, summary, *_ = run_category(category, None, SimpleNamespace())
        self.assertEqual(summary["status"], "EMPTY")
        self.assertFalse(summary["catalogue_gate_ok"])


if __name__ == "__main__":
    unittest.main()
