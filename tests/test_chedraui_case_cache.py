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
            "target_products": 1,
            "coverage": 1.0,
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

    def test_297_of_299_is_validated_at_9933_percent(self):
        accepted = dict(self.case, target_products=299, coverage=297 / 299)
        many_rows = pd.concat(
            [self.rows.assign(sku=str(i),
                              url="https://www.chedraui.com.mx/test/" + str(i))
             for i in range(297)],
            ignore_index=True,
        )
        accepted["products"] = 297
        path = persist_case(self.root, many_rows, accepted,
                            store_id="232", day=self.day)
        self.assertTrue(path.exists())
        self.assertEqual(len(load_valid_snapshots(self.root, day=self.day)), 1)

    def test_under_90_percent_is_never_validated(self):
        incomplete = dict(self.case, target_products=10, coverage=0.80)
        many_rows = pd.concat(
            [self.rows.assign(sku=str(i),
                              url="https://www.chedraui.com.mx/test/" + str(i))
             for i in range(8)],
            ignore_index=True,
        )
        incomplete["products"] = 8
        with self.assertRaisesRegex(ValueError, "umbral 90%"):
            persist_case(self.root, many_rows, incomplete,
                         store_id="232", day=self.day)

    def test_coverage_summary_mismatch_is_rejected(self):
        mismatch = dict(self.case, target_products=2, coverage=1.0)
        with self.assertRaisesRegex(ValueError, "cobertura reportada inconsistente"):
            persist_case(self.root, self.rows, mismatch,
                         store_id="232", day=self.day)

    def test_old_incomplete_checkpoint_not_reloaded(self):
        checkpoint = persist_case(
            self.root, self.rows, self.case,
            store_id="232", day=self.day,
        )
        # Reproduce a checkpoint stamped ISOLATED_VALIDATED by the old rule.
        with pd.ExcelFile(checkpoint) as excel:
            rows = pd.read_excel(excel, "Concentrado")
            summary = pd.read_excel(excel, "Resumen")
            control = pd.read_excel(excel, "Control")
        summary.loc[0, "target_products"] = 2
        summary.loc[0, "coverage"] = 0.5
        with pd.ExcelWriter(checkpoint, engine="openpyxl") as writer:
            rows.to_excel(writer, "Concentrado", index=False)
            summary.to_excel(writer, "Resumen", index=False)
            control.to_excel(writer, "Control", index=False)
        self.assertEqual(load_valid_snapshots(self.root, day=self.day), {})

    def test_missing_published_target_does_not_validate(self):
        missing = dict(self.case, target_products=None)
        with self.assertRaisesRegex(ValueError, "total publicado ausente"):
            persist_case(self.root, self.rows, missing,
                         store_id="232", day=self.day)

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
