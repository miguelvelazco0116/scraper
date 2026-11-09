"""Cancellation reconciliation is fail-closed; no web/scraper calls."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from dashboard.execution import (
    _atomic_json, job_path, stop_request_path, update_heartbeat,
)
from dashboard.recovery import (
    _live_scraper_process, reconcile_requested_stop,
)


def old_job(root, *, stopped=True, child_pid=2345, status="RUNNING"):
    rid = "a" * 32
    record = {
        "run_id": rid,
        "status": status,
        "phase": 1,
        "target": "Chedraui",
        "runner": "run_chedraui_incremental.ps1",
        "created_at": (datetime.now().astimezone()
                       - timedelta(minutes=8)).isoformat(),
        "started_at": (datetime.now().astimezone()
                       - timedelta(minutes=6)).isoformat(),
        "child_pid": child_pid,
    }
    _atomic_json(job_path(root, rid), record)
    if stopped:
        stop_request_path(root, rid).touch()
    return rid


def snapshot():
    return [{"ProcessId": 100, "Name": "python.exe",
             "CommandLine": "python background_unrelated.py"}]


def test_safe_recovery_releases_only_metadata_and_preserves_files(tmp_path):
    rid = old_job(tmp_path)
    master = tmp_path / "output" / "master.xlsx"
    master.parent.mkdir()
    master.write_bytes(b"original-master-sentinel")
    source = tmp_path / "output" / "chedraui_capture.xlsx"
    source.write_bytes(b"original-capture-sentinel")
    with patch("dashboard.recovery.windows_processes", return_value=snapshot()):
        result = reconcile_requested_stop(tmp_path)
    assert result["status"] == "STOPPED"
    state = json.loads(job_path(tmp_path, rid).read_text())
    assert state["status"] == "STOPPED"
    assert state["exit_code"] == 130
    assert Path(result["backup"]).is_file()
    assert master.read_bytes() == b"original-master-sentinel"
    assert source.read_bytes() == b"original-capture-sentinel"
    assert reconcile_requested_stop(tmp_path, rid)["status"] == "STOPPED"


def test_refuse_without_requested_stop(tmp_path):
    rid = old_job(tmp_path, stopped=False)
    with pytest.raises(ValueError, match="No se solicitó paro"):
        reconcile_requested_stop(tmp_path, rid)
    assert json.loads(job_path(tmp_path, rid).read_text())["status"] == "RUNNING"


def test_refuse_live_child_pid_even_if_old_heartbeat(tmp_path):
    rid = old_job(tmp_path)
    alive = [{"ProcessId": 2345, "Name": "powershell.exe",
              "CommandLine": "powershell.exe -File run_chedraui_incremental.ps1"}]
    with patch("dashboard.recovery.windows_processes", return_value=alive):
        with pytest.raises(RuntimeError, match="PID hijo"):
            reconcile_requested_stop(tmp_path, rid)
    assert json.loads(job_path(tmp_path, rid).read_text())["status"] == "RUNNING"


def test_refuse_live_worker_even_without_child_pid(tmp_path):
    rid = old_job(tmp_path, child_pid=None)
    active = [{"ProcessId": 9111, "Name": "python.exe",
               "CommandLine": f"python dashboard/worker.py {rid}"}]
    with patch("dashboard.recovery.windows_processes", return_value=active):
        with pytest.raises(RuntimeError, match="Worker"):
            reconcile_requested_stop(tmp_path, rid)


def test_refuse_orphan_chedraui_child(tmp_path):
    rid = old_job(tmp_path, child_pid=None)
    alive = [{"ProcessId": 9555, "Name": "python.exe",
              "CommandLine": "python scripts/test_chedraui.py --category all"}]
    with patch("dashboard.recovery.windows_processes", return_value=alive):
        with pytest.raises(RuntimeError, match="Captura Chedraui"):
            reconcile_requested_stop(tmp_path, rid)


def test_refuse_fresh_heartbeat_even_if_stop_requested(tmp_path):
    rid = old_job(tmp_path)
    update_heartbeat(tmp_path, rid)
    with patch("dashboard.recovery.windows_processes") as processes:
        with pytest.raises(RuntimeError, match="latido activo"):
            reconcile_requested_stop(tmp_path, rid)
    processes.assert_not_called()


def test_refuse_if_exclusive_worker_lock_held(tmp_path):
    from dashboard.worker import exclusive_lock
    rid = old_job(tmp_path)
    with exclusive_lock(tmp_path):
        with pytest.raises(RuntimeError, match="Otra captura"):
            reconcile_requested_stop(tmp_path, rid)
    assert json.loads(job_path(tmp_path, rid).read_text())["status"] == "RUNNING"


def test_refuse_if_multiple_cancelled_runs_need_folio(tmp_path):
    old_job(tmp_path)
    rid2 = "b" * 32
    old_job_data = json.loads(job_path(tmp_path, "a" * 32).read_text())
    old_job_data["run_id"] = rid2
    _atomic_json(job_path(tmp_path, rid2), old_job_data)
    stop_request_path(tmp_path, rid2).touch()
    with pytest.raises(ValueError, match="encontradas 2"):
        reconcile_requested_stop(tmp_path)


def test_refuse_when_process_inventory_unavailable(tmp_path):
    rid = old_job(tmp_path)
    with patch("dashboard.recovery.windows_processes",
               side_effect=RuntimeError("No se pudieron inspeccionar procesos Windows")):
        with pytest.raises(RuntimeError, match="inspeccionar procesos"):
            reconcile_requested_stop(tmp_path, rid)
    assert json.loads(job_path(tmp_path, rid).read_text())["status"] == "RUNNING"
