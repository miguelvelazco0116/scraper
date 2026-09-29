from __future__ import annotations

import json
import os
import re
import subprocess
import sys
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
    match = re.search(r"Productos\\s+(?:únicos|.nicos):\\s*(\\d+)", text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


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
    browser_channel = str(request.get("browser_channel") or "msedge").strip()

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
    env["FG_BROWSER_CHANNEL"] = browser_channel
    # El navegador interactivo que sí funciona usa la negociación normal.
    env["FG_DISABLE_HTTP2"] = "0"
    env["FG_DISABLE_QUIC"] = "0"
    # Evita reutilizar un UA fijo antiguo. El scraper usará el UA nativo.
    env.pop("FG_USER_AGENT", None)

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
    if headed:
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


if __name__ == "__main__":
    raise SystemExit(main())
