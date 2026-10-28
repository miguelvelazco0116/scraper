"""Integrate already captured Phase 2 data without launching a scraper.

Reads Farmacias Guadalajara's existing workbook and Soriana's 4 independently
audited postal workbooks. All writes are to the same-day productive master,
with backup, hash recheck, atomic replacement and rollback. A partially
validated Phase 2 may be saved, but never marked COMPLETE.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pandas as pd

from scraper.soriana_postal_contract import (
    RETAILER, SCOPE, postal_issues, postal_text,
)
from scripts.integrate_soriana_postal import _hash, _validate_sources
from scripts.integrate_complete_retailers import (
    _quality, _phase_states, _date_check, _read,
)
from scripts.run_phase2_production import _enabled_category_ids, _validate_fg
from scripts.integrate_validated_phase import (
    _canonical, _control_capture_date, _frame_capture_dates,
    _read_existing_master, _write_master, _retailer_category_count,
)
from scraper.io_utils import atomic_output_path

FG = "Farmacias Guadalajara"
FG_FILE = Path("output") / "farmacias_guadalajara_phase2_test.xlsx"
POINTER = Path("output") / "checkpoints_validos" / "current_master.json"
PHASE = "FASE 2"


def read_fg(root: Path, day: str):
    """Only accept a complete and same-day, independently validated FG file."""
    path = root / FG_FILE
    if not path.is_file():
        raise ValueError("Falta Excel de Farmacias Guadalajara")
    frame, source_summary, control = _read(path)
    _date_check(frame, control, day)
    categories = _enabled_category_ids("farmacias-guadalajara")
    if len(categories) != 4:
        raise ValueError("Configuración Farmacias Guadalajara: esperadas 4 categorías")
    rows = _validate_fg(frame, source_summary, categories)
    cases = pd.DataFrame(rows)
    if cases.empty or "source_status" not in cases:
        raise ValueError("Farmacias Guadalajara carece de resumen verificado")
    if not cases["source_status"].astype(str).str.upper().eq("SUCCESS").all():
        raise ValueError("Farmacias Guadalajara aún no termina correctamente")
    _quality(2, FG, frame, cases)
    cases["retailer"] = FG
    cases["source_phase"] = PHASE
    cases["integration_scope"] = "STANDARD_VERIFIED"
    return _canonical(frame), cases, {str(path): _hash(path)}


def prepare_plan(root: Path, day: str) -> dict:
    """Read-only validation: Phase 1 and today's pointer must already exist."""
    if datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d") != day:
        raise ValueError("Fecha inválida")
    if day != datetime.now().astimezone().date().isoformat():
        raise ValueError("Solo se integra la fecha local actual")
    pointer_path = root / POINTER
    if not pointer_path.is_file():
        raise ValueError("Primero integra Fase 1; no existe master productivo")
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    master = root / "output" / f"concentrado_productivo_total_{day}.xlsx"
    active = Path(str(pointer.get("master") or ""))
    if not active.is_absolute():
        active = root / active
    if (pointer.get("capture_date") != day
        or active.resolve() != master.resolve() or not master.is_file()):
        raise ValueError(
            f"Master activo: {pointer.get('capture_date')!r}; "
            f"captura solicitada: {day}. Primero integra Fase 1 de hoy."
        )
    if 1 not in [int(x) for x in pointer.get("completed_phases", [])]:
        raise ValueError("Fase 1 no figura COMPLETE en el puntero del master")
    old_frame, old_summary, old_control = _read_existing_master(master)
    if old_frame.empty or old_summary.empty or old_control.empty:
        raise ValueError("Master sin pestañas necesarias")
    if (_control_capture_date(old_control) != day
        or _frame_capture_dates(old_frame) != {day}):
        raise ValueError("El master contiene capturas de otra fecha")
    from scripts.audit_master_phases import inventory
    phases, _, _, _ = inventory(master)
    phase1 = phases.loc[phases["fase"].eq(1)]
    if (len(phase1) != 1 or phase1.iloc[0]["estado_calculado"] != "COMPLETE"
        or not bool(phase1.iloc[0]["control_coincide"])):
        raise ValueError("Fase 1 requiere auditoría COMPLETE antes de integrar Fase 2")

    additions, failures, already, hashes, retailers = {}, {}, {}, {}, {}
    fg_exists = old_frame["retailer"].astype(str).eq(FG)
    if fg_exists.any():
        already[FG] = int(fg_exists.sum())
        retailers[FG] = {"status": "YA_EXISTE_EN_MASTER",
                         "products": already[FG], "detail": "Ya integrado, sin cambios."}
    else:
        try:
            frame, cases, fg_hashes = read_fg(root, day)
            additions[FG] = (frame, cases)
            hashes.update(fg_hashes)
            retailers[FG] = {"status": "LISTO_PARA_INTEGRAR",
                             "products": len(frame), "detail": "Captura COMPLETE del día."}
        except (ValueError, KeyError, OSError, TypeError) as exc:
            failures[FG] = str(exc)

    soriana_exists = old_frame["retailer"].astype(str).eq(RETAILER)
    from scraper.soriana_postal_contract import is_postal_rotation_summary
    soriana_certified = (soriana_exists.any()
                         and is_postal_rotation_summary(old_summary)
                         and not postal_issues(old_frame, old_summary, day))
    if soriana_certified:
        already[RETAILER] = int(soriana_exists.sum())
        retailers[RETAILER] = {
            "status": "YA_EXISTE_EN_MASTER", "products": already[RETAILER],
            "detail": "24 casos postales certificados y ya integrados.",
        }
    else:
        try:
            frame, cases, soriana_hashes = _validate_sources(root, day)
            issues = postal_issues(frame, cases, day)
            if issues:
                raise ValueError("Rotación postal no validada: " + "; ".join(issues))
            additions[RETAILER] = (frame, cases)
            hashes.update(soriana_hashes)
            retailers[RETAILER] = {
                "status": ("LISTO_PARA_ACTUALIZAR" if soriana_exists.any()
                           else "LISTO_PARA_INTEGRAR"),
                "products": len(frame),
                "detail": ("Sustituirá los 6 casos legacy por 24 CP/categoría."
                           if soriana_exists.any() else "24 casos postales COMPLETE."),
            }
        except (ValueError, KeyError, OSError, TypeError) as exc:
            failures[RETAILER] = str(exc)

    for name in (FG, RETAILER):
        if name in failures:
            reason = failures[name]
            missing = ("falta excel", "no existe", "no encontrado",
                       "no se encontraron", "archivo no generado", "no hay")
            state = ("SIN_CAPTURA" if any(t in reason.casefold() for t in missing)
                     else "CAPTURA_NO_VALIDADA")
            retailers[name] = {"status": state, "products": 0, "detail": reason}
        item = retailers[name]
        print(f"RETAILER {name} | {item['status']} | "
              f"{item['products']} productos | {item['detail']}", flush=True)

    combined = old_frame.copy()
    summary = old_summary.copy()
    if "source_phase" not in summary:
        raise ValueError("Master sin origen de fase en Resumen")
    # Existing partitions of unaffected retailers remain untouched. If only one
    # source is complete, preserve the other same-day partition if any.
    for retailer, (frame, cases) in additions.items():
        combined = combined.loc[~combined["retailer"].astype(str).eq(retailer)].copy()
        summary = summary.loc[~(
            summary["retailer"].astype(str).eq(retailer)
            & summary["source_phase"].astype(str).eq(PHASE)
        )].copy()
        combined = pd.concat([combined, frame], ignore_index=True, sort=False)
        summary = pd.concat([summary, cases], ignore_index=True, sort=False)
    combined = _canonical(combined)
    if RETAILER in additions:
        combined["postal_code"] = combined["postal_code"].where(
            ~combined["retailer"].astype(str).eq(RETAILER),
            postal_text(combined["postal_code"]),
        )
    if "integration_scope" in summary:
        soriana_cases = summary.loc[
            summary["retailer"].astype(str).eq(RETAILER)
            & summary["source_phase"].astype(str).eq(PHASE)
        ]
        if not soriana_cases.empty and SCOPE in set(
            soriana_cases["integration_scope"].dropna().astype(str)
        ):
            issues = postal_issues(combined, summary, day)
            if issues:
                raise ValueError("Soriana postal inválida en master: " + "; ".join(issues))

    protected = ~old_frame["retailer"].astype(str).isin(additions)
    original = old_frame.loc[protected].reset_index(drop=True)
    after = combined.loc[~combined["retailer"].astype(str).isin(additions)].reset_index(drop=True)
    pd.testing.assert_frame_equal(
        _canonical(original), _canonical(after), check_dtype=False, check_exact=True,
    )
    if len(combined) != len(after) + sum(len(x[0]) for x in additions.values()):
        raise ValueError("Falló conciliación de las filas productivas")

    completed, states = _phase_states(combined, summary)
    control = old_control.copy()
    control.loc[0, "updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    control.loc[0, "last_phase_added"] = 2
    control.loc[0, "completed_phases"] = "|".join(map(str, completed))
    accepted = [p for p, state in states.items() if state == "PARTIAL_VALIDATED"]
    control.loc[0, "accepted_partial_phases"] = "|".join(map(str, accepted))
    control.loc[0, "products"] = len(combined)
    control.loc[0, "categories"] = _retailer_category_count(summary)
    control.loc[0, "execution_cases"] = len(summary)
    control.loc[0, "retailers"] = int(combined["retailer"].nunique())
    control.loc[0, "capture_date"] = day
    control.loc[0, "master"] = str(master)
    control.loc[0, "phase2_status"] = states[2]
    control.loc[0, "phase2_products"] = int(combined["retailer"].isin([FG, RETAILER]).sum())
    if RETAILER in additions:
        control.loc[0, "soriana_postal_cases"] = 24
        control.loc[0, "soriana_postal_products"] = len(additions[RETAILER][0])
        control.loc[0, "soriana_postal_scope"] = SCOPE
    # A previously present Phase 3..5 must remain correctly classified.
    for phase in (3, 4, 5):
        control.loc[0, f"phase{phase}_status"] = states[phase]
    return dict(
        day=day, master=master, pointer_path=pointer_path,
        hashes=hashes, master_hash=_hash(master), pointer_hash=_hash(pointer_path),
        before_rows=len(old_frame), after_rows=len(combined), additions=additions,
        already=already, failures=failures, retailers=retailers,
        frame=combined, summary=summary, control=control,
        completed=completed, partial=accepted, states=states,
    )


def report(plan: dict, status: str, *, backup: str | None = None) -> dict:
    return {
        "status": status, "capture_date": plan["day"],
        "master": str(plan["master"]),
        "rows_before": plan["before_rows"], "rows_after": plan["after_rows"],
        "integrated_sources": {name: len(data[0]) for name, data in plan["additions"].items()},
        "already_in_master": plan["already"],
        "pending_sources": plan["failures"],
        "retailers": plan["retailers"],
        "message": ("No hay capturas nuevas; master SIN CAMBIOS."
                    if not plan["additions"] else
                    "Solo se integrarán retailers faltantes o la rotación postal pendiente."),
        "phase2_status": plan["states"][2],
        "completed_phases": plan["completed"],
        "master_modified": status == "COMMITTED",
        "backup": backup,
    }


def write_pointer(pointer_path: Path, master: Path, day: str,
                  completed: list[int], accepted_partial: list[int]) -> None:
    payload = {
        "master": str(master), "capture_date": day,
        "completed_phases": completed,
        "accepted_partial_phases": accepted_partial,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    with atomic_output_path(pointer_path) as temporary:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def commit(root: Path, plan: dict) -> dict:
    if plan["day"] != datetime.now().astimezone().date().isoformat():
        raise ValueError("Cambió el día local; prohibido integrar capturas históricas")
    if not plan["additions"]:
        return report(plan, "SIN_CAMBIOS")
    master, pointer_path = plan["master"], plan["pointer_path"]
    if _hash(master) != plan["master_hash"] or _hash(pointer_path) != plan["pointer_hash"]:
        raise ValueError("Master o puntero cambió durante la validación")
    for path, hash_value in plan["hashes"].items():
        if _hash(Path(path)) != hash_value:
            raise ValueError("Una captura cambió durante la validación")
    backup_dir = root / "output" / "checkpoints_validos"
    backup_dir.mkdir(parents=True, exist_ok=True)
    tag = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
    master_backup = backup_dir / f"before_phase2_postal_{tag}.xlsx"
    pointer_backup = backup_dir / f"before_phase2_postal_{tag}.json"
    shutil.copy2(master, master_backup)
    shutil.copy2(pointer_path, pointer_backup)
    try:
        _write_master(master, plan["frame"], plan["summary"], plan["control"])
        write_pointer(pointer_path, master, plan["day"], plan["completed"], plan["partial"])
        from scripts.audit_master_phases import inventory
        phases, retailers, _, _ = inventory(master)
        phase = phases.loc[phases["fase"].eq(2)]
        if (len(phase) != 1
            or phase.iloc[0]["estado_calculado"] != plan["states"][2]
            or not bool(phase.iloc[0]["control_coincide"])):
            raise ValueError("Auditoría posterior de Fase 2 no coincide con Control")
        for retailer, (frame, _) in plan["additions"].items():
            part = retailers.loc[
                retailers["fase"].eq(2) & retailers["retailer"].eq(retailer)
            ]
            if (len(part) != 1
                or part.iloc[0]["estado"] != "INTEGRADO_COMPLETE"
                or int(part.iloc[0]["filas_master"]) != len(frame)):
                raise ValueError(f"Auditoría posterior falló para {retailer}")
        if plan["states"][2] == "COMPLETE" and 2 not in plan["completed"]:
            raise ValueError("Puntero no marca Fase 2 COMPLETE")
        ptr = json.loads(pointer_path.read_text(encoding="utf-8"))
        if (ptr.get("capture_date") != plan["day"]
            or ptr.get("completed_phases") != plan["completed"]):
            raise ValueError("Puntero productivo inconsistente")
    except Exception:
        # Roll back both files, including any partial failure after Excel write.
        shutil.copy2(master_backup, master)
        shutil.copy2(pointer_backup, pointer_path)
        raise
    result = report(plan, "COMMITTED", backup=str(master_backup))
    reports = root / "output" / "reportes_integracion"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"fase2_postal_{tag}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Fase 2: integrar Excel ya capturados, sin scraping")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--preview", action="store_true")
    action.add_argument("--commit", action="store_true")
    parser.add_argument("--day", default=datetime.now().astimezone().date().isoformat())
    args = parser.parse_args()
    try:
        plan = prepare_plan(ROOT, args.day)
        result = commit(ROOT, plan) if args.commit else report(plan, "PREVIEW_VALIDATED")
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0
    except Exception as exc:
        print(f"FASE 2 NO INTEGRADA: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
