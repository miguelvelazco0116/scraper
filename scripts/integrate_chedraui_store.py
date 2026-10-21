from __future__ import annotations

"""Safely integrate one COMPLETE Chedraui store from same-day isolated checkpoints.

This does NOT certify the whole Chedraui retailer / Phase 1. It does not
scrape or modify any other store, retailer or phase.
"""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from chedraui_case_cache import MIN_COVERAGE, _store_id, load_valid_snapshots, validate_case
from integrate_complete_retailers import _control
from integrate_validated_phase import (
    CHECKPOINT_DIR, CURRENT_MASTER_POINTER, _canonical,
    _control_capture_date, _frame_capture_dates,
    _master_path_for_date, _read_current_master_pointer,
    _read_existing_master, _write_current_master_pointer, _write_master,
)
from scraper.config import load_categories, load_locations


REPORT_DIR = ROOT / "output" / "reportes_integracion"
RETAILER = "Chedraui"


def _case_mask(frame: pd.DataFrame, store_id: str,
               category_ids: set[str] | None = None) -> pd.Series:
    if not {"retailer", "store_id", "category_id"}.issubset(frame.columns):
        raise ValueError("Faltan columnas de partición del master")
    mask = frame["retailer"].astype(str).eq(RETAILER) & frame["store_id"].map(_store_id).eq(store_id)
    if category_ids is not None:
        mask &= frame["category_id"].astype(str).isin(category_ids)
    return mask


def _expected_categories() -> set[str]:
    categories = load_categories(
        ROOT / "config" / "chedraui" / "categories.yaml"
    )
    result = {category.id for category in categories}
    if len(result) != 5:
        raise ValueError("El catálogo Chedraui no contiene exactamente 5 categorías activas")
    return result


def prepare_integration(
    frame: pd.DataFrame, summary: pd.DataFrame, isolated: dict,
    *, store_id: str, day: str, category_ids: set[str],
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Pure in-memory plan. Raise before any master write if evidence is invalid."""
    required_summary = {"retailer", "store_id", "category_id", "source_phase", "status"}
    if not required_summary.issubset(summary.columns):
        raise ValueError("El Resumen actual no tiene las columnas exigidas")
    if not category_ids or len(category_ids) != 5:
        raise ValueError("Se requieren las cinco categorías configuradas")

    missing = sorted(
        category_ids - {
            category for retailer, category, store in isolated
            if retailer == RETAILER and store == store_id
        }
    )
    if missing:
        raise ValueError(
            "Checkpoint COMPLETE faltante para la tienda "
            + store_id + ": " + ", ".join(missing)
        )

    frames: list[pd.DataFrame] = []
    summaries: list[dict] = []
    for category_id in sorted(category_ids):
        subset, case = isolated[(RETAILER, category_id, store_id)]
        issues = validate_case(
            subset, case, store_id=store_id,
            category_id=category_id, day=day,
        )
        if issues:
            raise ValueError(category_id + ": " + "; ".join(issues))
        if str(case.get("store_context_method") or "").strip() != "visible_store_control":
            raise ValueError(category_id + ": falta verificación visual del encabezado")

        avail = subset["availability_status"].fillna("").astype(str).str.strip().str.upper()
        binary = pd.to_numeric(subset["is_available"], errors="coerce")
        if not avail.isin(["AVAILABLE", "UNAVAILABLE"]).all():
            raise ValueError(category_id + ": disponibilidad no comprobada")
        if not (binary.eq(1).eq(avail.eq("AVAILABLE"))).all():
            raise ValueError(category_id + ": disponibilidad 1/0 inconsistente")

        target = pd.to_numeric(pd.Series([case.get("target_products")]), errors="coerce").iloc[0]
        if (pd.isna(target) or target <= 0 or int(target) != target
            or not (MIN_COVERAGE <= len(subset) / int(target) <= 1.0)):
            raise ValueError(category_id + ": cobertura inferior a 90%, superior al total o target inválido")
        required = pd.to_numeric(pd.Series([case.get("price_required")]), errors="coerce").iloc[0]
        complete = pd.to_numeric(pd.Series([case.get("price_required_complete")]), errors="coerce").iloc[0]
        if pd.isna(required) or pd.isna(complete) or int(required) != int(complete):
            raise ValueError(category_id + ": no todos los precios requeridos están completos")

        frames.append(_canonical(subset))
        new_case = dict(case)
        new_case.update({
            "retailer": RETAILER, "store_id": store_id,
            "source_phase": "FASE 1", "phase": "FASE 1",
            "status": "COMPLETE",
            "integration_scope": "SINGLE_STORE_VALIDATED",
            "price_complete": int(
                pd.to_numeric(subset["price_current"], errors="coerce").notna().sum()
            ),
        })
        summaries.append(new_case)

    incoming = _canonical(pd.concat(frames, ignore_index=True))
    incoming_summary = pd.DataFrame(summaries)
    if len(incoming) != sum(int(item["products"]) for item in summaries):
        raise ValueError("Conteo total de checkpoints inconsistente")
    if incoming.duplicated(subset=["retailer", "store_id", "category_id", "sku", "url"]).any():
        raise ValueError("Existen SKU+URL duplicados dentro de la partición")

    # Replace only the 5 store/category partitions of THIS Chedraui store.
    # Refuse legacy orphan rows / unsupported categories that might be lost.
    store_rows_mask = _case_mask(frame, store_id)
    replaced_rows_mask = _case_mask(frame, store_id, category_ids)
    if store_rows_mask.sum() != replaced_rows_mask.sum():
        raise ValueError("El master tiene otras categorías de esta tienda: no se reemplazan a ciegas")
    store_summary_mask = _case_mask(summary, store_id)
    replaced_summary_mask = _case_mask(summary, store_id, category_ids)
    if store_summary_mask.sum() != replaced_summary_mask.sum():
        raise ValueError("El Resumen contiene otras categorías de esta tienda")

    previous_case_keys = summary.loc[replaced_summary_mask, "category_id"].astype(str)
    if previous_case_keys.duplicated().any():
        raise ValueError("El master tiene entradas de Resumen duplicadas para la tienda")

    retained_rows = frame.loc[~replaced_rows_mask].copy()
    retained_summary = summary.loc[~replaced_summary_mask].copy()
    combined = _canonical(pd.concat([retained_rows, incoming], ignore_index=True))
    combined_summary = pd.concat(
        [retained_summary, incoming_summary], ignore_index=True, sort=False
    )
    # No other rows/categories/retailers are filtered. Detect accidental loss.
    if len(combined) != len(frame) - int(replaced_rows_mask.sum()) + len(incoming):
        raise ValueError("Conciliación de filas incorrecta")
    if len(combined_summary) != len(summary) - int(replaced_summary_mask.sum()) + len(incoming_summary):
        raise ValueError("Conciliación de Resumen incorrecta")

    result = {
        "store_id": store_id,
        "capture_date": day,
        "categories": {item["category_id"]: int(item["products"]) for item in summaries},
        "cases": len(summaries),
        "incoming_rows": len(incoming),
        "replaced_rows": int(replaced_rows_mask.sum()),
        "previous_master_rows": len(frame),
        "new_master_rows": len(combined),
        "other_rows_preserved": len(retained_rows),
    }
    return combined, combined_summary, result


def _write_report(payload: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = REPORT_DIR / f"chedraui_store_{payload['store_id']}_{stamp}.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Integra únicamente 5 categorías COMPLETE de una sucursal Chedraui."
    )
    parser.add_argument("--store", required=True, choices=[
        "chedraui-polanco", "chedraui-tepeyac",
        "chedraui-cancun-kabah", "chedraui-puebla-centro-sur",
    ])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preview", action="store_true")
    group.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    day = datetime.now().astimezone().date().isoformat()
    locations = {item.id: item for item in load_locations(ROOT / "config" / "locations.yaml")}
    store_id = str(locations[args.store].store_id)
    master = _master_path_for_date(day)

    if not master.is_file():
        print("ERROR: master del día no existe; no se crea desde un solo retailer.")
        return 2
    pointer = _read_current_master_pointer()
    if pointer is None or pointer.resolve() != master.resolve():
        print("ERROR: master del día no coincide con current_master.json.")
        return 2

    try:
        frame, summary, control = _read_existing_master(master)
        if control.empty or _control_capture_date(control) != day:
            raise ValueError("Fecha/Control del master no válida")
        if _frame_capture_dates(frame) != {day}:
            raise ValueError("Master con productos de fechas diferentes")
        isolated = load_valid_snapshots(ROOT, day=day)
        combined, combined_summary, report = prepare_integration(
            frame, summary, isolated, store_id=store_id,
            day=day, category_ids=_expected_categories(),
        )
        master_control, completed, partial, states = _control(
            combined, combined_summary, day, 1, master
        )
        if states[1] != "PARTIAL_VALIDATED":
            raise ValueError("Fase 1 debe permanecer PARTIAL_VALIDATED hasta tener 20 casos")
        report.update({
            "preview": args.preview,
            "master": str(master),
            "phase_states": states,
            "completed_phases": completed,
            "accepted_partial_phases": partial,
        })
        print("CHEDRAUI TIENDA " + store_id + " | " + day)
        print("CATEGORÍAS: " + json.dumps(report["categories"], ensure_ascii=False))
        print("FILAS VALIDADAS: " + str(report["incoming_rows"]))
        print("FILAS REEMPLAZADAS: " + str(report["replaced_rows"]))
        print("MASTER ANTES: " + str(report["previous_master_rows"]))
        print("MASTER DESPUÉS: " + str(report["new_master_rows"]))
        print("FASE 1: " + states[1])

        if args.preview:
            report["committed"] = False
            path = _write_report(report)
            print("PREVIEW SIN CAMBIOS: " + str(path))
            return 0

        # Immutable before-image of both the master and active pointer.
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup = CHECKPOINT_DIR / f"before_chedraui_store{store_id}_{stamp}.xlsx"
        pointer_backup = CHECKPOINT_DIR / f"pointer_before_chedraui_store{store_id}_{stamp}.json"
        shutil.copy2(master, backup)
        shutil.copy2(CURRENT_MASTER_POINTER, pointer_backup)

        try:
            _write_master(master, combined, combined_summary, master_control)
            # Post-write: read actual on-disk data before updating active pointer.
            saved_rows, saved_summary, saved_control = _read_existing_master(master)
            actual = _case_mask(saved_rows, store_id, _expected_categories())
            actual_summary = _case_mask(saved_summary, store_id, _expected_categories())
            if (len(saved_rows) != len(combined)
                or int(actual.sum()) != int(report["incoming_rows"])
                or int(actual_summary.sum()) != 5
                or _control_capture_date(saved_control) != day):
                raise ValueError("Validación posterior a escritura no pasó")
            _write_current_master_pointer(
                master_path=master, capture_date=day,
                completed_phases=completed, accepted_partial_phases=partial,
            )
        except Exception:
            shutil.copy2(backup, master)
            shutil.copy2(pointer_backup, CURRENT_MASTER_POINTER)
            raise

        checkpoint = CHECKPOINT_DIR / f"checkpoint_chedraui_store{store_id}_{stamp}.xlsx"
        shutil.copy2(master, checkpoint)
        report.update({
            "committed": True, "backup": str(backup),
            "pointer_backup": str(pointer_backup),
            "checkpoint": str(checkpoint),
        })
        path = _write_report(report)
        print("INTEGRACIÓN COMPLETADA")
        print("MASTER: " + str(master))
        print("BACKUP: " + str(backup))
        print("CHECKPOINT: " + str(checkpoint))
        print("REPORTE: " + str(path))
        return 0
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print("ERROR: " + str(exc))
        print("Master sin integración válida para esta ejecución.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
