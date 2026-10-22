"""Allowlisted local job launcher with persistent metadata and single-run locking.

No raw shell text, URLs, credentials, or arbitrary paths come from Streamlit.
Scrapers are run from Windows PowerShell in a dedicated local worker process.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

RUNNERS = {
    1: {
        "Todas las tiendas": "run_phase1_production.ps1",
        "Farmacias del Ahorro": "test_farmacias_del_ahorro_all.ps1",
        "Ibarra Mayoreo": "test_ibarra_mayoreo.ps1",
        "Chedraui": "run_chedraui_incremental.ps1",
    },
    2: {
        "Todas las tiendas": "run_phase2_production.ps1",
        "Farmacias Guadalajara": "test_farmacias_guadalajara_phase2.ps1",
        "Soriana": "test_soriana_manual_phase2.ps1",
    },
    3: {
        "Todas las tiendas": "run_phase3_production.ps1",
        "Bodega Aurrera": "test_bodega_aurrera.ps1",
        "Walmart": "test_walmart_phase3.ps1",
    },
    4: {
        "Todas las tiendas": "run_phase4_production.ps1",
        "Farmacias San Pablo": "run_san_pablo_full.ps1",
        "Farmacias Benavides": "test_benavides.ps1",
    },
    5: {
        "Todas las tiendas": "run_phase5_production.ps1",
        "Farmacias Similares": "test_farmacias_similares.ps1",
        "HEB": "test_heb.ps1",
        "Sam's Club": "test_sams_club.ps1",
        "Mayorista Duero": "test_duero_mayoreo.ps1",
    },
}
ALL = "Todas las tiendas"
RUNS_DIR = Path("diagnostics") / "dashboard_runs"
RUN_STATUS = {"PENDING", "RUNNING", "SUCCESS", "ERROR", "REJECTED", "STOPPED"}
BACKUP_SOURCE = {
    "Farmacias del Ahorro": "farmacias_del_ahorro_all_test.xlsx",
    "Ibarra Mayoreo": "ibarra_mayoreo_test.xlsx",
    "Farmacias Guadalajara": "farmacias_guadalajara_phase2_test.xlsx",
    "Soriana": "soriana_manual_phase2_test.xlsx",
    "Farmacias San Pablo": "farmacias_san_pablo_test.xlsx",
    "Farmacias Benavides": "benavides_test.xlsx",
    "Farmacias Similares": "farmacias_similares_test.xlsx",
    "HEB": "heb_phase5_test.xlsx",
    "Sam's Club": "sams_club_phase5_test.xlsx",
    "Mayorista Duero": "duero_mayoreo_phase5_test.xlsx",
    "Bodega Aurrera": "bodega_aurrera_phase3_test.xlsx",
    "Walmart": "walmart_phase3_test.xlsx",
}

def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def local_day():
    return datetime.now().astimezone().date().isoformat()

def job_folder(root: Path):
    return root / RUNS_DIR

def job_path(root: Path, run_id: str):
    if len(run_id) != 32 or any(x not in "0123456789abcdef" for x in run_id):
        raise ValueError("Id de ejecución inválido")
    return job_folder(root) / (run_id + ".json")

def _atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp." + uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

def load_job(root: Path, run_id: str):
    return json.loads(job_path(root, run_id).read_text(encoding="utf-8"))

def update_job(root: Path, run_id: str, **values):
    # Only the worker updates an execution after startup.
    path = job_path(root, run_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(values)
    _atomic_json(path, payload)

def stop_request_path(root: Path, run_id: str):
    return job_path(root, run_id).with_suffix(".stop")

def stop_requested(root: Path, run_id: str):
    return stop_request_path(root, run_id).is_file()

def stop_process_tree(pid: int):
    """Windows: terminate only the recorded PowerShell PID and descendants."""
    if os.name != "nt" or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        result = subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            capture_output=True, timeout=15, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

def request_stop(root: Path, run_id: str):
    job = load_job(root, run_id)
    if job.get("status") not in {"PENDING", "RUNNING"}:
        raise ValueError("Esta ejecución ya terminó; no hay proceso que detener.")
    # Restrict immediate process killing to fresh, heartbeating workers; a
    # stale PID may have been recycled by Windows and must not be touched.
    heartbeat = job.get("heartbeat_at")
    recent = False
    if heartbeat:
        try:
            recent = (datetime.now().astimezone() -
                      datetime.fromisoformat(heartbeat)) < timedelta(seconds=20)
        except (ValueError, TypeError):
            pass
    path = stop_request_path(root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as out:
            out.write(now() + "\n")
    except FileExistsError:
        pass
    pid = job.get("child_pid")
    # Immediate cancellation also works for jobs started by older worker code.
    # In new workers the same request is noticed on their next 2s poll.
    if recent and isinstance(pid, int) and pid > 0:
        stop_process_tree(pid)
    return True

def latest_jobs(root: Path, limit=25):
    folder = job_folder(root)
    if not folder.is_dir():
        return []
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out = []
    for path in files:
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            if stop_requested(root, job["run_id"]) and job.get("status") in ("RUNNING", "PENDING"):
                job["display_status"] = "PARO SOLICITADO"
            if job.get("status") == "RUNNING":
                heartbeat = job.get("heartbeat_at")
                if heartbeat:
                    try:
                        age = datetime.now().astimezone() - datetime.fromisoformat(heartbeat)
                        if age > timedelta(minutes=2):
                            if not job.get("display_status"):
                                job["display_status"] = "SIN LATIDO - REVISAR"
                    except (ValueError, TypeError):
                        pass
            out.append(job)
        except (OSError, ValueError):
            continue
    return out

def current_active_job(root: Path):
    for job in latest_jobs(root, limit=100):
        if job.get("status") in ("PENDING", "RUNNING"):
            start = job.get("started_at") or job.get("created_at")
            if start:
                try:
                    age = datetime.now().astimezone() - datetime.fromisoformat(start)
                    if job["status"] == "PENDING" and age <= timedelta(minutes=2):
                        return job
                    heartbeat = job.get("heartbeat_at")
                    if heartbeat and datetime.now().astimezone() - datetime.fromisoformat(heartbeat) <= timedelta(minutes=2):
                        return job
                except (ValueError, TypeError):
                    pass
    return None

def active_pointer(root: Path):
    path = root / "output" / "checkpoints_validos" / "current_master.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}

def prepare_run(root: Path, phase: int, target: str, mode: str):
    if phase not in RUNNERS or target not in RUNNERS[phase]:
        raise ValueError("Runner no autorizado para esa fase/retailer")
    if mode not in {"solo_captura", "captura_integracion"}:
        raise ValueError("Modo de ejecución inválido")
    full = target == ALL
    if not full and mode != "solo_captura":
        raise ValueError("Los runners por retailer son pruebas aisladas: no integran automáticamente")
    if phase == 1 and full and mode != "captura_integracion":
        raise ValueError("Fase 1 siempre realiza integración incremental. No existe modo solo captura.")
    if mode == "captura_integracion" and not full:
        raise ValueError("Integración restringida a runners de fase")
    if os.name != "nt":
        raise RuntimeError("La ejecución local solo se habilita en Windows")
    runner = root / "scripts" / RUNNERS[phase][target]
    if not runner.is_file():
        raise FileNotFoundError("Script no encontrado: " + str(runner))
    ps = shutil.which("powershell.exe")
    if not ps:
        raise RuntimeError("PowerShell de Windows no está disponible")
    pointer = active_pointer(root)
    # Phases 4 and 5 always require a complete same-day base even for --SkipIntegration.
    # Earlier phases 2 and 3 allow isolated capture without an active same-day master.
    needs_today = (phase in (4, 5) and full) or (full and mode == "captura_integracion" and phase > 1)
    if needs_today:
        if pointer.get("capture_date") != local_day() or not pointer.get("master") or not Path(pointer["master"]).is_file():
            raise ValueError("Master activo de hoy no disponible; el dashboard no utilizará capturas de otra fecha.")
        required = list(range(1, phase))
        prior = pointer.get("completed_phases", [])
        if any(x not in prior for x in required):
            raise ValueError("Las fases previas todavía no aparecen completas en el master de hoy.")
    args = [ps, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(runner)]
    if full and mode == "solo_captura":
        if phase not in (2, 3, 4, 5):
            raise ValueError("El runner no implementa -SkipIntegration.")
        args.append("-SkipIntegration")
    if not full and target == "Farmacias San Pablo":
        args.append("-SkipIntegration")
    if not full and target == "Chedraui":
        args.append("-Headed")
    return {
        "phase": phase, "target": target, "mode": mode,
        "runner": RUNNERS[phase][target], "command": args,
        "capture_date": local_day(),
    }

def public_command(root: Path, phase: int, target: str, mode: str):
    script = RUNNERS.get(phase, {}).get(target)
    if not script:
        return ""
    skip = mode == "solo_captura" and (target == "Farmacias San Pablo" or target == ALL and phase in (2, 3, 4, 5))
    return f"& .\\scripts\\{script}" + (" -SkipIntegration" if skip else "") + (" -Headed" if target == "Chedraui" else "")

def start_run(root: Path, phase: int, target: str, mode: str):
    spec = prepare_run(root, phase, target, mode)
    if current_active_job(root):
        raise RuntimeError("Ya existe una ejecución activa. Consulta su estado antes de iniciar otra.")
    run_id = uuid.uuid4().hex
    payload = {
        "run_id": run_id, "phase": phase, "target": target, "mode": mode,
        "runner": spec["runner"], "capture_date": spec["capture_date"],
        "created_at": now(), "status": "PENDING",
        "log_name": run_id + ".log",
    }
    _atomic_json(job_path(root, run_id), payload)
    worker = root / "dashboard" / "worker.py"
    try:
        # A standalone local job survives Streamlit rerenders/browser navigation.
        # CREATE_NO_WINDOW avoids opening an unrelated console window.
        proc = subprocess.Popen(
            [sys.executable, str(worker), run_id],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
        if proc.poll() is not None and proc.returncode != 0:
            raise RuntimeError("No fue posible iniciar el proceso de ejecución.")
    except Exception as exc:
        update_job(root, run_id, status="ERROR", error="No fue posible iniciar el worker local.", finished_at=now())
        raise RuntimeError("No fue posible iniciar el worker local") from exc
    return payload

def read_run_log(root: Path, run_id: str, max_bytes=80000):
    from dashboard.data import safe_log_tail
    path = job_folder(root) / (job_path(root, run_id).stem + ".log")
    if not path.is_file():
        return "(La captura aún no genera salida.)"
    return safe_log_tail(path, max_bytes=max_bytes)
