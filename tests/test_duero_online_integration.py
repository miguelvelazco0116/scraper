from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from duero_online_integration_evidence import (
    EXPECTED_CATEGORIES, ONLINE_SCOPE,
    validate_duero_online_evidence,
)


class DueroScopedApprovalTests(unittest.TestCase):
    day = "2026-10-19"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        keys = sorted(EXPECTED_CATEGORIES)
        # 659 rows, 6 categories, totals deliberately exact.
        counts = [68, 240, 67, 101, 137, 46]
        catalogue_counts = [50, 150, 55, 73, 116, 41]
        self.summary = pd.DataFrame([
            {
                "category_id": key,
                "products": count,
                "catalogue_products_seen": n,
            }
            for key, count, n in zip(keys, counts, catalogue_counts)
        ])
        self.frame = pd.DataFrame([
            {
                "category_id": key,
                "sku": key + str(i),
                "url": f"https://duero.example/{key}/{i}",
                "units_per_package": 1,
                "price_current": 12,
                "price_per_unit": 12,
                "is_available": i % 2,
                "store_context_verified": False,
                "postal_code": None,
            }
            for key, count in zip(keys, counts)
            for i in range(count)
        ])
        self.pagination = {
            "generated_at": self.day + "T12:10:38-06:00",
            "all_api_paginations_verified": True,
            "categories": [
                {
                    "category_id": key,
                    "pagination_verified": True,
                    "independent_match": True,
                    "terminal_page_limit50": 4,
                    "terminal_page_limit250": 2,
                    "catalogue_products_seen": catalogue,
                    "variant_count_api": count,
                    "errors": [],
                }
                for key, count, catalogue in zip(keys, counts, catalogue_counts)
            ],
        }
        self.storefront = {
            "generated_at": self.day + "T12:38:34-06:00",
            "all_storefront_counts_match": True,
            "postal_code_verified": False,
            "store_context_verified": False,
            "storefront_results": [
                {
                    "category_id": key,
                    "match": True,
                    "http_status": 200,
                    "api_catalogue_products": catalogue,
                    "html_catalogue_products": catalogue,
                }
                for key, catalogue in zip(keys, catalogue_counts)
            ],
        }
        self.packaging = {
            "capture_date": self.day,
            "variants": 659,
            "dual_pagination_verified": True,
            "unit_count_unknown": 0,
            "unit_count_conflicts": 0,
            "prior_count_without_variant_evidence": 0,
            "identified_units_without_unit_price": 0,
            "boxes_without_unit_count": 0,
            "unit_count_identified": 659,
            "store_context_verified": 0,
        }
        self.save()

    def save(self):
        for prefix, value in [
            ("duero_paginacion_", self.pagination),
            ("duero_storefront_", self.storefront),
            ("auditoria_duero_fase5_", self.packaging),
        ]:
            path = self.dir / (prefix + "20261019_120000_000001.json")
            path.write_text(json.dumps(value), encoding="utf-8")

    def approve(self):
        return validate_duero_online_evidence(
            self.frame, self.summary, self.day, self.dir
        )

    def test_online_public_catalogue_only(self):
        approval = self.approve()
        self.assertEqual(approval["scope"], ONLINE_SCOPE)
        self.assertEqual(approval["variants"], 659)
        self.assertEqual(approval["catalogue_products"], 485)
        self.assertFalse(approval["postal_price_verified"])
        self.assertFalse(approval["store_context_verified"])
        self.assertTrue(self.frame["store_context_verified"].eq(False).all())

    def test_refuses_unchecked_postal_price_flags(self):
        self.frame.loc[0, "store_context_verified"] = True
        with self.assertRaisesRegex(ValueError, "flag de tienda"):
            self.approve()

    def test_refuses_unreviewed_units(self):
        self.frame.loc[0, "units_per_package"] = None
        with self.assertRaisesRegex(ValueError, "units_per_package"):
            self.approve()

    def test_refuses_mismatched_storefront(self):
        self.storefront["storefront_results"][0]["html_catalogue_products"] = 999
        self.save()
        with self.assertRaisesRegex(ValueError, "HTML no cotejado"):
            self.approve()

    def test_refuses_pagination_without_empty_page(self):
        self.pagination["categories"][0]["terminal_page_limit50"] = None
        self.save()
        with self.assertRaisesRegex(ValueError, "paginación discrepante"):
            self.approve()

    def test_refuses_prior_packaging_conflicts(self):
        self.packaging["prior_count_without_variant_evidence"] = 1
        self.save()
        with self.assertRaisesRegex(ValueError, "empaques no completamente auditados"):
            self.approve()

    def test_refuses_old_day_evidence(self):
        self.pagination["generated_at"] = "2026-10-18T10:00:00-06:00"
        self.save()
        with self.assertRaisesRegex(ValueError, "otra fecha"):
            self.approve()

    def test_refuses_duplicate_variants(self):
        self.frame.loc[1, "sku"] = self.frame.loc[0, "sku"]
        self.frame.loc[1, "url"] = self.frame.loc[0, "url"]
        self.frame.loc[1, "category_id"] = self.frame.loc[0, "category_id"]
        with self.assertRaisesRegex(ValueError, "duplicada"):
            self.approve()

    def test_refuses_missing_evidence(self):
        for file in self.dir.glob("auditoria_duero_fase5_*.json"):
            file.unlink()
        with self.assertRaisesRegex(ValueError, "sin evidencia"):
            self.approve()


if __name__ == "__main__":
    unittest.main()
