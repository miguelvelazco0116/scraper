from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROL_DIR = ROOT / "control" / "farmacias_guadalajara"
REQUEST_PATH = CONTROL_DIR / "request.json"
RESULT_PATH = CONTROL_DIR / "result.json"
LOG_PATH = CONTROL_DIR / "worker.log"

ALLOWED_CATEGORIES = {
    "vias-respiratorias",
    "lavanderia",
    "cuidado-bucal",
    "preservativos",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def _identity() -> str:
    try:
        completed = subprocess.run(
            ["whoami"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        return completed.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _rows_from_stdout(stdout: str) -> int | None:
    text = stdout or ""
    match = re.search(
        r"Productos\s+[^:\r\n]*:\s*(\d+)",
        text,
        flags=re.IGNORECASE,
    )
    return int(match.group(1)) if match else None

def _chrome_executable() -> Path:
    candidates: list[Path] = []

    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = (os.getenv(env_name) or "").strip()
        if root:
            candidates.append(
                Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"
            )

    candidates.extend(
        [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        ]
    )

    discovered = shutil.which("chrome.exe") or shutil.which("chrome")
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

    checked = "\n - ".join(str(path) for path in candidates)
    raise FileNotFoundError(
        "No se encontró Google Chrome instalado. Rutas revisadas:\n - " + checked
    )


def _kill_process_tree(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    try:
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _kill_chrome_profile_processes(profile: Path | None) -> None:
    if profile is None:
        return

    # Chrome on Windows can hand execution from the launcher PID to another
    # chrome.exe process. Clean up only processes that reference this run's
    # unique user-data-dir so other Chrome windows remain untouched.
    script = (
        "$target = $args[0]; "
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" "
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


def _wait_for_devtools_active_port(
    process: subprocess.Popen,
    profile: Path,
    timeout: float = 60.0,
) -> int:
    marker = profile / "DevToolsActivePort"
    deadline = time.monotonic() + timeout
    launcher_exit_code: int | None = None

    while time.monotonic() < deadline:
        # On Windows, chrome.exe can act only as a launcher: the original
        # process may exit with code 0 after handing the browser off to another
        # chrome.exe process. That is not a startup failure. The authoritative
        # signal is DevToolsActivePort in the requested user-data-dir.
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
        f"Chrome no publicó {marker.name} en {timeout:.0f}s{detail}"
    )


def _start_native_chrome(
    request_id: str,
    attempts: int = 3,
) -> tuple[subprocess.Popen, str, Path]:
    chrome = _chrome_executable()
    profiles_root = CONTROL_DIR / "chrome_profiles"
    profiles_root.mkdir(parents=True, exist_ok=True)

    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        profile = profiles_root / f"{request_id}-{attempt}"
        if profile.exists():
            shutil.rmtree(profile, ignore_errors=True)
        profile.mkdir(parents=True, exist_ok=True)

        # Chrome chooses the port itself, eliminating the race between reserving
        # a local port and launching the browser.
        command = [
            str(chrome),
            "--remote-debugging-port=0",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-mode",
            "--new-window",
            "about:blank",
        ]

        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            port = _wait_for_devtools_active_port(
                process,
                profile,
                timeout=60.0,
            )
            return process, f"http://127.0.0.1:{port}", profile
        except Exception as exc:
            last_error = exc
            _kill_process_tree(process)
            _kill_chrome_profile_processes(profile)
            for _ in range(10):
                try:
                    shutil.rmtree(profile)
                    break
                except Exception:
                    time.sleep(0.25)

            if attempt < attempts:
                time.sleep(1.0)

    detail = (
        f"{type(last_error).__name__}: {last_error}"
        if last_error is not None
        else "sin detalle"
    )
    raise RuntimeError(
        f"No fue posible iniciar Chrome CDP después de {attempts} intentos. {detail}"
    )

def main() -> int:
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)

    if not REQUEST_PATH.exists():
        _write_json(
            RESULT_PATH,
            {
                "status": "error",
                "finished_at": _now(),
                "identity": _identity(),
                "error": f"No existe {REQUEST_PATH}",
            },
        )
        return 2

    try:
        request = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        _write_json(
            RESULT_PATH,
            {
                "status": "error",
                "finished_at": _now(),
                "identity": _identity(),
                "error": f"Solicitud inválida: {type(exc).__name__}: {exc}",
            },
        )
        return 2

    request_id = str(request.get("request_id") or "").strip()
    category = str(request.get("category") or "").strip()
    max_load_more = int(request.get("max_load_more", 100))
    headed = bool(request.get("headed", False))
    browser_channel = str(request.get("browser_channel") or "chrome-cdp").strip()
    native_chrome_cdp = browser_channel.casefold() == "chrome-cdp"

    if not request_id:
        raise SystemExit("request_id es obligatorio")
    if category not in ALLOWED_CATEGORIES:
        raise SystemExit(f"Categoría no permitida: {category}")
    if max_load_more < 1 or max_load_more > 500:
        raise SystemExit("max_load_more debe estar entre 1 y 500")

    identity = _identity()
    started = {
        "request_id": request_id,
        "status": "running",
        "started_at": _now(),
        "identity": identity,
        "category": category,
        "browser_channel": browser_channel,
        "headed": headed,
    }
    _write_json(RESULT_PATH, started)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["FG_DISABLE_HTTP2"] = "0"
    env["FG_DISABLE_QUIC"] = "0"
    env.pop("FG_USER_AGENT", None)
    env.pop("FG_CDP_URL", None)

    chrome_process = None
    chrome_profile = None
    if native_chrome_cdp:
        try:
            chrome_process, cdp_url, chrome_profile = _start_native_chrome(request_id)
            env["FG_CDP_URL"] = cdp_url
            env.pop("FG_BROWSER_CHANNEL", None)
        except Exception as exc:
            result = {
                **started,
                "status": "error",
                "finished_at": _now(),
                "exit_code": 1,
                "error": f"No se pudo iniciar Chrome nativo/CDP: {type(exc).__name__}: {exc}",
            }
            _write_json(RESULT_PATH, result)
            return 1
    else:
        env["FG_BROWSER_CHANNEL"] = browser_channel

    command = [
        sys.executable,
        str(ROOT / "main.py"),
        "--retailer",
        "farmacias-guadalajara",
        "--category",
        category,
        "--max-load-more",
        str(max_load_more),
    ]
    if headed or native_chrome_cdp:
        command.append("--headed")

    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        LOG_PATH.write_text(
            (
                f"[{_now()}] request_id={request_id}\n"
                f"identity={identity}\n"
                f"command={' '.join(command)}\n\n"
                f"===== STDOUT =====\n{stdout}\n"
                f"===== STDERR =====\n{stderr}\n"
            ),
            encoding="utf-8",
        )
        result = {
            **started,
            "status": "ok" if completed.returncode == 0 else "error",
            "finished_at": _now(),
            "exit_code": completed.returncode,
            "rows": _rows_from_stdout(stdout),
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
            "log": str(LOG_PATH),
        }
        _write_json(RESULT_PATH, result)
        return int(completed.returncode)
    except subprocess.TimeoutExpired as exc:
        LOG_PATH.write_text(
            f"[{_now()}] TIMEOUT request_id={request_id}\n{exc}\n",
            encoding="utf-8",
        )
        _write_json(
            RESULT_PATH,
            {
                **started,
                "status": "error",
                "finished_at": _now(),
                "exit_code": 124,
                "error": "Timeout de 3600 segundos",
                "log": str(LOG_PATH),
            },
        )
        return 124
    except Exception as exc:
        LOG_PATH.write_text(
            f"[{_now()}] ERROR request_id={request_id}\n{type(exc).__name__}: {exc}\n",
            encoding="utf-8",
        )
        _write_json(
            RESULT_PATH,
            {
                **started,
                "status": "error",
                "finished_at": _now(),
                "exit_code": 1,
                "error": f"{type(exc).__name__}: {exc}",
                "log": str(LOG_PATH),
            },
        )
        return 1
    finally:
        _kill_process_tree(chrome_process)
        _kill_chrome_profile_processes(chrome_profile)

        if chrome_profile is not None:
            # Chrome puede tardar unos segundos en liberar archivos del perfil.
            for _ in range(10):
                try:
                    shutil.rmtree(chrome_profile)
                    break
                except Exception:
                    time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
