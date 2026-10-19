from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_chedraui_phase1 import _case, audit, CONTRACT


class ChedrauiPhase1AuditTests(unittest.TestCase):
    def setUp(self):
        self.day = "2026-10-19"
        self.frame = pd.DataFrame([{
            "retailer": "Chedraui",
            "store_id": "232",
            "category_id": "lavanderia",
            "scrape_timestamp": self.day + "T10:30:00-06:00",
            "sku": "SKU-1",
            "url": "https://www.chedraui.com.mx/test/p",
            "availability_status": "AVAILABLE",
            "is_available": 1,
            "price_current": 20.5,
            "store_context_verified": True,
        }])
        self.summary = pd.DataFrame([{
            "retailer": "Chedraui",
            "store_id": "232",
            "category_id": "lavanderia",
            "status": "COMPLETE",
            "products": 1,
            "store_context_verified": True,
            "store_context_contract_version": CONTRACT,
            "pagination_gaps": 0,
            "structured_price_missing_pages": 0,
            "price_order_errors": 0,
        }])

    def evaluate(self):
        return _case(
            self.frame, self.summary, store_id="232", category_id="lavanderia",
            day=self.day, source="PROGRESO_FASE1", require_store_in_summary=True,
        )

    def test_complete_case_is_valid(self):
        result = self.evaluate()
        self.assertTrue(result["valido"])
        self.assertEqual(result["filas"], 1)

    def test_rejects_wrong_store(self):
        self.assertIsNone(_case(
            self.frame, self.summary, store_id="173", category_id="lavanderia",
            day=self.day, source="MASTER", require_store_in_summary=True
        ))

    def test_rejects_stale_capture(self):
        self.frame.loc[0, "scrape_timestamp"] = "2026-10-18T10:00:00-06:00"
        self.assertIn("fechas", self.evaluate()["problemas"])

    def test_rejects_unverified_context(self):
        self.frame.loc[0, "store_context_verified"] = False
        self.assertIn("filas sin tienda", self.evaluate()["problemas"])

    def test_rejects_unknown_availability(self):
        self.frame.loc[0, "is_available"] = None
        self.assertIn("is_available", self.evaluate()["problemas"])

    def test_rejects_gaps(self):
        self.summary.loc[0, "pagination_gaps"] = 1
        self.assertIn("pagination_gaps", self.evaluate()["problemas"])

    def test_reports_all_20_cases_without_master(self):
        with tempfile.TemporaryDirectory() as temp:
            view, _ = audit(Path(temp), self.day)
            self.assertEqual(len(view), 20)
            self.assertTrue(view["estado"].eq("SIN_CAPTURA_VALIDA").all())


if __name__ == "__main__":
    unittest.main()
