from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from integrate_complete_retailers import _quality, _phase_states, _date_check


class CompleteRetailerIntegrationTests(unittest.TestCase):
    def test_complete_retailer_passes(self):
        retailer = "Soriana"
        categories = ["c" + str(i) for i in range(6)]
        frame = pd.DataFrame([
            {
                "retailer": retailer,
                "category_id": category,
                "sku": str(i),
                "price_current": 100,
                "url": "https://example.com/" + str(i),
                "scrape_timestamp": "2026-10-19T10:00:00",
            }
            for i, category in enumerate(categories)
        ])
        summary = pd.DataFrame([
            {"retailer": retailer, "category_id": category, "status": "COMPLETE", "products": 1}
            for category in categories
        ])
        _quality(2, retailer, frame, summary)

    def test_partial_retailer_is_rejected(self):
        retailer = "Soriana"
        frame = pd.DataFrame([
            {"retailer": retailer, "category_id": "c0", "sku": "1",
             "price_current": 100, "url": "https://example.com"}
        ])
        summary = pd.DataFrame([
            {"retailer": retailer, "category_id": "c0", "status": "PARTIAL", "products": 1}
        ])
        with self.assertRaises(ValueError):
            _quality(2, retailer, frame, summary)

    def test_partial_phase1_does_not_block_complete_phase2(self):
        retailers = ["Farmacias del Ahorro", "Ibarra Mayoreo", "Farmacias Guadalajara", "Soriana"]
        frame = pd.DataFrame({"retailer": retailers})
        summary = pd.DataFrame(
            [{"source_phase": "FASE 1", "retailer": "Farmacias del Ahorro",
              "status": "COMPLETE", "category_id": "a" + str(i)}
             for i in range(6)]
            + [{"source_phase": "FASE 1", "retailer": "Ibarra Mayoreo",
                "status": "COMPLETE", "category_id": "i" + str(i)}
               for i in range(4)]
            + [{"source_phase": "FASE 2", "retailer": "Farmacias Guadalajara",
                "status": "COMPLETE", "category_id": "g" + str(i)}
               for i in range(4)]
            + [{"source_phase": "FASE 2", "retailer": "Soriana",
                "status": "COMPLETE", "category_id": "s" + str(i)}
               for i in range(6)]
        )
        completed, states = _phase_states(frame, summary)
        self.assertEqual(states[1], "PARTIAL_VALIDATED")
        self.assertEqual(states[2], "COMPLETE")
        self.assertIn(2, completed)
        self.assertNotIn(1, completed)

    def test_mixed_capture_dates_rejected(self):
        frame = pd.DataFrame({"scrape_timestamp": [
            "2026-10-19T09:00:00", "2026-10-18T09:00:00"
        ]})
        with self.assertRaises(ValueError):
            _date_check(frame, pd.DataFrame(), "2026-10-19")


if __name__ == "__main__":
    unittest.main()
