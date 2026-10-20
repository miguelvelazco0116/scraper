from __future__ import annotations

"""One-time guarded daily closure: 2026-10-19 Chedraui (3 of 4 stores).

This tool never fabricates inventory, merges other capture dates, certifies
Puebla, or marks Phase 1 COMPLETE. It runs on the owner's local Windows server.
"""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from chedraui_case_cache import load_valid_snapshots
from integrate_chedraui_store import _expected_categories, prepare_integration
from integrate_complete_retailers import _control
from integrate_validated_phase import (
    CHECKPOINT_DIR, CURRENT_MASTER_POINTER, _control_capture_date,
    _frame_capture_dates, _master_path_for_date,
    _read_current_master_pointer, _read_existing_master,
    _write_current_master_pointer, _write_master,
)

DAY = "2026-10-19"
STORES = (
    ("chedraui-polanco", "232", 1271),
    ("chedraui-tepeyac", "173", 1306),
    ("chedraui-cancun-kabah", "255", 1294),
)
EXPECTED_ROWS = 3871
REPORT_DIR = ROOT / "output" / "reportes_integracion"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def plan_closure(rows: pd.DataFrame, summary: pd.DataFrame,
                 snapshots: dict, *, day: str = DAY):
    """Validate all 3 stores before writing even one master row."""
    categories = _expected_categories()
    combined, combined_summary = rows.copy(), summary.copy()
    reports = []
    for slug, store_id, expected_count in STORES:
        combined, combined_summary, report = prepare_integration(
            combined, combined_summary, snapshots,
            store_id=store_id, day=day, category_ids=categories,
        )
        if report["cases"] != 5 or report["incoming_rows"] != expected_count:
            raise ValueError(
                f"{slug}: esperado 5 categorías/{expected_count} filas, "
                f"obtenido {report['cases']}/{report['incoming_rows']}"
            )
        reports.append(report)
    if sum(item["incoming_rows"] for item in reports) != EXPECTED_ROWS:
        raise ValueError("El cierre no coincide con las 3,871 filas validadas")
    target_mask = combined["retailer"].astype(str).eq("Chedraui") & (
        combined["store_id"].astype(str).str.replace(r"\.0$", "", regex=True)
        .isin([store_id for _, store_id, _ in STORES])
    )
    if int(target_mask.sum()) != EXPECTED_ROWS:
        raise ValueError("Chedraui: conteo final de tres tiendas diferente")
    if len(combined_summary.loc[
        combined_summary["retailer"].astype(str).eq("Chedraui")
        & combined_summary["store_id"].astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .isin([store_id for _, store_id, _ in STORES])
    ]) != 15:
        raise ValueError("Chedraui: Resumen final distinto de 15 casos")
    return combined, combined_summary, reports


def _write_report(payload: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = REPORT_DIR / f"cierre_chedraui_20261019_{stamp}.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _check_other_phase_states(old_control: pd.DataFrame, states: dict):
    old = old_control.iloc[0]
    for phase in (2, 3, 4, 5):
        col = f"phase{phase}_status"
        previous = str(old.get(col) or "").strip().upper()
        if previous and previous != "NAN" and previous != states[phase]:
            raise ValueError(
                f"Fase {phase}: no se permite alterar estado "
                f"{previous} a {states[phase]}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cierre 2026-10-19 de 15 casos Chedraui en 3 tiendas."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    today = datetime.now().astimezone().date().isoformat()
    if today != DAY:
        print(f"ERROR: cierre exclusivo del {DAY}; hoy es {today}.")
        return 2
    master = _master_path_for_date(DAY)
    if not master.is_file():
        print("ERROR: el master del 19 de octubre no existe. No se crea ni reemplaza.")
        return 2
    pointer = _read_current_master_pointer()
    if pointer is None or pointer.resolve() != master.resolve():
        print("ERROR: current_master.json no apunta al master del 19 de octubre.")
        return 2

    try:
        before_hash = _sha256(master)
        pointer_hash = _sha256(CURRENT_MASTER_POINTER)
        rows, summary, old_control = _read_existing_master(master)
        if old_control.empty or _control_capture_date(old_control) != DAY:
            raise ValueError("Fecha del Control del master incorrecta")
        if _frame_capture_dates(rows) != {DAY}:
            raise ValueError("El master contiene otras fechas de captura")

        snapshots = load_valid_snapshots(ROOT, day=DAY)
        combined, combined_summary, reports = plan_closure(
            rows, summary, snapshots,
        )
        control, completed, partial, states = _control(
            combined, combined_summary, DAY, 1, master
        )
        if states[1] != "PARTIAL_VALIDATED":
            raise ValueError("Fase 1 debe quedar PARTIAL_VALIDATED; Puebla pendiente")
        _check_other_phase_states(old_control, states)

        report = {
            "capture_date": DAY, "preview": bool(args.preview),
            "committed": False, "master": str(master),
            "stores": reports, "validated_cases": 15,
            "validated_rows": EXPECTED_ROWS,
            "master_before": len(rows), "master_after": len(combined),
            "phase_states": states,
        }
        print("=" * 72)
        print("CIERRE CHEDRAUI 2026-10-19 | 3 SUCURSALES | PUEBLA PENDIENTE")
        print("=" * 72)
        for slug, store_id, _ in STORES:
            detail = next(item for item in reports if item["store_id"] == store_id)
            print(
                f"{slug}: COMPLETE 5/5 | {detail['incoming_rows']} filas "
                f"| reemplazadas: {detail['replaced_rows']}"
            )
        print(f"TOTAL CHEDRAUI: 15/20 casos | {EXPECTED_ROWS} filas")
        print(f"MASTER ANTES: {len(rows)} | DESPUÉS: {len(combined)}")
        print("FASE 1: PARTIAL_VALIDATED")
        print("OTRAS FASES: conservan sus estados")

        if args.preview:
            print("PREVIEW: sin cambios al master.")
            print("REPORTE: " + str(_write_report(report)))
            return 0

        if _sha256(master) != before_hash or _sha256(CURRENT_MASTER_POINTER) != pointer_hash:
            raise ValueError("El master o el puntero cambiaron durante el preflight")

        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup = CHECKPOINT_DIR / f"before_cierre_chedraui_20261019_{stamp}.xlsx"
        pointer_backup = CHECKPOINT_DIR / f"pointer_before_cierre_chedraui_20261019_{stamp}.json"
        shutil.copy2(master, backup)
        shutil.copy2(CURRENT_MASTER_POINTER, pointer_backup)

        try:
            _write_master(master, combined, combined_summary, control)
            after_rows, after_summary, after_control = _read_existing_master(master)
            if (len(after_rows) != len(combined)
                or len(after_summary) != len(combined_summary)
                or _control_capture_date(after_control) != DAY):
                raise ValueError("Fallo de conciliación posterior a escritura")
            for _, store_id, expected_count in STORES:
                mask = (
                    after_rows["retailer"].astype(str).eq("Chedraui")
                    & after_rows["store_id"].astype(str)
                    .str.replace(r"\.0$", "", regex=True).eq(store_id)
                )
                if int(mask.sum()) != expected_count:
                    raise ValueError(f"Tienda {store_id}: total guardado incorrecto")
            _write_current_master_pointer(
                master_path=master, capture_date=DAY,
                completed_phases=completed,
                accepted_partial_phases=partial,
            )
        except Exception:
            # Restore original state only if it can be done safely.
            shutil.copy2(backup, master)
            shutil.copy2(pointer_backup, CURRENT_MASTER_POINTER)
            raise

        checkpoint = CHECKPOINT_DIR / f"checkpoint_cierre_chedraui_20261019_{stamp}.xlsx"
        shutil.copy2(master, checkpoint)
        report.update({
            "committed": True, "backup": str(backup),
            "pointer_backup": str(pointer_backup),
            "checkpoint": str(checkpoint),
        })
        print("INTEGRACIÓN COMPLETADA | 15/20 casos | 3871 filas CHEDRAUI")
        print("MASTER: " + str(master))
        print("RESPALDO: " + str(backup))
        print("CHECKPOINT: " + str(checkpoint))
        print("REPORTE: " + str(_write_report(report)))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("ERROR: " + str(exc))
        print("Cierre NO confirmado; revisar el master y el diagnóstico.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
