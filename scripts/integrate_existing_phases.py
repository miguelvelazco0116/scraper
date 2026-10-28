"""Protected no-scraping integration of already captured Phase 1, 3, 4 or 5.

The existing incremental integrator is the single authority for retailer
quality gates. This wrapper adds same-day / previous-phase gates, independent
preflight, backup, post-write audit and rollback. Phase 2 intentionally uses
its distinct 24-case Soriana postal integrator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "scripts"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import pandas as pd

from dashboard.execution import active_pointer
from scripts import integrate_complete_retailers as incremental
from scripts.audit_master_phases import inventory
from scripts.integrate_validated_phase import (
    _canonical, _control_capture_date, _frame_capture_dates,
    _read_existing_master,
)

PHASES = (1, 3, 4, 5)
POINTER = Path("output/checkpoints_validos/current_master.json")


def checksum(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(2**20), b""):
            digest.update(block)
    return digest.hexdigest()


def today() -> str:
    return datetime.now().astimezone().date().isoformat()


def source_candidates(phase: int, capture_date: str):
    """Validated source partitions, using the same rules as the real integrator."""
    ready, pending, hashes = {}, {}, {}
    for retailer, frame, summary, source, errors in incremental._candidates(
        phase, capture_date, approve_duero_online=False,
    ):
        try:
            if errors:
                raise ValueError("; ".join(errors))
            incremental._quality(phase, retailer, frame, summary)
            incremental._date_check(frame, pd.DataFrame(), capture_date)
            if not source.is_file():
                raise ValueError("Origen no disponible")
            ready[retailer] = len(frame)
            hashes[str(source)] = checksum(source)
        except (ValueError, KeyError, OSError, TypeError) as error:
            pending[retailer] = str(error)
    return ready, pending, hashes


def prepare(root: Path, phase: int, capture_date: str) -> dict:
    if phase not in PHASES:
        raise ValueError("Esta integración cubre únicamente las fases 1, 3, 4 y 5")
    if capture_date != today():
        raise ValueError("Sólo se permite integrar la fecha local actual")
    master = root / "output" / f"concentrado_productivo_total_{capture_date}.xlsx"
    pointer_file = root / POINTER
    current = active_pointer(root)
    prior = [int(x) for x in current.get("completed_phases", [])]
    previous_phases = list(range(1, phase))
    if phase > 1:
        if (current.get("capture_date") != capture_date
            or not master.is_file()
            or not str(current.get("master") or "").strip()):
            raise ValueError("Primero integra las fases previas en el master de hoy")
        pointer_master = Path(str(current["master"]))
        if not pointer_master.is_absolute():
            pointer_master = root / pointer_master
        if pointer_master.resolve() != master.resolve():
            raise ValueError("Puntero de master no corresponde al archivo de hoy")
        missing = [p for p in previous_phases if p not in prior]
        if missing:
            raise ValueError("Fases previas no COMPLETE: " + ", ".join(map(str, missing)))
    else:
        # Phase 1 can initialize a new master for today, but never replace a
        # different active-day master or an orphaned same-day workbook.
        if master.is_file():
            if current.get("capture_date") != capture_date:
                raise ValueError("El master de hoy no coincide con el puntero activo")
            ptr_master = Path(str(current.get("master") or ""))
            if not ptr_master.is_absolute():
                ptr_master = root / ptr_master
            if ptr_master.resolve() != master.resolve():
                raise ValueError("Master de hoy no es el activo")
        elif current.get("capture_date") == capture_date:
            raise ValueError("Puntero indica master de hoy, pero el archivo no existe")

    old_frame = None
    old_summary = None
    if master.is_file():
        old_frame, old_summary, control = _read_existing_master(master)
        if control.empty or _control_capture_date(control) != capture_date:
            raise ValueError("Control del master no corresponde a hoy")
        if _frame_capture_dates(old_frame) != {capture_date}:
            raise ValueError("El master contiene registros de otras fechas")
        phases, _, _, facts = inventory(master)
        if phase > 1:
            for p in previous_phases:
                row = phases.loc[phases["fase"].eq(p)]
                if (len(row) != 1 or row.iloc[0]["estado_calculado"] != "COMPLETE"
                    or not bool(row.iloc[0]["control_coincide"])):
                    raise ValueError(f"Fase {p} no tiene auditoría COMPLETE en master")
        if facts["warnings"]:
            raise ValueError("El master tiene observaciones de auditoría: "
                             + "; ".join(facts["warnings"]))

    ready, pending, hashes = source_candidates(phase, capture_date)
    if not ready:
        raise ValueError(
            f"Fase {phase}: ningún retailer con captura COMPLETE del día. "
            + "; ".join(f"{k}: {v}" for k, v in pending.items())
        )
    return {
        "phase": phase, "day": capture_date,
        "master": master, "pointer": pointer_file,
        "master_sha": checksum(master), "pointer_sha": checksum(pointer_file),
        "source_hashes": hashes,
        "ready": ready, "pending": pending,
        "original_frame": old_frame, "original_summary": old_summary,
    }


def public_report(plan: dict, status: str, *, backup: str | None = None):
    return {
        "status": status, "phase": plan["phase"],
        "capture_date": plan["day"],
        "sources_ready": plan["ready"],
        "sources_pending": plan["pending"],
        "master": str(plan["master"]),
        "master_modified": status == "COMMITTED",
        "backup": backup,
    }


def check_unchanged_partitions(before: pd.DataFrame | None,
                               after: pd.DataFrame,
                               updated: set[str]) -> None:
    if before is None:
        return
    old = _canonical(before.loc[~before["retailer"].astype(str).isin(updated)].copy())
    new = _canonical(after.loc[~after["retailer"].astype(str).isin(updated)].copy())
    pd.testing.assert_frame_equal(
        old.reset_index(drop=True), new.reset_index(drop=True),
        check_dtype=False, check_exact=True,
    )


def commit(root: Path, plan: dict) -> dict:
    if today() != plan["day"]:
        raise ValueError("Cambió la fecha local; no integrar un día diferente")
    master, pointer = plan["master"], plan["pointer"]
    if checksum(master) != plan["master_sha"] or checksum(pointer) != plan["pointer_sha"]:
        raise ValueError("El master o el puntero cambiaron desde la validación")
    for path, digest in plan["source_hashes"].items():
        if checksum(Path(path)) != digest:
            raise ValueError("Las capturas cambiaron desde la validación")
    # Worker enforces the single active job lock. This script itself never
    # starts a browser, Playwright, or a capture runner.
    checkpoint = root / "output" / "checkpoints_validos"
    checkpoint.mkdir(parents=True, exist_ok=True)
    token = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
    before_master = checkpoint / f"before_existing_fase{plan['phase']}_{token}.xlsx"
    before_pointer = checkpoint / f"before_existing_fase{plan['phase']}_{token}.json"
    if master.is_file():
        shutil.copy2(master, before_master)
    if pointer.is_file():
        shutil.copy2(pointer, before_pointer)
    try:
        process = subprocess.run(
            [sys.executable, str(root / "scripts" / "integrate_complete_retailers.py"),
             "--phase", str(plan["phase"])],
            cwd=root, check=False,
        )
        if process.returncode != 0:
            raise ValueError(f"El integrador incremental terminó con código {process.returncode}")
        if not master.is_file() or not pointer.is_file():
            raise ValueError("No se publicó el master/puntero de hoy")
        if today() != plan["day"]:
            raise ValueError("La fecha local cambió durante la integración")
        after, summary, control = _read_existing_master(master)
        if (_control_capture_date(control) != plan["day"]
            or _frame_capture_dates(after) != {plan["day"]}):
            raise ValueError("El master resultante mezcla fechas")
        current = json.loads(pointer.read_text(encoding="utf-8"))
        target = Path(str(current.get("master") or ""))
        if not target.is_absolute():
            target = root / target
        if current.get("capture_date") != plan["day"] or target.resolve() != master.resolve():
            raise ValueError("El puntero actualizado no coincide con el master")
        check_unchanged_partitions(plan["original_frame"], after, set(plan["ready"]))
        phases, retailers, _, facts = inventory(master)
        if facts["warnings"]:
            raise ValueError("Auditoría posterior no satisfactoria: "
                             + "; ".join(facts["warnings"]))
        for retailer, expected_rows in plan["ready"].items():
            row = retailers.loc[
                retailers["fase"].eq(plan["phase"])
                & retailers["retailer"].eq(retailer)
            ]
            if (len(row) != 1 or row.iloc[0]["estado"] != "INTEGRADO_COMPLETE"
                or int(row.iloc[0]["filas_master"]) != expected_rows):
                raise ValueError(f"Auditoría posterior no certificó {retailer}")
        phase = phases.loc[phases["fase"].eq(plan["phase"])]
        if len(phase) != 1 or not bool(phase.iloc[0]["control_coincide"]):
            raise ValueError("Estado de la fase no coincide con Control")
        for previous in range(1, plan["phase"]):
            row = phases.loc[phases["fase"].eq(previous)]
            if len(row) != 1 or row.iloc[0]["estado_calculado"] != "COMPLETE":
                raise ValueError(f"Se perdió certificación de Fase {previous}")
    except Exception:
        # Restore the old master AND pointer on any failure, including
        # post-publication audit failure. Remove new-day files only if we
        # bootstrapped Phase 1 and there was no previous master.
        if plan["master_sha"] is None:
            if master.exists():
                master.unlink()
        else:
            shutil.copy2(before_master, master)
        if plan["pointer_sha"] is None:
            if pointer.exists():
                pointer.unlink()
        else:
            shutil.copy2(before_pointer, pointer)
        raise
    result = public_report(plan, "COMMITTED",
                           backup=str(before_master) if plan["master_sha"] else None)
    result["master_rows"] = len(after)
    result["phase_status"] = str(phase.iloc[0]["estado_calculado"])
    reports = root / "output" / "reportes_integracion"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"capturas_existentes_fase{plan['phase']}_{token}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return result


def main():
    parser = argparse.ArgumentParser(description="Integrar capturas existentes sin scraping")
    parser.add_argument("--phase", type=int, required=True, choices=PHASES)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--preview", action="store_true")
    modes.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    try:
        plan = prepare(ROOT, args.phase, today())
        result = (public_report(plan, "PREVIEW_VALIDATED") if args.preview
                  else commit(ROOT, plan))
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0
    except Exception as error:
        print(f"NO INTEGRADO: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
