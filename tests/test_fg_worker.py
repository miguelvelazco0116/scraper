from __future__ import annotations

import sys

import pytest

import scripts.fg_worker as worker


class FakeProcess:
    def __init__(self, pid: int = 1234, returncode=None):
        self.pid = pid
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_rows_from_stdout_handles_utf8_and_mojibake():
    assert worker._rows_from_stdout("Productos únicos: 358") == 358
    assert worker._rows_from_stdout("Productos Ãºnicos: 40") == 40
    assert worker._rows_from_stdout("sin conteo") is None


def test_wait_for_devtools_active_port_reads_chrome_marker(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "DevToolsActivePort").write_text(
        "43123\n/devtools/browser/test\n",
        encoding="utf-8",
    )

    port = worker._wait_for_devtools_active_port(
        FakeProcess(),
        profile,
        timeout=0.5,
    )
    assert port == 43123


def test_wait_for_devtools_active_port_allows_windows_launcher_handoff(
    monkeypatch,
    tmp_path,
):
    profile = tmp_path / "profile"
    profile.mkdir()
    process = FakeProcess(returncode=0)
    sleeps = {"count": 0}

    def fake_sleep(seconds):
        sleeps["count"] += 1
        if sleeps["count"] == 1:
            (profile / "DevToolsActivePort").write_text(
                "43124\n/devtools/browser/handoff\n",
                encoding="utf-8",
            )

    monkeypatch.setattr(worker.time, "sleep", fake_sleep)

    port = worker._wait_for_devtools_active_port(
        process,
        profile,
        timeout=0.5,
    )

    assert port == 43124


def test_wait_for_devtools_active_port_times_out_after_launcher_exit(
    monkeypatch,
    tmp_path,
):
    profile = tmp_path / "profile"
    profile.mkdir()
    ticks = iter([0.0, 0.1, 0.6])

    monkeypatch.setattr(worker.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)

    with pytest.raises(TimeoutError, match="launcher_exit_code=0"):
        worker._wait_for_devtools_active_port(
            FakeProcess(returncode=0),
            profile,
            timeout=0.5,
        )


def test_start_native_chrome_uses_chrome_assigned_port_and_unique_profile(
    monkeypatch,
    tmp_path,
):
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    control = tmp_path / "control"

    calls = {}

    class PopenProcess(FakeProcess):
        pass

    def fake_popen(command, **kwargs):
        calls["command"] = command
        calls["kwargs"] = kwargs
        return PopenProcess(pid=9876)

    monkeypatch.setattr(worker, "CONTROL_DIR", control)
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(worker, "_chrome_executable", lambda: chrome)
    monkeypatch.setattr(worker.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        worker,
        "_wait_for_devtools_active_port",
        lambda process, profile, timeout=60.0: 45678,
    )

    process, cdp_url, profile = worker._start_native_chrome(
        "request-abc",
        attempts=1,
    )

    assert process.pid == 9876
    assert cdp_url == "http://127.0.0.1:45678"
    assert profile == control / "chrome_profiles" / "request-abc-1"
    assert "--remote-debugging-port=0" in calls["command"]
    assert (
        f"--user-data-dir={control / 'chrome_profiles' / 'request-abc-1'}"
        in calls["command"]
    )


def test_start_native_chrome_retries_after_failed_start(monkeypatch, tmp_path):
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    control = tmp_path / "control"

    starts = []
    waits = {"count": 0}

    def fake_popen(command, **kwargs):
        process = FakeProcess(pid=1000 + len(starts))
        starts.append(process)
        return process

    def fake_wait(process, profile, timeout=60.0):
        waits["count"] += 1
        if waits["count"] == 1:
            raise TimeoutError("first attempt")
        return 45679

    monkeypatch.setattr(worker, "CONTROL_DIR", control)
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(worker, "_chrome_executable", lambda: chrome)
    monkeypatch.setattr(worker.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(worker, "_wait_for_devtools_active_port", fake_wait)
    monkeypatch.setattr(worker, "_kill_process_tree", lambda process: None)
    monkeypatch.setattr(worker, "_kill_chrome_profile_processes", lambda profile: None)
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)

    process, cdp_url, profile = worker._start_native_chrome(
        "request-retry",
        attempts=2,
    )

    assert len(starts) == 2
    assert process is starts[1]
    assert cdp_url == "http://127.0.0.1:45679"
    assert profile.name == "request-retry-2"


@pytest.mark.skipif(sys.platform != "win32", reason="Validación específica de Windows")
def test_google_chrome_is_installed_on_windows_runner():
    chrome = worker._chrome_executable()
    assert chrome.exists()
    assert chrome.name.casefold() == "chrome.exe"
