from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_master_phases import inventory


class MasterPhaseInventoryTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame([
            {"retailer": "Soriana", "category_id": "higiene-bucal", "sku": "1",
             "price_current": 99, "url": "https://example.com/1", "is_available": 1,
             "store_context_verified": False},
            {"retailer": "Soriana", "category_id": "higiene-bucal", "sku": "2",
             "price_current": 108, "url": "https://example.com/2", "is_available": 0,
             "store_context_verified": False},
            {"retailer": "Farmacias Similares", "category_id": "condones", "sku": "3",
             "price_current": 10, "url": "https://example.com/3", "is_available": 1,
             "store_context_verified": False},
        ])
        self.summary = pd.DataFrame([
            {"source_phase": "FASE 2", "retailer": "Soriana", "category_id": "higiene-bucal",
             "status": "COMPLETE", "products": 2},
            {"source_phase": "FASE 5", "retailer": "Farmacias Similares", "category_id": "condones",
             "status": "COMPLETE", "products": 1},
        ])
        self.control = pd.DataFrame([{
            "phase1_status": "PENDING",
            "phase2_status": "PARTIAL_VALIDATED",
            "phase3_status": "PENDING",
            "phase4_status": "PENDING",
            "phase5_status": "PARTIAL_VALIDATED",
        }])

    def run_audit(self):
        with patch(
            "audit_master_phases._read_existing_master",
            return_value=(self.frame, self.summary, self.control)
        ):
            return inventory(Path("unused.xlsx"))

    def test_reports_real_rows_not_expected_guesses(self):
        phases, retailers, _, facts = self.run_audit()
        self.assertEqual(facts["total_rows"], 3)
        self.assertEqual(facts["total_retailers"], 2)
        self.assertEqual(facts["phases_complete"], 0)
        soriana = retailers.loc[retailers["retailer"].eq("Soriana")].iloc[0]
        self.assertEqual(soriana["filas_master"], 2)
        self.assertEqual(soriana["casos_master"], 1)
        self.assertEqual(soriana["estado"], "INTEGRADO_PARCIAL_O_INCONSISTENTE")
        self.assertEqual(phases.iloc[1]["estado_calculado"], "PARTIAL_VALIDATED")
        self.assertFalse(facts["warnings"])

    def test_marks_unknown_retailer_and_counts_rows(self):
        self.frame.loc[len(self.frame)] = {
            "retailer": "No Configurado", "category_id": "x", "sku": "999",
            "price_current": 99, "url": "https://example.com/999", "is_available": 1,
            "store_context_verified": False,
        }
        _, _, _, facts = self.run_audit()
        self.assertEqual(facts["total_rows"], 4)
        self.assertIn("No Configurado", facts["unknown_retailers"])
        self.assertTrue(any("Filas no reconciliadas" in x for x in facts["warnings"]))

    def test_detects_control_mismatch(self):
        self.control.loc[0, "phase5_status"] = "COMPLETE"
        _, _, _, facts = self.run_audit()
        self.assertTrue(any("Fase 5" in x for x in facts["warnings"]))

    def test_detects_reported_row_mismatch(self):
        self.summary.loc[0, "products"] = 3
        _, _, _, facts = self.run_audit()
        self.assertTrue(any("Soriana" in x and "Resumen=3" in x for x in facts["warnings"]))


    def test_phase6_yza_is_reported_only_when_two_cases_complete(self):
        for category in ("medicamentos-respiratorio", "higiene-bucal"):
            self.frame.loc[len(self.frame)] = {
                "retailer": "Farmacias YZA", "category_id": category,
                "sku": category, "price_current": 75,
                "url": "https://www.yza.mx/" + category,
                "is_available": None, "store_context_verified": False,
            }
            self.summary.loc[len(self.summary)] = {
                "source_phase": "FASE 6", "retailer": "Farmacias YZA",
                "category_id": category, "status": "COMPLETE", "products": 1,
            }
        self.control.loc[0, "phase6_status"] = "COMPLETE"
        phases, retailers, _, facts = self.run_audit()
        phase = phases.loc[phases["fase"].eq(6)].iloc[0]
        self.assertEqual(phase["estado_calculado"], "COMPLETE")
        self.assertEqual(facts["phases_complete"], 1)
        self.assertEqual(
            retailers.loc[retailers["retailer"].eq("Farmacias YZA"), "casos_master"].iloc[0],
            2,
        )

    def test_phase6_missing_on_old_master_is_pending_not_a_warning(self):
        phases, _, _, facts = self.run_audit()
        row = phases.loc[phases["fase"].eq(6)].iloc[0]
        self.assertEqual(row["estado_calculado"], "PENDING")
        self.assertTrue(row["control_coincide"])
        self.assertFalse(facts["warnings"])


if __name__ == "__main__":
    unittest.main()
