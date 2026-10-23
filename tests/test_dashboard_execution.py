"""Offline tests for the Streamlit execution control plane.

No scraper subprocess or Azure connection is started.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import unittest
from unittest.mock import patch

from dashboard.execution import (
    ALL, RUNNERS, _atomic_json, current_active_job, job_path,
    latest_jobs, public_command, prepare_run, start_run, request_stop, stop_requested,
    update_heartbeat, heartbeat_time, update_job,
)
from dashboard.worker import exclusive_lock


class DashboardExecutionTests(unittest.TestCase):
    def test_metadata_atomic_update_preserves_valid_json(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            rid = "c" * 32
            _atomic_json(job_path(root, rid), {"run_id": rid, "status": "PENDING"})
            update_job(root, rid, status="RUNNING", child_pid=4567)
            payload = json.loads(job_path(root, rid).read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "RUNNING")
            self.assertEqual(payload["child_pid"], 4567)
            self.assertEqual(
                list(job_path(root, rid).parent.glob("*.tmp.*")), []
            )

    def test_sidecar_heartbeat_does_not_rewrite_job_json(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            rid = "d" * 32
            _atomic_json(job_path(root, rid), {"run_id": rid, "status": "RUNNING"})
            content = job_path(root, rid).read_bytes()
            update_heartbeat(root, rid)
            self.assertIsNotNone(heartbeat_time(root, {"run_id": rid}))
            self.assertEqual(job_path(root, rid).read_bytes(), content)

    @unittest.skipUnless(os.name == "nt", "Windows rename sharing behavior")
    def test_windows_temporary_replace_denial_recovers(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            path = job_path(root, "e" * 32)
            _atomic_json(path, {"status": "PENDING"})
            original = os.replace
            attempts = []

            def temporarily_denied(source, destination):
                attempts.append(1)
                if len(attempts) < 3:
                    problem = PermissionError("[WinError 5] Access denied")
                    problem.winerror = 5
                    raise problem
                return original(source, destination)

            with patch("dashboard.execution.os.replace", side_effect=temporarily_denied), \
                 patch("dashboard.execution.time.sleep"):
                _atomic_json(path, {"status": "RUNNING"})
            self.assertEqual(len(attempts), 3)
            self.assertEqual(json.loads(path.read_text())["status"], "RUNNING")

    def test_retailers_all_thirteen_are_represented(self):
        self.assertEqual(len(RUNNERS), 5)
        self.assertEqual(sum(len(row) - 1 for row in RUNNERS.values()), 13)
        self.assertEqual(len(set(name for row in RUNNERS.values() for name in row if name != ALL)), 13)

    def test_cli_commands_are_allowlisted(self):
        base = Path("C:/Proyectos/scraper")
        self.assertIn("test_sams_club.ps1", public_command(base, 5, "Sam's Club", "solo_captura"))
        self.assertIn("-SkipIntegration", public_command(base, 4, ALL, "solo_captura"))
        self.assertIn("test_farmacias_del_ahorro_all.ps1", public_command(base, 1, "Farmacias del Ahorro", "solo_captura"))
        self.assertEqual(public_command(base, 2, "Retailer inexistente", "solo_captura"), "")

    def test_integration_not_available_for_individual_retailer(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                prepare_run(Path(temp), 5, "Mayorista Duero", "captura_integracion")

    def test_phase1_no_full_capture_only(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                prepare_run(Path(temp), 1, ALL, "solo_captura")

    def test_unknown_command_rejected_before_shell_lookup(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                prepare_run(Path(temp), 6, ALL, "solo_captura")

    def test_bad_run_id_rejected(self):
        with self.assertRaises(ValueError):
            job_path(Path("C:/Proyectos/scraper"), "../../../../../Windows")

    def test_pending_job_is_detected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            job_id = "a" * 32
            from dashboard.execution import now
            _atomic_json(job_path(root, job_id), {
                "run_id": job_id,
                "phase": 5,
                "target": "HEB",
                "status": "PENDING",
                "created_at": now(),
            })
            active = current_active_job(root)
            self.assertIsNotNone(active)
            self.assertEqual(active["run_id"], job_id)
            self.assertEqual(len(latest_jobs(root)), 1)

    def test_start_run_prevents_duplicate_before_subprocess(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            spec = {"runner": "test_heb.ps1", "capture_date": "2026-10-22"}
            with patch("dashboard.execution.prepare_run", return_value=spec), \
                 patch("dashboard.execution.current_active_job", return_value={"status": "RUNNING"}), \
                 patch("dashboard.execution.subprocess.Popen") as process:
                with self.assertRaises(RuntimeError):
                    start_run(root, 5, "HEB", "solo_captura")
                process.assert_not_called()

    def test_chedraui_uses_incremental_runner(self):
        command = public_command(Path("C:/Proyectos/scraper"), 1, "Chedraui", "solo_captura")
        self.assertIn("run_chedraui_incremental.ps1", command)
        self.assertNotIn("multistore", command)
        self.assertIn("-Headed", command)

    def test_stop_request_creates_durable_signal(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            job_id = "a" * 32
            from dashboard.execution import now
            _atomic_json(job_path(root, job_id), {
                "run_id": job_id, "phase": 1, "target": "Chedraui",
                "status": "RUNNING", "heartbeat_at": now(), "child_pid": 1234,
            })
            with patch("dashboard.execution.stop_process_tree") as kill:
                self.assertTrue(request_stop(root, job_id))
                kill.assert_called_once_with(1234)
                self.assertTrue(stop_requested(root, job_id))
            with self.assertRaises(ValueError):
                request_stop(root, "no-es-id")

    def test_finished_job_cannot_be_stopped(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            job_id = "b" * 32
            _atomic_json(job_path(root, job_id), {
                "run_id": job_id, "status": "SUCCESS",
            })
            with self.assertRaises(ValueError):
                request_stop(root, job_id)

    def test_exclusive_lock_rejects_a_second_worker(self):
        with TemporaryDirectory() as temp:
            with exclusive_lock(Path(temp)):
                with self.assertRaises(RuntimeError):
                    with exclusive_lock(Path(temp)):
                        pass


if __name__ == "__main__":
    unittest.main()
