from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROL_DIR = ROOT / "control" / "farmacias_guadalajara"
PROFILE_DIR = CONTROL_DIR / "local_edge_profile"


def _edge_executable() -> Path:
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ]
    discovered = shutil.which("msedge.exe") or shutil.which("msedge")
    if discovered:
        candidates.append(Path(discovered))

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate

    checked = "\n - ".join(str(x) for x in candidates)
    raise FileNotFoundError(
        "No se encontró Microsoft Edge. Rutas revisadas:\n - " + checked
    )


def _wait_for_devtools_active_port(
    process: subprocess.Popen,
    profile: Path,
    timeout: float = 60.0,
) -> int:
    marker = profile / "DevToolsActivePort"
    deadline = time.monotonic() + timeout
    launcher_exit_code: int | None = None

    while time.monotonic() < deadline:
        current_exit = process.poll()
        if current_exit is not None and launcher_exit_code is None:
            launcher_exit_code = int(current_exit)

        if marker.exists():
            try:
                lines = marker.read_text(
                    encoding="utf-8",
                    errors="replace",
                ).splitlines()
                port = int(lines[0].strip())
                if port > 0:
                    return port
            except (OSError, ValueError, IndexError):
                pass

        time.sleep(0.25)

    detail = (
        f"; launcher_exit_code={launcher_exit_code}"
        if launcher_exit_code is not None
        else ""
    )
    raise TimeoutError(
        f"Edge no publicó {marker.name} en {timeout:.0f}s{detail}"
    )


def _kill_profile_processes(profile: Path) -> None:
    script = (
        "$target = $args[0]; "
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" "
        "| Where-Object { "
        "$_.CommandLine -and "
        "$_.CommandLine.IndexOf($target, "
        "[System.StringComparison]::OrdinalIgnoreCase) -ge 0 "
        "} "
        "| ForEach-Object { "
        "Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue "
        "}"
    )
    try:
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
                str(profile),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception:
        pass


def _start_edge() -> tuple[subprocess.Popen, str]:
    edge = _edge_executable()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # Remove only stale DevTools marker. Keep the profile itself persistent so
    # Edge behaves like a normal user browser across runs.
    (PROFILE_DIR / "DevToolsActivePort").unlink(missing_ok=True)

    command = [
        str(edge),
        "--remote-debugging-port=0",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        "about:blank",
    ]

    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    port = _wait_for_devtools_active_port(process, PROFILE_DIR)
    return process, f"http://127.0.0.1:{port}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Ejecuta Farmacias Guadalajara en Edge nativo y adjunta Playwright "
            "por CDP, sin lanzar el navegador desde Playwright"
        )
    )
    parser.add_argument("--category", required=True)
    parser.add_argument("--max-load-more", type=int, default=100)
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()

    process = None
    try:
        process, cdp_url = _start_edge()
        print(f"Edge nativo iniciado. CDP: {cdp_url}")

        env = os.environ.copy()
        env["FG_CDP_URL"] = cdp_url
        env["FG_DISABLE_HTTP2"] = "0"
        env["FG_DISABLE_QUIC"] = "0"
        env.pop("FG_BROWSER_CHANNEL", None)
        env.pop("FG_BROWSER_EXECUTABLE", None)
        env.pop("FG_USER_AGENT", None)
        env.pop("FG_GRID_REQUEST_FALLBACK", None)

        command = [
            sys.executable,
            str(ROOT / "main.py"),
            "--retailer",
            "farmacias-guadalajara",
            "--category",
            args.category,
            "--max-load-more",
            str(args.max_load_more),
            "--headed",
        ]

        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                check=False,
                timeout=args.timeout,
            )
        except subprocess.TimeoutExpired:
            return 124

        return int(completed.returncode)
    finally:
        if process is not None:
            _kill_profile_processes(PROFILE_DIR)


if __name__ == "__main__":
    raise SystemExit(main())
