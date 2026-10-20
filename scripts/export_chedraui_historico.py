from __future__ import annotations

"""Create an audited historical Chedraui workbook from dated local checkpoints.

Separate from all productive daily masters. Does not change current_master.json,
phase state, checkpoint source files, or data for other retailers.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS
from chedraui_case_cache import CONTRACT, _store_id, load_valid_snapshots, validate_case
from integrate_chedraui_store import _expected_categories
from integrate_validated_phase import _canonical, _write_master
from scraper.config import load_locations

MANIFEST_PATH = ROOT / "config" / "chedraui" / "validated_runs_20261019_20261020.json"
OUTPUT = ROOT / "output" / "historicos" / "chedraui_validado_2026-10-19_a_2026-10-20.xlsx"
EXPECTED_DATES = {"2026-10-19", "2026-10-20"}


def validate_manifest(manifest: dict, *,
                      allowed_categories: set[str],
                      configured_locations: dict[str, str]) -> None:
    if manifest.get("schema_version") != "chedraui-validated-run-manifest-v1":
        raise ValueError("Versión desconocida del manifiesto")
    if manifest.get("snapshot_type") != "multi_date_historical":
        raise ValueError("Manifiesto sin alcance histórico multifecha")
    if not isinstance(manifest.get("stores"), list) or len(manifest["stores"]) != 4:
        raise ValueError("Se requieren 4 tiendas")

    expected_ids = {"232", "173", "255", "76"}
    observed_ids = set()
    cases = 0
    total_products = 0
    total_prices = 0
    observed_dates = set()

    for store in manifest["stores"]:
        location_id = store.get("location_id")
        store_id = str(store.get("store_id"))
        day = str(store.get("capture_date"))
        if configured_locations.get(location_id) != store_id:
            raise ValueError(f"Ubicación/configuración no coincide: {location_id}")
        if store_id in observed_ids:
            raise ValueError("Tienda duplicada: " + store_id)
        observed_ids.add(store_id)
        if day not in EXPECTED_DATES:
            raise ValueError("Fecha fuera del rango validado")
        observed_dates.add(day)
        categories = store.get("categories")
        if not isinstance(categories, list) or len(categories) != 5:
            raise ValueError(f"Tienda {store_id}: se requieren 5 categorías")
        category_ids = [item.get("category_id") for item in categories]
        if set(category_ids) != allowed_categories or len(set(category_ids)) != 5:
            raise ValueError(f"Tienda {store_id}: categorías inesperadas")
        for item in categories:
            if item.get("status") != "COMPLETE":
                raise ValueError("El manifiesto contiene casos no COMPLETE")
            if item.get("store_context_contract_version") != CONTRACT:
                raise ValueError("Contratos de sucursal incompatibles")
            products = item.get("products")
            required = item.get("price_required")
            if (type(products) is not int or type(required) is not int
                or products <= 0 or required < 0 or required > products):
                raise ValueError("Conteos inválidos")
            total_products += products
            total_prices += required
            cases += 1

    if observed_ids != expected_ids or observed_dates != EXPECTED_DATES:
        raise ValueError("Tiendas o fechas diferentes de las verificadas")
    if (cases != 20
        or manifest.get("expected_total_categories") != 20
        or manifest.get("expected_total_products") != total_products
        or manifest.get("expected_total_required_prices") != total_prices):
        raise ValueError("Totales del manifiesto inconsistentes")
    if total_products != 5139 or total_prices != 5042:
        raise ValueError("Totales distintos de la evidencia original")


def build_historical_export(manifest: dict, snapshots_by_date: dict):
    """Return validated Consolidado, Resumen and Control without writing files."""
    parts = []
    summary_rows = []
    dates = set()
    seen = set()
    for store in manifest["stores"]:
        store_id = str(store["store_id"])
        day = store["capture_date"]
        dates.add(day)
        snapshots = snapshots_by_date.get(day, {})
        for item in store["categories"]:
            category_id = item["category_id"]
            key = ("Chedraui", category_id, store_id)
            if key in seen:
                raise ValueError("Caso duplicado: " + str(key))
            seen.add(key)
            if key not in snapshots:
                raise ValueError(f"Checkpoint faltante: {store_id}/{category_id} ({day})")
            frame, case = snapshots[key]
            errors = validate_case(
                frame, case,
                category_id=category_id, store_id=store_id, day=day,
            )
            if errors:
                raise ValueError(
                    f"Checkpoint rechazado {store_id}/{category_id}: "
                    + "; ".join(errors)
                )
            if str(case.get("store_context_method") or "") != "visible_store_control":
                raise ValueError(f"{store_id}/{category_id}: no se verificó encabezado")
            for metric in ("products", "target_products"):
                value = pd.to_numeric(pd.Series([case.get(metric)]), errors="coerce").iloc[0]
                if pd.isna(value) or int(value) != item["products"]:
                    raise ValueError(
                        f"Total {metric} diferente: {store_id}/{category_id}"
                    )
            required = pd.to_numeric(
                pd.Series([case.get("price_required")]), errors="coerce"
            ).iloc[0]
            complete = pd.to_numeric(
                pd.Series([case.get("price_required_complete")]), errors="coerce"
            ).iloc[0]
            if (pd.isna(required) or pd.isna(complete)
                or int(required) != item["price_required"]
                or int(complete) != item["price_required"]):
                raise ValueError(f"Precios requeridos inconsistentes: {store_id}/{category_id}")

            status = frame["availability_status"].fillna("").astype(str).str.upper()
            available = pd.to_numeric(frame["is_available"], errors="coerce")
            if not status.isin(["AVAILABLE", "UNAVAILABLE"]).all():
                raise ValueError(f"Disponibilidad desconocida: {store_id}/{category_id}")
            if not available.eq(status.eq("AVAILABLE").astype(int)).all():
                raise ValueError(f"Disponibilidad 1/0 incorrecta: {store_id}/{category_id}")

            # Preempt duplicates within and between files for the same store.
            if frame.duplicated(subset=["sku", "url"]).any():
                raise ValueError(f"SKU/URL duplicados: {store_id}/{category_id}")

            parts.append(_canonical(frame))
            row = dict(case)
            row.update({
                "retailer": "Chedraui", "store_id": store_id,
                "category_id": category_id, "status": "COMPLETE",
                "source_phase": "FASE 1",
                "capture_date": day,
                "integration_scope": "HISTORICAL_MULTI_DATE_NOT_DAILY_MASTER",
                "price_complete": int(
                    pd.to_numeric(frame["price_current"], errors="coerce").notna().sum()
                ),
            })
            summary_rows.append(row)

    combined = _canonical(pd.concat(parts, ignore_index=True))
    summary = pd.DataFrame(summary_rows)
    if len(seen) != 20 or len(combined) != manifest["expected_total_products"]:
        raise ValueError("Conteo histórico 20/20 o 5139 incorrecto")
    if set(combined["scrape_timestamp"].astype(str).str[:10]) != dates:
        raise ValueError("Fechas de filas diferentes del manifiesto")

    for store_id in ("232", "173", "255", "76"):
        subset = combined.loc[
            combined["store_id"].map(_store_id).eq(store_id)
        ]
        if subset.duplicated(subset=["category_id", "sku", "url"]).any():
            raise ValueError("Filas duplicadas entre categorías de tienda " + store_id)

    control = pd.DataFrame([{
        "status": "HISTORICAL_VALIDATED_MULTI_DATE",
        "retailer": "Chedraui",
        "store_count": 4,
        "cases": 20,
        "products": len(combined),
        "capture_dates": "|".join(sorted(dates)),
        "scope": "historical_snapshot_only",
        "is_daily_master": False,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "notes": (
            "4 tiendas y 20 categorías COMPLETE; no sustituye un master diario, "
            "no prueba capturas simultáneas y conserva las fechas originales."
        ),
    }])
    return combined, summary, control


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exporta 20 casos históricos Chedraui de 19 y 20 de octubre."
    )
    exclusive = parser.add_mutually_exclusive_group(required=True)
    exclusive.add_argument("--preview", action="store_true")
    exclusive.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        locations = {
            loc.id: str(loc.store_id) for loc in load_locations(ROOT / "config" / "locations.yaml")
            if loc.id.startswith("chedraui-")
        }
        validate_manifest(
            manifest,
            allowed_categories=_expected_categories(),
            configured_locations=locations,
        )
        evidence = {
            day: load_valid_snapshots(ROOT, day=day)
            for day in EXPECTED_DATES
        }
        concentrated, summary, control = build_historical_export(
            manifest, evidence
        )

        print("=" * 70)
        print("CEDHRAUI HISTÓRICO VALIDADO | 20 CASOS | 5,139 FILAS")
        print("=" * 70)
        for store in manifest["stores"]:
            print(
                f"{store['location_id']} ({store['store_id']}) "
                f"{store['capture_date']}: "
                f"{sum(c['products'] for c in store['categories'])} filas"
            )
        print("CAPTURAS: 2026-10-19 | 2026-10-20")
        print("MASTER DIARIO Y PUNTERO: sin cambios")
        print("ARCHIVO HISTÓRICO: " + str(OUTPUT))
        if args.preview:
            print("PREVIEW COMPLETE: nada fue modificado.")
            return 0
        if OUTPUT.exists():
            raise ValueError("El histórico ya existe: no se sobrescribe.")
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        _write_master(OUTPUT, concentrated, summary, control)
        print("HISTÓRICO VALIDADO PUBLICADO: " + str(OUTPUT))
        return 0
    except (ValueError, OSError, KeyError, TypeError, ImportError) as exc:
        print("ERROR: " + str(exc))
        print("No se publicó ni se modificó ningún master diario.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
