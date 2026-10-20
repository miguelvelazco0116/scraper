from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from chedraui_case_cache import CONTRACT
from cierre_chedraui_20261019 import DAY, EXPECTED_ROWS, STORES, plan_closure

CATEGORIES = [
    "higiene-bucal", "lavanderia",
    "desodorantes-y-antitranspirantes", "depilacion", "respiratorios",
]


def fixtures():
    evidence = {}
    for slug, store, total in STORES:
        counts = [total // 5] * 5
        for i in range(total % 5):
            counts[i] += 1
        for category, count in zip(CATEGORIES, counts):
            records = []
            for i in range(count):
                records.append({
                    "retailer": "Chedraui", "store_id": store,
                    "category_id": category,
                    "scrape_timestamp": DAY + "T15:00:00",
                    "store_context_verified": True,
                    "store_context_method": "visible_store_control",
                    "availability_status": "AVAILABLE", "is_available": 1,
                    "sku": f"{store}-{category}-{i}",
                    "url": f"https://www.chedraui.com.mx/{store}/{category}/{i}",
                    "price_current": 80.0,
                })
            evidence[("Chedraui", category, store)] = (
                pd.DataFrame(records),
                {
                    "category_id": category, "status": "COMPLETE",
                    "products": count, "target_products": count,
                    "price_required": count,
                    "price_required_complete": count,
                    "store_context_verified": True,
                    "store_context_method": "visible_store_control",
                    "store_context_contract_version": CONTRACT,
                    "pagination_gaps": 0,
                    "structured_price_missing_pages": 0,
                    "price_order_errors": 0,
                }
            )
    other_rows = pd.DataFrame([
        {"retailer": "Walmart", "store_id": "W", "category_id": "another",
         "sku": "W-keep"},
        {"retailer": "Chedraui", "store_id": "76", "category_id": "lavanderia",
         "sku": "Puebla-keep"},
    ])
    other_summary = pd.DataFrame([
        {"retailer": "Walmart", "store_id": "W", "category_id": "another",
         "source_phase": "FASE 3", "status": "COMPLETE"},
        {"retailer": "Chedraui", "store_id": "76", "category_id": "lavanderia",
         "source_phase": "FASE 1", "status": "COMPLETE"},
    ])
    return other_rows, other_summary, evidence


class DayClosureTests(unittest.TestCase):
    def test_full_batch_is_exact_and_retains_unrelated_rows(self):
        rows, summary, evidence = fixtures()
        merged, merged_summary, report = plan_closure(
            rows, summary, evidence
        )
        self.assertEqual(sum(item["incoming_rows"] for item in report),
                         EXPECTED_ROWS)
        self.assertEqual(len(report), 3)
        self.assertEqual(len(merged), EXPECTED_ROWS + 2)
        self.assertEqual(len(merged_summary), 17)
        self.assertIn("W-keep", merged["sku"].values)
        self.assertIn("Puebla-keep", merged["sku"].values)

    def test_missing_single_case_rejects_whole_batch(self):
        rows, summary, evidence = fixtures()
        del evidence[("Chedraui", "respiratorios", "255")]
        with self.assertRaisesRegex(ValueError, "Checkpoint COMPLETE faltante"):
            plan_closure(rows, summary, evidence)

    def test_wrong_store_rejects_whole_batch(self):
        rows, summary, evidence = fixtures()
        evidence[("Chedraui", "lavanderia", "173")][0]["store_id"] = "232"
        with self.assertRaisesRegex(ValueError, "tienda incorrecta"):
            plan_closure(rows, summary, evidence)

    def test_count_regression_rejects_whole_batch(self):
        rows, summary, evidence = fixtures()
        frame, item = evidence[("Chedraui", "depilacion", "173")]
        duplicate = frame.iloc[[0]].copy()
        duplicate["sku"] = "extra"
        duplicate["url"] = "https://www.chedraui.com.mx/extra"
        evidence[("Chedraui", "depilacion", "173")] = (
            pd.concat([frame, duplicate], ignore_index=True),
            {**item, "products": item["products"] + 1,
             "target_products": item["target_products"] + 1,
             "price_required": item["price_required"] + 1,
             "price_required_complete": item["price_required_complete"] + 1},
        )
        with self.assertRaisesRegex(ValueError, "esperado 5 categorías"):
            plan_closure(rows, summary, evidence)


if __name__ == "__main__":
    unittest.main()
