from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from test_chedraui import _quality


def product_frame(n: int) -> pd.DataFrame:
    return pd.DataFrame({
        "availability_status": ["AVAILABLE"] * n,
        "price_current": [100] * n,
        "price_regular": [120] * n,
        "sku": [str(i) for i in range(n)],
        "url": [f"https://www.chedraui.com.mx/item/{i}" for i in range(n)],
    })


def metadata(total):
    return {
        "displayed_category_products": total,
        "store_context_verified": True,
        "structured_price_missing_pages": [],
        "internal_pagination_gaps": [],
    }


class ChedrauiStrictCoverageTests(unittest.TestCase):
    def test_297_of_299_is_partial_not_complete(self):
        status, metrics = _quality(product_frame(297), metadata(299))
        self.assertEqual(status, "PARTIAL")
        self.assertEqual(metrics["products"], 297)
        self.assertEqual(metrics["target_products"], 299)
        self.assertAlmostEqual(metrics["coverage"], 297 / 299)

    def test_exact_count_is_complete(self):
        status, metrics = _quality(product_frame(299), metadata(299))
        self.assertEqual(status, "COMPLETE")
        self.assertEqual(metrics["coverage"], 1)

    def test_more_than_published_is_partial(self):
        status, metrics = _quality(product_frame(301), metadata(299))
        self.assertEqual(status, "PARTIAL")
        self.assertGreater(metrics["coverage"], 1)

    def test_missing_published_total_is_partial(self):
        status, metrics = _quality(product_frame(297), metadata(None))
        self.assertEqual(status, "PARTIAL")
        self.assertIsNone(metrics["coverage"])

    def test_low_coverage_is_partial(self):
        status, metrics = _quality(product_frame(270), metadata(299))
        self.assertEqual(status, "PARTIAL")
        self.assertLess(metrics["coverage"], 1)

    def test_sku_gaps_prevent_complete(self):
        frame = product_frame(299)
        frame.loc[0, "sku"] = ""
        status, _ = _quality(frame, metadata(299))
        self.assertEqual(status, "PARTIAL")


if __name__ == "__main__":
    unittest.main()
