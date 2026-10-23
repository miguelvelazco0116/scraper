"""Background worker for Streamlit execution buttons; Windows-only CLI bridge.

The worker runs only fixed runner files and writes a persistent log, result
metadata, and a pre-run backup. Never interprets user-controlled shell text.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from dashboard.execution import (
    RUNS_DIR, active_pointer, job_folder, job_path, load_job, local_day,
    now, prepare_run, update_job, update_heartbeat, stop_requested, stop_process_tree,
)

@contextmanager
def exclusive_lock(root: Path):
    """OS-level file lock; released on crash, preventing double Streamlit runs."""
    path = job_folder(root) / "exclusive.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Otra captura de Streamlit está en ejecución") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Otra captura de Streamlit está en ejecución") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

def fingerprint(path):
    if not path or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(2**20), b""):
            h.update(chunk)
    return h.hexdigest()

def master_reference(root: Path):
    pointer = active_pointer(root)
    master = Path(pointer["master"]) if pointer.get("master") else None
    return master, fingerprint(master), pointer.get("capture_date")

def backup_validated_inputs(root: Path, run_id: str, target: str, mode: str):
    from dashboard.execution import BACKUP_SOURCE
    paths = []
    source_file = BACKUP_SOURCE.get(target)
    if source_file:
        paths.append(root / "output" / source_file)
    if mode == "captura_integracion":
        pointer = active_pointer(root)
        if pointer.get("master"):
            paths.append(Path(pointer["master"]))
        pointer_path = root / "output" / "checkpoints_validos" / "current_master.json"
        paths.append(pointer_path)
    directory = root / "output" / "checkpoints_validos" / "dashboard_pre_runs" / run_id
    saved = []
    for source in paths:
        if source.is_file():
            directory.mkdir(parents=True, exist_ok=True)
            target_path = directory / source.name
            shutil.copy2(source, target_path)
            saved.append(str(target_path.relative_to(root)))
    return saved

def _run_child(spec, log, run_id: str):
    if stop_requested(ROOT, run_id):
        log.write(b"PARO SOLICITADO: captura no iniciada.\n")
        log.flush()
        return 130, True
    cmd = spec["command"]
    log.write(("\n===== INICIO: " + now() + " =====\n" +
               "Script: " + spec["runner"] + "\n" +
               "Modo: " + spec["mode"] + "\n").encode("utf-8"))
    log.flush()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        cmd, cwd=str(ROOT), stdin=subprocess.DEVNULL,
        stdout=log, stderr=subprocess.STDOUT,
        env=env, close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        update_job(ROOT, run_id, child_pid=proc.pid)
        cancelled = False
        while True:
            code = proc.poll()
            if stop_requested(ROOT, run_id):
                cancelled = True
                if code is None:
                    stop_process_tree(proc.pid)
                    try:
                        code = proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        code = proc.wait(timeout=5)
                break
            # Keep heartbeat separate from job JSON. This is best effort:
            # a locked heartbeat marker must not orphan a running scraper.
            try:
                update_heartbeat(ROOT, run_id)
            except OSError:
                pass
            if code is not None:
                break
            time.sleep(2)
        log.write(("\n===== TERMINO: " + now() + f"; exit_code={code}; cancelled={cancelled} =====\n").encode("utf-8"))
        log.flush()
        return code, cancelled
    except BaseException:
        # If metadata writing fails after Popen, never leave an orphan
        # PowerShell/browser child capturing while the UI says REJECTED.
        if proc.poll() is None:
            stop_process_tree(proc.pid)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        raise

def main(run_id):
    job = load_job(ROOT, run_id)
    log_path = job_folder(ROOT) / (run_id + ".log")
    with log_path.open("ab") as log:
        try:
            with exclusive_lock(ROOT):
                # Re-check allowlist and master-date constraints inside the lock.
                if stop_requested(ROOT, run_id):
                    update_job(ROOT, run_id, status="STOPPED", finished_at=now(), exit_code=130)
                    log.write(b"Paro aceptado antes de iniciar.\n")
                    return 0
                spec = prepare_run(ROOT, int(job["phase"]), job["target"], job["mode"])
                if job["capture_date"] != local_day():
                    raise ValueError("La fecha cambió desde que se solicitó la captura. No se ejecuta.")
                update_job(ROOT, run_id, status="RUNNING", started_at=now(), heartbeat_at=now())
                update_heartbeat(ROOT, run_id)
                master_before, hash_before, master_date_before = master_reference(ROOT)
                backups = backup_validated_inputs(ROOT, run_id, job["target"], job["mode"])
                update_job(ROOT, run_id, backups=backups, master_before=str(master_before or ""),
                           master_sha256_before=hash_before, master_date_before=master_date_before)
                code, cancelled = _run_child(spec, log, run_id)
                master_after, hash_after, master_date_after = master_reference(ROOT)
                integrity_warning = None
                if job["mode"] == "solo_captura":
                    if master_before != master_after or hash_before != hash_after or master_date_before != master_date_after:
                        integrity_warning = "ALERTA: cambió el master durante un job de solo captura. Revisar concurrencia externa."
                if not cancelled and code == 0 and job["mode"] == "captura_integracion":
                    log.write(b"\n===== AUDITORIA POST-EJECUCION (SOLO LECTURA) =====\n")
                    log.flush()
                    audit = subprocess.run(
                        [sys.executable, str(ROOT / "scripts" / "audit_master_phases.py"), "--categories"],
                        cwd=str(ROOT), stdin=subprocess.DEVNULL,
                        stdout=log, stderr=subprocess.STDOUT, check=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    update_job(ROOT, run_id, audit_exit_code=audit.returncode)
                    if audit.returncode != 0:
                        integrity_warning = "Auditoría post-ejecución no satisfactoria; revisar log"
                if integrity_warning:
                    log.write(("\n" + integrity_warning + "\n").encode("utf-8"))
                    log.flush()
                update_job(
                    ROOT, run_id, status=("STOPPED" if cancelled else ("SUCCESS" if code == 0 and not integrity_warning else "ERROR")),
                    finished_at=now(), exit_code=code, integrity_warning=integrity_warning,
                    master_after=str(master_after or ""), master_sha256_after=hash_after,
                    master_date_after=master_date_after,
                    # Exit 0 is never treated as integration COMPLETE.
                    validation_note="El exit code no certifica las fases. Verifica el master/Control.",
                )
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            log.write(("\nERROR DEL WORKER: " + message + "\n").encode("utf-8", errors="replace"))
            log.flush()
            update_job(ROOT, run_id, status="REJECTED", finished_at=now(),
                       error=message[:500], exit_code=None)
            return 2
    return 0

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Se requiere un identificador de ejecución")
    raise SystemExit(main(sys.argv[1]))
