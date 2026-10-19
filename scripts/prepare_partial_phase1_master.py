from __future__ import annotations

"""Prepare a same-day master from validated Phase 1 non-Chedraui cases.

Chedraui remains pending. Existing phase integration commands then add
the fully validated Phase 2 and Phase 3 partitions.
Default mode is read-only; --commit creates the seed master and pointer.
"""

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

from integrate_validated_phase import (
    CHECKPOINT_DIR,
    CURRENT_MASTER_POINTER,
    _canonical,
    _control_capture_date,
    _frame_capture_dates,
    _master_path_for_date,
    _read_workbook,
    _validate_phase,
    _write_current_master_pointer,
    _write_master,
)
from scraper.quality_rules import meets_rate

ROOT = Path(__file__).resolve().parents[1]
PROGRESS = ROOT / "output" / "fase1_progress.xlsx"
SOURCES = {
    2: ROOT / "output" / "concentrado_fase2.xlsx",
    3: ROOT / "output" / "concentrado_fase3.xlsx",
}
REQUIRED = {"Farmacias del Ahorro": 6, "Ibarra Mayoreo": 4}


def _check_source(phase: int, path: Path, capture_date: str) -> int:
    if not path.is_file():
        raise ValueError("Falta archivo productivo Fase " + str(phase) + ": " + str(path))
    frame, summary, control = _read_workbook(path)
    errors = _validate_phase(phase, frame, summary, control)
    if errors:
        raise ValueError("Fase " + str(phase) + " inválida: " + "; ".join(errors))
    control_date = _control_capture_date(control)
    row_dates = _frame_capture_dates(frame)
    if control_date != capture_date or row_dates != {capture_date}:
        raise ValueError(
            "Fase " + str(phase) + " mezcla fechas o no es del día: "
            + str(control_date) + " / " + str(sorted(row_dates))
        )
    return len(frame)


def _validated_partial(capture_date: str):
    if not PROGRESS.is_file():
        raise ValueError("No existe el checkpoint parcial: " + str(PROGRESS))

    frame, summary, control = _read_workbook(PROGRESS)
    if control.empty or str(control.iloc[0].get("status")) != "PROGRESS":
        raise ValueError("Fase 1 progress: Control no indica PROGRESS.")
    if _control_capture_date(control) != capture_date:
        raise ValueError("Fase 1 progress no corresponde a " + capture_date)
    if _frame_capture_dates(frame) != {capture_date}:
        raise ValueError("Fase 1 progress tiene fechas de captura diferentes.")

    if not {"retailer", "category_id", "status", "products"}.issubset(summary.columns):
        raise ValueError("Fase 1 progress carece de columnas de resumen.")

    selected = summary.loc[summary["retailer"].isin(REQUIRED)].copy()
    for retailer, expected in REQUIRED.items():
        rows = selected.loc[selected["retailer"].eq(retailer)]
        if len(rows) != expected or rows["category_id"].nunique() != expected:
            raise ValueError(retailer + ": faltan categorías válidas (" + str(len(rows)) + "/" + str(expected) + ").")
        if not rows["status"].astype(str).str.upper().eq("COMPLETE").all():
            raise ValueError(retailer + ": existen categorías no COMPLETE.")

    valid_frames = []
    for _, item in selected.iterrows():
        retailer = str(item["retailer"])
        category = str(item["category_id"])
        subset = frame.loc[
            frame["retailer"].astype(str).eq(retailer)
            & frame["category_id"].astype(str).eq(category)
        ].copy()
        count = pd.to_numeric(item["products"], errors="coerce")
        if pd.isna(count) or int(count) <= 0 or len(subset) != int(count):
            raise ValueError(retailer + "/" + category + ": conteo inconsistente.")
        for column in ("price_current", "url"):
            if column not in subset.columns:
                raise ValueError("Falta " + column + " en " + retailer + "/" + category)
        prices = int(pd.to_numeric(subset["price_current"], errors="coerce").notna().sum())
        urls = int(subset["url"].fillna("").astype(str).str.strip().ne("").sum())
        if not meets_rate(prices, len(subset)) or not meets_rate(urls, len(subset)):
            raise ValueError(retailer + "/" + category + ": precio/URL insuficiente.")
        valid_frames.append(subset)

    result = _canonical(pd.concat(valid_frames, ignore_index=True))
    if result.empty:
        raise ValueError("Fase 1 sin registros válidos.")
    selected.insert(0, "source_phase", "FASE 1")
    return result, selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepara master parcial del día; Chedraui excluido.")
    parser.add_argument("--commit", action="store_true", help="Crear master y activar puntero.")
    args = parser.parse_args()

    capture_date = datetime.now().astimezone().date().isoformat()
    try:
        phase1, summary1 = _validated_partial(capture_date)
        counts = {phase: _check_source(phase, path, capture_date) for phase, path in SOURCES.items()}
    except (ValueError, KeyError, OSError) as exc:
        print("PREVALIDACIÓN RECHAZADA: " + str(exc))
        return 2

    master = _master_path_for_date(capture_date)
    print("FECHA: " + capture_date)
    print("FASE 1 (Ahorro + Ibarra): " + str(len(phase1)) + " filas; Chedraui excluido")
    print("FASE 2: " + str(counts[2]) + " filas válidas")
    print("FASE 3: " + str(counts[3]) + " filas válidas")
    print("TOTAL PREVISTO: " + str(len(phase1) + counts[2] + counts[3]))
    print("MASTER: " + str(master))

    if master.exists():
        print("DETENIDO: ya existe un master de esta fecha. No se sobrescribe.")
        print("Revisa su Control y el puntero antes de continuar.")
        return 3

    if not args.commit:
        print("PREVALIDACIÓN CORRECTA. No se modificó ningún archivo.")
        print("Ejecuta con --commit para crear el master inicial parcial.")
        return 0

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    control = pd.DataFrame([{
        "status": "CHECKPOINT_VALIDATED",
        "updated_at": generated_at,
        "last_phase_added": 1,
        "completed_phases": "",
        "accepted_partial_phases": "",
        "phase1_status": "PARTIAL_VALIDATED",
        "phase2_status": "PENDING",
        "phase3_status": "PENDING",
        "phase4_status": "PENDING",
        "phase5_status": "PENDING",
        "phase3_validated_retailers": "",
        "phase4_validated_retailers": "",
        "phase5_validated_retailers": "",
        "products": len(phase1),
        "categories": len(summary1),
        "execution_cases": len(summary1),
        "retailers": len(REQUIRED),
        "phase1_products": len(phase1),
        "phase2_products": 0,
        "phase3_products": 0,
        "phase4_products": 0,
        "phase5_products": 0,
        "capture_date": capture_date,
        "master": str(master),
    }])

    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    if CURRENT_MASTER_POINTER.exists():
        backup = CHECKPOINT_DIR / ("current_master_before_partial_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
        shutil.copy2(CURRENT_MASTER_POINTER, backup)
        print("Puntero anterior respaldado: " + str(backup))

    try:
        _write_master(master, phase1, summary1, control)
        checkpoint = CHECKPOINT_DIR / ("checkpoint_fase1_partial_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".xlsx")
        shutil.copy2(master, checkpoint)
        _write_current_master_pointer(
            master_path=master,
            capture_date=capture_date,
            completed_phases=[],
            accepted_partial_phases=[],
        )
    except Exception as exc:
        print("ERROR creando master parcial: " + str(exc))
        return 2

    print("MASTER PARCIAL PREPARADO: " + str(master))
    print("CHECKPOINT: " + str(checkpoint))
    print("Siguiente: integrar Fase 2 y Fase 3 con -AllowPartialPrevious.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
