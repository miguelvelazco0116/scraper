"""Reconcile a *requested* stop after a dashboard worker has disappeared.

This module never kills processes or changes capture Excel/master files.
It only releases stale dashboard metadata after an OS-level lock, a dead
worker/child process check, and an explicit recorded stop request.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from dashboard.execution import (
    _atomic_json, heartbeat_time, job_path, latest_jobs, load_job, now,
    stop_requested,
)
from dashboard.worker import exclusive_lock


def windows_processes() -> list[dict]:
    """Fail closed when Windows cannot provide a complete process snapshot."""
    command = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
        "ConvertTo-Json -Depth 3 -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
             "-Command", command],
            text=True, capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("No fue posible inspeccionar procesos Windows") from exc
    if result.returncode:
        raise RuntimeError(
            "No se pudieron inspeccionar procesos Windows; conserva el bloqueo. "
            + result.stderr.strip()[:250]
        )
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Respuesta de procesos Windows no confiable") from exc
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not data:
        raise RuntimeError("No se recibió un inventario de procesos Windows")
    if any(not isinstance(item, dict) or "ProcessId" not in item
           or "Name" not in item for item in data):
        raise RuntimeError("Inventario de procesos Windows incompleto")
    return data


def _live_scraper_process(job: dict, processes: list[dict]) -> str | None:
    run_id = job["run_id"].casefold()
    child_pid = job.get("child_pid")
    runner = str(job.get("runner") or "").casefold()
    target = str(job.get("target") or "").casefold()
    for process in processes:
        try:
            pid = int(process["ProcessId"])
        except (ValueError, TypeError, KeyError) as exc:
            raise RuntimeError("PID Windows ilegible: no se libera el bloqueo") from exc
        cmd = str(process.get("CommandLine") or "").casefold()
        name = str(process.get("Name") or "").casefold()
        # A PID match blocks even if Windows recycled it: false positives are
        # safer than releasing a job while its tree is still running.
        if isinstance(child_pid, int) and child_pid > 0 and pid == child_pid:
            return f"PID hijo {child_pid} sigue asignado: {name}"
        if name not in {"python.exe", "pythonw.exe", "powershell.exe",
                        "pwsh.exe", "py.exe"}:
            continue
        if "worker.py" in cmd and run_id in cmd:
            return f"Worker del folio todavía activo (PID {pid})"
        if runner and runner in cmd:
            return f"Runner {runner} todavía activo (PID {pid})"
        # An orphaned Python/Chedraui child may outlive its PowerShell parent.
        # Treat every Chedraui automation process as a blocker.
        if target == "chedraui" and "chedraui" in cmd and (
            ".py" in cmd or ".ps1" in cmd
        ):
            return f"Captura Chedraui todavía activa (PID {pid})"
        # For full-phase runs, any project runner may still be updating data.
        if target == "todas las tiendas" and (
            "scripts\\run_phase" in cmd
            or "scripts/run_phase" in cmd
            or "scripts\\test_" in cmd
            or "scripts/test_" in cmd
        ):
            return f"Runner de fase todavía activo (PID {pid})"
    return None


def reconcile_requested_stop(root: Path, run_id: str | None = None) -> dict:
    """Atomically mark a *dead*, explicitly cancelled job STOPPED.

    Refuse if the dashboard worker still holds the execution lock, if its
    heartbeat is fresh, or if Windows sees any relevant worker/child.
    """
    if run_id is None:
        stopped = [
            job for job in latest_jobs(root, limit=100)
            if job.get("status") in {"PENDING", "RUNNING"}
            and stop_requested(root, job["run_id"])
        ]
        if len(stopped) != 1:
            raise ValueError(
                f"Se esperaban 1 ejecución activa con paro solicitado; encontradas {len(stopped)}. "
                "Especifica el folio si hay más de una."
            )
        run_id = stopped[0]["run_id"]
    with exclusive_lock(root):
        job = load_job(root, run_id)
        if job.get("status") not in {"PENDING", "RUNNING"}:
            return {"run_id": run_id, "status": job.get("status"),
                    "message": "La ejecución ya no está activa."}
        if not stop_requested(root, run_id):
            raise ValueError("No se solicitó paro para esta ejecución")
        heartbeat = heartbeat_time(root, job)
        if heartbeat is None:
            try:
                heartbeat = datetime.fromisoformat(job["created_at"]).astimezone()
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    "Sin fecha verificable de la ejecución; no se libera"
                ) from exc
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.astimezone()
        elapsed = datetime.now().astimezone() - heartbeat
        if elapsed < timedelta(seconds=45):
            raise RuntimeError(
                "El worker aún puede estar cerrando. Paro reciente/latido activo; "
                "no se libera el bloqueo."
            )
        processes = windows_processes()
        live = _live_scraper_process(job, processes)
        if live:
            raise RuntimeError(live + ". Verifica que termine antes de reintentar.")
        # Capture a recoverable copy of original metadata, NOT the Excel/master.
        original = job_path(root, run_id)
        backup = original.with_name(original.stem + "." + uuid.uuid4().hex + ".json.bak")
        shutil.copy2(original, backup)
        job.update({
            "status": "STOPPED",
            "finished_at": now(),
            "exit_code": 130,
            "reconciled_at": now(),
            "reconciliation_reason": "Paro solicitado; worker y proceso de captura no activos",
            "reconciliation_backup": str(backup),
        })
        _atomic_json(original, job)
        return {
            "run_id": run_id, "status": "STOPPED",
            "message": "Ejecución detenida verificada: el bloqueo se liberó. "
                       "Master y capturas sin modificaciones.",
            "backup": str(backup),
        }
