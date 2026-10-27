"""Fase 6: integración YZA al master después de validar el piloto del DÍA.

No scrapea, no modifica las fases 1-5 ni integra Farmatodo.
Uso: python scripts/integrate_phase6_yza.py --preview
     python scripts/integrate_phase6_yza.py --commit
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / "scripts"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import pandas as pd

from scripts import run_phase6_pilot
from scripts.integrate_validated_phase import (
    CHECKPOINT_DIR, CURRENT_MASTER_POINTER, _canonical,
    _control_capture_date, _frame_capture_dates,
    _master_path_for_date, _read_existing_master, _write_master,
    _write_current_master_pointer,
)

RETAILER = "Farmacias YZA"
PHASE = 6
EXPECTED = frozenset({"medicamentos-respiratorio", "higiene-bucal"})
REPORT_DIR = ROOT / "output" / "reportes_integracion"


def _validate_frame(frame: pd.DataFrame, summary: pd.DataFrame, day: str) -> None:
    """Additional safeguards beyond Phase-6 pilot validations."""
    if frame.empty or len(summary) != 2:
        raise ValueError("YZA requiere exactamente dos categorías no vacías")
    if set(summary["category_id"].astype(str)) != EXPECTED:
        raise ValueError("Categorías YZA distintas del contrato")
    if set(frame["category_id"].astype(str)) != EXPECTED:
        raise ValueError("Faltan filas para una categoría YZA")
    if not frame["retailer"].eq(RETAILER).all():
        raise ValueError("Se encontraron filas de otro retailer")
    if _frame_capture_dates(frame) != {day}:
        raise ValueError("YZA mezcla fechas de captura")
    if frame["sku"].isna().any() or frame["sku"].astype(str).str.strip().eq("").any():
        raise ValueError("SKU incompleto")
    if frame.duplicated(subset=["category_id", "sku", "url"]).any():
        raise ValueError("Duplicados YZA")
    if not pd.to_numeric(frame["price_current"], errors="coerce").gt(0).all():
        raise ValueError("Precio no positivo o inválido")
    for url in frame["url"]:
        try:
            parsed = urlparse(str(url))
            if parsed.scheme != "https" or parsed.hostname not in {"www.yza.mx", "yza.mx"}:
                raise ValueError("URL YZA no válida")
        except (TypeError, ValueError) as exc:
            raise ValueError("URL YZA no válida") from exc
    for field in ("postal_code", "store", "store_id"):
        if field in frame and frame[field].notna().any():
            raise ValueError(f"YZA no puede atribuir {field} sin evidencia")
    if "store_context_verified" not in frame or not frame["store_context_verified"].eq(False).all():
        raise ValueError("YZA no valida localidad")
    if "status" not in summary or not summary["status"].astype(str).eq("SUCCESS").all():
        raise ValueError("Las dos categorías deben ser SUCCESS")
    counts = summary.set_index("category_id")["products"]
    for cat in EXPECTED:
        if len(frame.loc[frame["category_id"].eq(cat)]) != int(counts.loc[cat]):
            raise ValueError(f"Conteo inconsistente: {cat}")


def preview(day: str | None = None) -> dict:
    day = day or datetime.now().astimezone().date().isoformat()
    # Pilot must have been validated using its own workbook + diagnostics.
    run_phase6_pilot.validated_manifest()
    pilot = run_phase6_pilot.validate_capture("farmacias-yza", day)
    pointer_path = CURRENT_MASTER_POINTER
    if not pointer_path.is_file():
        raise ValueError("No existe el puntero al master productivo")
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    master = _master_path_for_date(day)
    if pointer.get("capture_date") != day:
        raise ValueError("El master activo es de otra fecha")
    raw = str(pointer.get("master") or "").strip()
    if not raw or Path(raw).resolve() != master.resolve():
        raise ValueError("El puntero activo no corresponde al master de hoy")
    completed = sorted({int(p) for p in pointer.get("completed_phases", [])})
    if not set(range(1, 6)).issubset(completed):
        raise ValueError("Las fases 1 a 5 deben estar COMPLETE antes de Fase 6")
    if not master.is_file():
        raise ValueError("No existe master productivo del día")
    old_frame, old_summary, old_control = _read_existing_master(master)
    if old_frame.empty or old_summary.empty or old_control.empty:
        raise ValueError("Master sin datos o pestañas requeridas")
    if _control_capture_date(old_control) != day or _frame_capture_dates(old_frame) != {day}:
        raise ValueError("Master con fechas de control o filas distintas del día")
    if "source_phase" not in old_summary:
        raise ValueError("Master sin source_phase")
    # The previous phases are immutable. A phase-6 re-run may replace only YZA.
    if old_frame.loc[~old_frame["retailer"].eq(RETAILER)].empty:
        raise ValueError("Master sin retailers anteriores a Fase 6")
    path = run_phase6_pilot.EXPECTED["farmacias-yza"]["workbook"]
    new_frame = pd.read_excel(path, sheet_name="Concentrado", dtype={"sku": str, "category_id": str})
    new_summary = pd.read_excel(path, sheet_name="Resumen", dtype={"category_id": str})
    _validate_frame(new_frame, new_summary, day)
    # Prevent silently using a manually altered report as evidence of coverage.
    if int(pilot["products"]) != len(new_frame) or int(pilot["categories"]) != 2:
        raise ValueError("Piloto y captura no coinciden")
    return {
        "capture_date": day, "master": master, "pointer": pointer,
        "old_frame": old_frame, "old_summary": old_summary, "old_control": old_control,
        "new_frame": new_frame, "new_summary": new_summary,
        "phase6_rows": len(new_frame),
        "will_replace_yza_rows": int(old_frame["retailer"].eq(RETAILER).sum()),
        "prior_phases": completed,
    }


def commit_plan(plan: dict) -> dict:
    """Atomic workbook write, backups and pointer rollback on failure."""
    day = plan["capture_date"]
    master: Path = plan["master"]
    old_frame, old_summary, control = plan["old_frame"], plan["old_summary"], plan["old_control"].copy()
    new_frame, new_summary = plan["new_frame"], plan["new_summary"]
    # Dedup/replacement only for phase-6 retailer, never touch phase-1..5 rows.
    frame = pd.concat([
        old_frame.loc[~old_frame["retailer"].eq(RETAILER)],
        _canonical(new_frame),
    ], ignore_index=True)
    if not frame.loc[~frame["retailer"].eq(RETAILER)].reset_index(drop=True).equals(
        old_frame.loc[~old_frame["retailer"].eq(RETAILER)].reset_index(drop=True)
    ):
        raise ValueError("Las filas de las fases 1–5 cambiaron")
    summary_add = new_summary.copy()
    summary_add["source_status"] = summary_add["status"]
    summary_add["status"] = "COMPLETE"
    summary_add["source_phase"] = "FASE 6"
    summary_add["integration_scope"] = "ONLINE_CATALOGUE_NO_LOCATION_VERIFICATION"
    summary = pd.concat([
        old_summary.loc[~old_summary["retailer"].eq(RETAILER)],
        summary_add,
    ], ignore_index=True, sort=False)
    phases = sorted(set(plan["prior_phases"]) | {6})
    control.loc[0, "phase6_status"] = "COMPLETE"
    control.loc[0, "phase6_products"] = plan["phase6_rows"]
    control.loc[0, "last_phase_added"] = 6
    control.loc[0, "completed_phases"] = "|".join(map(str, phases))
    control.loc[0, "products"] = len(frame)
    control.loc[0, "retailers"] = int(frame["retailer"].nunique())
    control.loc[0, "categories"] = len(summary[["retailer", "category_id"]].drop_duplicates())
    control.loc[0, "execution_cases"] = len(summary)
    control.loc[0, "updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")

    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    slug = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    master_backup = CHECKPOINT_DIR / f"before_phase6_yza_{slug}.xlsx"
    pointer_backup = CHECKPOINT_DIR / f"before_phase6_yza_{slug}.json"
    shutil.copy2(master, master_backup)
    shutil.copy2(CURRENT_MASTER_POINTER, pointer_backup)
    try:
        _write_master(master, _canonical(frame), summary, control)
        _write_current_master_pointer(
            master_path=master, capture_date=day, completed_phases=phases,
            accepted_partial_phases=plan["pointer"].get("accepted_partial_phases", []),
        )
    except Exception:
        # Preserve the original failure if recovery also fails.
        shutil.copy2(master_backup, master)
        shutil.copy2(pointer_backup, CURRENT_MASTER_POINTER)
        raise
    result = {
        "status": "COMPLETE", "phase": PHASE, "retailer": RETAILER,
        "capture_date": day, "rows": plan["phase6_rows"],
        "previous_phases_preserved": True, "master": str(master),
        "backup": str(master_backup), "pointer_backup": str(pointer_backup),
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / f"phase6_yza_{slug}.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Fase 6 YZA: preview o commit con gates")
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument("--preview", action="store_true")
    commands.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    try:
        plan = preview()
        if args.preview:
            print(json.dumps({
                "status": "VALIDATED_PREVIEW", "phase": PHASE,
                "master": str(plan["master"]), "rows": plan["phase6_rows"],
                "replaced": plan["will_replace_yza_rows"],
                "previous_phases_complete": plan["prior_phases"],
                "master_modified": False,
            }, ensure_ascii=False, indent=2))
            return 0
        print(json.dumps(commit_plan(plan), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"Fase 6 no integrada: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
