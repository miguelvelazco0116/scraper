from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from integrate_chedraui_store import prepare_integration
from chedraui_case_cache import CONTRACT

CATEGORIES = {"higiene-bucal", "lavanderia", "desodorantes-y-antitranspirantes", "depilacion", "respiratorios"}
DAY = "2026-10-19"


def setup_data():
    isolated = {}
    for category in CATEGORIES:
        frame = pd.DataFrame([{
            "retailer": "Chedraui", "store_id": "232",
            "category_id": category, "scrape_timestamp": DAY + "T10:00:00",
            "store_context_verified": True, "store_context_method": "visible_store_control",
            "availability_status": "AVAILABLE", "is_available": 1,
            "sku": category, "url": "https://www.chedraui.com.mx/" + category,
            "price_current": 100.0,
        }])
        summary = {
            "category_id": category, "status": "COMPLETE", "products": 1,
            "target_products": 1, "coverage": 1.0,
            "price_required": 1, "price_required_complete": 1,
            "store_context_verified": True, "store_context_method": "visible_store_control",
            "store_context_contract_version": CONTRACT,
            "pagination_gaps": 0, "structured_price_missing_pages": 0,
            "price_order_errors": 0,
        }
        isolated[("Chedraui", category, "232")] = (frame, summary)
    other = pd.DataFrame([
        {"retailer": "Walmart", "store_id": "A", "category_id": "another", "sku": "W"},
        {"retailer": "Chedraui", "store_id": "173", "category_id": "lavanderia", "sku": "T"},
    ])
    other_summary = pd.DataFrame([
        {"retailer": "Walmart", "store_id": "A", "category_id": "another",
         "source_phase": "FASE 3", "status": "COMPLETE"},
        {"retailer": "Chedraui", "store_id": "173", "category_id": "lavanderia",
         "source_phase": "FASE 1", "status": "COMPLETE"},
    ])
    return other, other_summary, isolated


class SingleStoreIntegrationTests(unittest.TestCase):
    def test_preserves_other_stores_and_retailers(self):
        before, before_summary, evidence = setup_data()
        combined, summary, report = prepare_integration(
            before, before_summary, evidence,
            store_id="232", day=DAY, category_ids=CATEGORIES,
        )
        self.assertEqual(len(combined), 7)
        self.assertEqual(len(summary), 7)
        self.assertEqual(report["incoming_rows"], 5)
        self.assertEqual(report["other_rows_preserved"], 2)
        self.assertIn("W", combined["sku"].values)
        self.assertIn("T", combined["sku"].values)

    def test_refuses_incomplete_store(self):
        before, before_summary, evidence = setup_data()
        del evidence[("Chedraui", "depilacion", "232")]
        with self.assertRaisesRegex(ValueError, "Checkpoint COMPLETE faltante"):
            prepare_integration(before, before_summary, evidence,
                                store_id="232", day=DAY, category_ids=CATEGORIES)

    def test_refuses_mismatched_store(self):
        before, before_summary, evidence = setup_data()
        evidence[("Chedraui", "depilacion", "232")][0].loc[0, "store_id"] = "173"
        with self.assertRaisesRegex(ValueError, "tienda incorrecta"):
            prepare_integration(before, before_summary, evidence,
                                store_id="232", day=DAY, category_ids=CATEGORIES)

    def test_replaces_only_matching_partition(self):
        before, before_summary, evidence = setup_data()
        before.loc[len(before)] = {
            "retailer": "Chedraui", "store_id": "232",
            "category_id": "depilacion", "sku": "OLD",
        }
        before_summary.loc[len(before_summary)] = {
            "retailer": "Chedraui", "store_id": "232",
            "category_id": "depilacion", "source_phase": "FASE 1", "status": "COMPLETE",
        }
        combined, summary, report = prepare_integration(
            before, before_summary, evidence,
            store_id="232", day=DAY, category_ids=CATEGORIES,
        )
        self.assertEqual(report["replaced_rows"], 1)
        self.assertEqual(len(combined), 7)
        self.assertNotIn("OLD", combined["sku"].values)


if __name__ == "__main__":
    unittest.main()
