from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from chedraui_case_cache import (
    CONTRACT, persist_case, seed_from_prior_test, validate_case,
    load_valid_snapshots,
)


class ChedrauiCaseCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.day = "2026-10-19"
        self.case = {
            "category_id": "higiene-bucal",
            "status": "COMPLETE",
            "products": 1,
            "store_context_verified": True,
            "store_context_contract_version": CONTRACT,
            "pagination_gaps": 0,
            "structured_price_missing_pages": 0,
            "price_order_errors": 0,
        }
        self.rows = pd.DataFrame([{
            "retailer": "Chedraui",
            "category_id": "higiene-bucal",
            "store_id": "232",
            "scrape_timestamp": self.day + "T14:00:00-06:00",
            "store_context_verified": True,
            "is_available": 1,
            "availability_status": "AVAILABLE",
            "sku": "102300",
            "url": "https://www.chedraui.com.mx/test/p",
            "price_current": 89.0,
        }])

    def test_complete_is_persisted_independently(self):
        target = persist_case(self.root, self.rows, self.case,
                              store_id="232", day=self.day)
        self.assertTrue(target.is_file())
        self.assertEqual(
            pd.read_excel(target, sheet_name="Resumen").iloc[0]["store_id"],
            232,
        )
        self.assertEqual(
            pd.read_excel(target, sheet_name="Control").iloc[0]["status"],
            "ISOLATED_VALIDATED",
        )

    def test_unknown_availability_never_validated(self):
        self.rows.loc[0, "is_available"] = None
        with self.assertRaisesRegex(ValueError, "disponibilidad"):
            persist_case(self.root, self.rows, self.case,
                         store_id="232", day=self.day)

    def test_wrong_store_never_validated(self):
        self.rows.loc[0, "store_id"] = "173"
        problems = validate_case(self.rows, self.case,
                                 store_id="232", category_id="higiene-bucal",
                                 day=self.day)
        self.assertIn("tienda incorrecta", problems)

    def test_different_day_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "fecha"):
            persist_case(self.root, self.rows, self.case,
                         store_id="232", day="2026-10-20")

    def test_recovers_previous_isolated_success_only_once(self):
        previous = self.root / "output" / "chedraui_polanco_test.xlsx"
        previous.parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(previous, engine="openpyxl") as w:
            self.rows.to_excel(w, sheet_name="Concentrado", index=False)
            pd.DataFrame([self.case]).to_excel(
                w, sheet_name="Resumen", index=False
            )
        self.assertEqual(
            len(seed_from_prior_test(self.root, previous,
                                     store_id="232", day=self.day)), 1
        )
        self.assertEqual(
            len(seed_from_prior_test(self.root, previous,
                                     store_id="232", day=self.day)), 0
        )

    def test_loads_only_same_day_validated_snapshots(self):
        persist_case(self.root, self.rows, self.case,
                     store_id="232", day=self.day)
        cases = load_valid_snapshots(self.root, day=self.day)
        self.assertIn(("Chedraui", "higiene-bucal", "232"), cases)
        self.assertEqual(len(cases[("Chedraui", "higiene-bucal", "232")][0]), 1)
        self.assertEqual(
            load_valid_snapshots(self.root, day="2026-10-20"), {}
        )

    def test_error_summary_cannot_seed(self):
        previous = self.root / "output" / "chedraui_polanco_test.xlsx"
        previous.parent.mkdir(parents=True, exist_ok=True)
        broken = dict(self.case, status="STORE_CONTEXT_ERROR")
        with pd.ExcelWriter(previous, engine="openpyxl") as w:
            self.rows.to_excel(w, sheet_name="Concentrado", index=False)
            pd.DataFrame([broken]).to_excel(
                w, sheet_name="Resumen", index=False
            )
        self.assertEqual(
            seed_from_prior_test(self.root, previous,
                                 store_id="232", day=self.day), []
        )


if __name__ == "__main__":
    unittest.main()
