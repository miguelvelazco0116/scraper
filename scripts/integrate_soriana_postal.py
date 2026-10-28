"""Soriana postal rotation -> same-day productive master (guarded, opt-in).

Replaces only the Soriana rows and its Fase-2 Resumen with 24 CP/category
cases. Never backfills another date or changes Farmacias Guadalajara, any
other retailer, other phase status, or the pilot XLSX files. --preview is
read-only; --commit is explicit and creates backup+rollback evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS
from scraper.soriana_postal_contract import (
    CODES, CATEGORIES, EXPECTED, PHASE, RETAILER, SCOPE, postal_issues, postal_text,
)
from scraper.capture_progress import _write_json_atomic
from scripts.audit_soriana_cp_rotation import audit
from scripts.integrate_validated_phase import (
    _canonical, _control_capture_date, _frame_capture_dates,
    _read_existing_master, _write_master,
)

PILOT_RELATIVE = Path("output") / "pilots" / "soriana_cp_rotation"
CHECKPOINT_RELATIVE = Path("output") / "checkpoints_validos"
REPORT_RELATIVE = Path("output") / "reportes_integracion"


def _hash(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _master_paths(root: Path, day: str):
    checkpoint = root / CHECKPOINT_RELATIVE
    master = root / "output" / f"concentrado_productivo_total_{day}.xlsx"
    pointer = checkpoint / "current_master.json"
    return master, pointer, checkpoint


def _validate_sources(root: Path, day: str):
    """Re-run the independent 24-case audit, then re-read the actual Excel data."""
    directory = root / PILOT_RELATIVE
    results = audit(day, output_dir=directory)
    if (len(results) != len(EXPECTED)
        or any(r["status"] != "COMPLETE" for r in results)):
        invalid = [f"{r['cp']}/{r['category']}: {r['issue']}" for r in results
                   if r["status"] != "COMPLETE"]
        raise ValueError("Soriana sin auditoría 24/24: " + "; ".join(invalid[:8]))

    frames, summaries, hashes = [], [], {}
    for cp in CODES:
        path = directory / f"soriana_{cp}.xlsx"
        hashes[str(path)] = _hash(path)
        frame = pd.read_excel(
            path, sheet_name="Concentrado",
            dtype={"sku": str, "store_id": str, "postal_code": str, "category_id": str},
        )
        summary = pd.read_excel(
            path, sheet_name="Resumen",
            dtype={"postal_code": str, "category_id": str},
        )
        # Ignore optional experimental Gastrointestinales: only the six
        # independently audited production categories enter the master.
        if not {"category_id", "postal_code"}.issubset(frame) or not {"category_id", "postal_code"}.issubset(summary):
            raise ValueError("Archivo CP carece de campos obligatorios")
        frame = frame.loc[frame["category_id"].isin(CATEGORIES)].copy()
        summary = summary.loc[summary["category_id"].isin(CATEGORIES)].copy()
        frame["postal_code"] = postal_text(frame["postal_code"])
        summary["postal_code"] = postal_text(summary["postal_code"])
        if not frame["postal_code"].eq(cp).all() or not summary["postal_code"].eq(cp).all():
            raise ValueError(f"Archivo del CP {cp} contiene otra ubicación")
        frames.append(frame)
        summaries.append(summary)

    all_rows = pd.concat(frames, ignore_index=True, sort=False)
    all_cases = pd.concat(summaries, ignore_index=True, sort=False)
    all_cases["retailer"] = RETAILER
    all_cases["source_phase"] = PHASE
    all_cases["integration_scope"] = SCOPE
    all_cases["source_status"] = all_cases["status"]
    # This is delivery-postal context, not a certified physical store.
    issues = postal_issues(all_rows, all_cases, day)
    if issues:
        raise ValueError("Contrato Soriana no conforme: " + "; ".join(issues))
    if sum(int(x["products"]) for x in results) != len(all_rows):
        raise ValueError("La auditoría independiente y las filas no coinciden")
    all_rows = _canonical(all_rows)
    all_rows["postal_code"] = postal_text(all_rows["postal_code"])
    return all_rows, all_cases, hashes


def source_preview(root: Path, day: str) -> dict:
    """Validate historic Soriana XLSX files, without requiring or writing master.

    Explicitly NOT an eligibility check for productive integration. Suitable
    for independently reviewing archived captures after the active master
    has moved to a different date.
    """
    incoming, cases, hashes = _validate_sources(root, day)
    _, pointer_path, _ = _master_paths(root, day)
    master_day = None
    if pointer_path.is_file():
        try:
            master_day = json.loads(
                pointer_path.read_text(encoding="utf-8")
            ).get("capture_date")
        except (OSError, ValueError, TypeError):
            pass
    return {
        "status": "SORIANA_SOURCE_VALIDATED",
        "capture_date": day,
        "postal_codes": list(CODES),
        "postal_cases": len(cases),
        "products_validated": len(incoming),
        "master_active_date": master_day,
        "master_integration_checked": False,
        "master_modified": False,
        "source_workbooks": list(hashes),
        "note": "Solo se auditaron los cuatro Excel; no se verifico ni altero el master.",
    }


def prepare_plan(root: Path, day: str) -> dict:
    """Read-only preview; rejects missing or mismatched active-day masters."""
    if len(day) != 10 or day[4] != "-" or day[7] != "-":
        raise ValueError("Fecha inválida; usa YYYY-MM-DD")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Fecha inválida; usa YYYY-MM-DD") from exc
    master, pointer_path, checkpoint = _master_paths(root, day)
    if not pointer_path.is_file():
        raise ValueError("No existe puntero productivo: primero genera el master de la fecha")
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    if pointer.get("capture_date") != day:
        raise ValueError(
            f"Master activo fechado {pointer.get('capture_date')!r}; "
            f"los archivos Soriana son de {day}. "
            "No se permite mezclar capturas. Usa SourcePreview para "
            "validar los cuatro Excel históricos sin tocar el master."
        )
    # Fail quickly on mismatched days before reading potentially large Excel
    # sources; preview should tell the operator the true blocker.
    incoming, cases, source_hashes = _validate_sources(root, day)
    raw = str(pointer.get("master") or "").strip()
    if not raw:
        raise ValueError("Puntero productivo sin ruta de master")
    reported = Path(raw)
    if not reported.is_absolute():
        reported = root / reported
    if reported.resolve() != master.resolve() or not master.is_file():
        raise ValueError("Master activo no corresponde al archivo de la fecha")
    completed = {int(p) for p in pointer.get("completed_phases", [])}
    if not {1, 2}.issubset(completed):
        raise ValueError("Fases 1 y 2 deben estar COMPLETE antes de sustituir Soriana")
    old_frame, old_summary, old_control = _read_existing_master(master)
    if old_frame.empty or old_summary.empty or old_control.empty:
        raise ValueError("Master sin Concentrado, Resumen o Control válidos")
    if _control_capture_date(old_control) != day or _frame_capture_dates(old_frame) != {day}:
        raise ValueError("Master mezcla fechas o Control de otra fecha")
    if set(old_frame["retailer"].astype(str)).isdisjoint({"Farmacias Guadalajara", RETAILER}):
        raise ValueError("Master sin ninguna partición de Fase 2")
    if "source_phase" not in old_summary:
        raise ValueError("Resumen sin origen de fase")
    # The old Fase 2 needs to be COMPLETE by evidence, not just pointer.
    from scripts.audit_master_phases import inventory
    phases, retailers, _, _ = inventory(master)
    old_p2 = phases.loc[phases["fase"].eq(2)]
    if len(old_p2) != 1 or old_p2.iloc[0]["estado_calculado"] != "COMPLETE":
        raise ValueError("Auditoría del master indica que Fase 2 no está COMPLETE")
    if old_p2.iloc[0]["estado_control"] != "COMPLETE":
        raise ValueError("Control de Fase 2 no es COMPLETE")

    non_soriana = old_frame.loc[~old_frame["retailer"].astype(str).eq(RETAILER)].copy()
    kept_summary = old_summary.loc[~(
        old_summary["retailer"].astype(str).eq(RETAILER)
        & old_summary["source_phase"].astype(str).eq(PHASE)
    )].copy()
    if old_summary.loc[old_summary["retailer"].astype(str).eq(RETAILER)
                       & ~old_summary["source_phase"].astype(str).eq(PHASE)].shape[0]:
        raise ValueError("Soriana tiene entradas ajenas a Fase 2; revisión manual")
    combined = pd.concat([non_soriana, incoming], ignore_index=True)
    combined = _canonical(combined)
    combined["postal_code"] = combined["postal_code"].where(
        ~combined["retailer"].astype(str).eq(RETAILER),
        postal_text(combined["postal_code"]),
    )
    combined_summary = pd.concat([kept_summary, cases], ignore_index=True, sort=False)
    # pandas concat can upcast otherwise unchanged numeric/string columns.
    # Compare actual values, not the incidental resulting DataFrame dtypes.
    try:
        pd.testing.assert_frame_equal(
            combined.loc[~combined["retailer"].astype(str).eq(RETAILER)].reset_index(drop=True),
            non_soriana.reset_index(drop=True),
            check_dtype=False, check_exact=True,
        )
    except AssertionError as exc:
        raise ValueError("Intento de alterar filas de otros retailers") from exc
    issues = postal_issues(combined, combined_summary, day)
    if issues:
        raise ValueError("La partición Soriana agregada no certifica los 24 casos: "
                         + "; ".join(issues))
    if len(combined) != len(non_soriana) + len(incoming):
        raise ValueError("Conciliación falló: cardinalidad de productos")

    control = old_control.copy()
    control.loc[0, "products"] = len(combined)
    control.loc[0, "phase2_products"] = int(combined["retailer"].isin(["Soriana", "Farmacias Guadalajara"]).sum())
    control.loc[0, "categories"] = len(combined_summary[["retailer", "category_id"]].drop_duplicates())
    control.loc[0, "execution_cases"] = len(combined_summary)
    control.loc[0, "retailers"] = int(combined["retailer"].nunique())
    control.loc[0, "updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    control.loc[0, "soriana_postal_cases"] = len(cases)
    control.loc[0, "soriana_postal_products"] = len(incoming)
    control.loc[0, "soriana_postal_scope"] = SCOPE

    return {
        "day": day, "master": master, "pointer": pointer_path, "checkpoint": checkpoint,
        "master_sha256": _hash(master), "pointer_sha256": _hash(pointer_path),
        "source_hashes": source_hashes,
        "old_soriana_rows": len(old_frame) - len(non_soriana),
        "new_soriana_rows": len(incoming),
        "old_total": len(old_frame), "new_total": len(combined),
        "other_retailers_preserved": len(non_soriana),
        "frame": combined, "summary": combined_summary, "control": control,
    }


def plan_report(plan: dict) -> dict:
    return {
        "status": "VALIDATED_PREVIEW", "date": plan["day"],
        "old_soriana_rows": plan["old_soriana_rows"],
        "new_soriana_rows": plan["new_soriana_rows"],
        "old_master_rows": plan["old_total"], "new_master_rows": plan["new_total"],
        "other_retailer_rows_preserved": plan["other_retailers_preserved"],
        "postal_cases": 24, "master": str(plan["master"]),
        "master_modified": False, "requires_explicit_commit": True,
    }


def commit_plan(root: Path, plan: dict) -> dict:
    """Atomic write, complete independent post-audit, and rollback on failure."""
    if datetime.now().astimezone().date().isoformat() != plan["day"]:
        raise ValueError("Commit de Soriana solamente permitido en la fecha local de captura")
    from dashboard.execution import current_active_job
    if current_active_job(root):
        raise ValueError("Hay un job activo de Streamlit; cierra la captura antes de integrar")
    master, pointer_path = plan["master"], plan["pointer"]
    if _hash(master) != plan["master_sha256"] or _hash(pointer_path) != plan["pointer_sha256"]:
        raise ValueError("Master/puntero cambió durante preview: vuelve a ejecutar")
    for name, sha in plan["source_hashes"].items():
        if _hash(Path(name)) != sha:
            raise ValueError("Un Excel del piloto cambió durante preview")
    checkpoint = plan["checkpoint"]
    checkpoint.mkdir(parents=True, exist_ok=True)
    tag = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
    backup = checkpoint / f"before_soriana_postal_{tag}.xlsx"
    pointer_backup = checkpoint / f"before_soriana_postal_{tag}.json"
    shutil.copy2(master, backup)
    shutil.copy2(pointer_path, pointer_backup)
    try:
        _write_master(master, plan["frame"], plan["summary"], plan["control"])
        # Pointer is deliberately unchanged: day, phases and master identity stay constant.
        from scripts.audit_master_phases import inventory
        phases, retailers, _, facts = inventory(master)
        row = retailers.loc[
            retailers["retailer"].astype(str).eq(RETAILER)
            & retailers["fase"].eq(2)
        ]
        phase = phases.loc[phases["fase"].eq(2)]
        if (len(row) != 1 or row.iloc[0]["estado"] != "INTEGRADO_COMPLETE"
            or int(row.iloc[0]["casos_master"]) != 24
            or int(row.iloc[0]["filas_master"]) != plan["new_soriana_rows"]
            or len(phase) != 1 or phase.iloc[0]["estado_calculado"] != "COMPLETE"
            or not bool(phase.iloc[0]["control_coincide"])):
            raise ValueError("Auditoría posterior del master no certificó la partición Soriana")
        if _hash(pointer_path) != plan["pointer_sha256"]:
            raise ValueError("El puntero productivo cambió sin autorización")
    except Exception:
        # Always restore BOTH files even if the post-write audit failed.
        shutil.copy2(backup, master)
        shutil.copy2(pointer_backup, pointer_path)
        raise
    result = dict(plan_report(plan))
    result.update(
        status="COMMITTED", master_modified=True, backup=str(backup),
        pointer_backup=str(pointer_backup),
    )
    reports = root / REPORT_RELATIVE
    reports.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(reports / f"soriana_postal_{plan['day']}_{tag}.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Soriana postal: preview o commit protegido")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--preview", action="store_true", help="Validar sin escribir master")
    modes.add_argument("--source-preview", action="store_true", help="Auditar XLSX históricos sin master")
    modes.add_argument("--commit", action="store_true", help="Integrar con backup + auditoría + rollback")
    parser.add_argument("--day", default=datetime.now().astimezone().date().isoformat())
    args = parser.parse_args()
    try:
        if args.source_preview:
            response = source_preview(ROOT, args.day)
        else:
            plan = prepare_plan(ROOT, args.day)
            response = plan_report(plan) if args.preview else commit_plan(ROOT, plan)
        print(json.dumps(response, ensure_ascii=False, indent=2, default=str), flush=True)
        return 0
    except Exception as exc:
        print("SORIANA NO INTEGRADA: " + str(exc), file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
