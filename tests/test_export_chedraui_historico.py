from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from export_chedraui_historico import (
    MANIFEST_PATH, build_historical_export, validate_manifest,
)

CATEGORIES = {
    "higiene-bucal", "lavanderia",
    "desodorantes-y-antitranspirantes", "depilacion",
    "respiratorios",
}
STORES = {
    "chedraui-polanco": "232", "chedraui-tepeyac": "173",
    "chedraui-cancun-kabah": "255", "chedraui-puebla-centro-sur": "76",
}


def fixture():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    snapshots = {}
    for store in manifest["stores"]:
        day = store["capture_date"]
        store_id = store["store_id"]
        snapshots.setdefault(day, {})
        for category in store["categories"]:
            category_id = category["category_id"]
            count = category["products"]
            required = category["price_required"]
            rows = [{
                "retailer": "Chedraui", "store_id": store_id,
                "category_id": category_id,
                "scrape_timestamp": day + "T15:00:00-06:00",
                "store_context_verified": True,
                "store_context_method": "visible_store_control",
                "availability_status": "AVAILABLE" if index < required else "UNAVAILABLE",
                "is_available": 1 if index < required else 0,
                "sku": f"{store_id}_{category_id}_{index}",
                "url": f"https://www.chedraui.com.mx/{store_id}/{category_id}/{index}",
                "price_current": 99.0 if index < required else None,
            } for index in range(count)]
            case = {
                "category_id": category_id,
                "store_id": store_id,
                "status": "COMPLETE",
                "products": count, "target_products": count,
                "price_required": required,
                "price_required_complete": required,
                "store_context_verified": True,
                "store_context_method": "visible_store_control",
                "store_context_contract_version": "chedraui-directory-ui-v3",
                "pagination_gaps": 0,
                "structured_price_missing_pages": 0,
                "price_order_errors": 0,
            }
            snapshots[day][("Chedraui", category_id, store_id)] = (
                pd.DataFrame(rows), case
            )
    return manifest, snapshots


class HistoricalChedrauiTests(unittest.TestCase):
    def test_manifests_match_configured_20_cases(self):
        manifest, _ = fixture()
        validate_manifest(
            manifest, allowed_categories=CATEGORIES,
            configured_locations=STORES,
        )
        self.assertEqual(manifest["expected_total_products"], 5139)
        self.assertEqual(manifest["expected_total_required_prices"], 5042)

    def test_reconstructs_5139_rows_without_disguising_dates(self):
        manifest, snapshots = fixture()
        frame, summary, control = build_historical_export(
            manifest, snapshots
        )
        self.assertEqual(len(frame), 5139)
        self.assertEqual(len(summary), 20)
        self.assertEqual(
            set(frame["scrape_timestamp"].str[:10]),
            {"2026-10-19", "2026-10-20"},
        )
        self.assertEqual(
            set(summary["integration_scope"]),
            {"HISTORICAL_MULTI_DATE_NOT_DAILY_MASTER"},
        )
        self.assertFalse(bool(control.iloc[0]["is_daily_master"]))
        store_counts = frame.groupby("store_id").size().to_dict()
        self.assertEqual(
            store_counts,
            {"232": 1271, "173": 1306, "255": 1294, "76": 1268},
        )

    def test_missing_checkpoint_fails_closed(self):
        manifest, snapshots = fixture()
        snapshots["2026-10-20"].pop(("Chedraui", "respiratorios", "76"))
        with self.assertRaisesRegex(ValueError, "Checkpoint faltante"):
            build_historical_export(manifest, snapshots)

    def test_wrong_capture_date_fails_closed(self):
        manifest, snapshots = fixture()
        frame, _ = snapshots["2026-10-19"][("Chedraui", "higiene-bucal", "232")]
        frame.loc[0, "scrape_timestamp"] = "2026-10-20T15:00:00-06:00"
        with self.assertRaisesRegex(ValueError, "fecha diferente"):
            build_historical_export(manifest, snapshots)

    def test_price_required_mismatch_fails_closed(self):
        manifest, snapshots = fixture()
        item = snapshots["2026-10-19"][("Chedraui", "lavanderia", "173")][1]
        item["price_required_complete"] -= 1
        with self.assertRaisesRegex(ValueError, "Precios requeridos"):
            build_historical_export(manifest, snapshots)

    def test_wrong_availability_fails_closed(self):
        manifest, snapshots = fixture()
        frame, _ = snapshots["2026-10-19"][("Chedraui", "lavanderia", "173")]
        frame.loc[0, "is_available"] = 0
        with self.assertRaisesRegex(ValueError, "Disponibilidad 1/0"):
            build_historical_export(manifest, snapshots)

    def test_manifest_rejects_altered_store_category(self):
        manifest, _ = fixture()
        edited = copy.deepcopy(manifest)
        edited["stores"][0]["categories"][0]["category_id"] = "categoria-ajena"
        with self.assertRaisesRegex(ValueError, "categorías inesperadas"):
            validate_manifest(
                edited, allowed_categories=CATEGORIES,
                configured_locations=STORES,
            )

    def test_manifest_rejects_changed_totals(self):
        manifest, _ = fixture()
        edited = copy.deepcopy(manifest)
        edited["expected_total_products"] += 1
        with self.assertRaisesRegex(ValueError, "Totales del manifiesto"):
            validate_manifest(
                edited, allowed_categories=CATEGORIES,
                configured_locations=STORES,
            )


if __name__ == "__main__":
    unittest.main()
