from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROL_DIR = ROOT / "control" / "farmacias_guadalajara"
REQUEST_PATH = CONTROL_DIR / "request.json"
RESULT_PATH = CONTROL_DIR / "result.json"

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


def _read_result_for(request_id: str) -> dict | None:
    if not RESULT_PATH.exists():
        return None
    try:
        payload = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if str(payload.get("request_id") or "") != request_id:
        return None
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dispara Farmacias Guadalajara mediante Windows Task Scheduler"
    )
    parser.add_argument("--category", required=True, choices=sorted(ALLOWED_CATEGORIES))
    parser.add_argument("--max-load-more", type=int, default=100)
    parser.add_argument("--browser-channel", default="msedge-cdp")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--headed",
        dest="headed",
        action="store_true",
        help="Ejecutar navegador visible; con msedge-cdp es el modo normal de producción",
    )
    mode.add_argument(
        "--headless",
        dest="headed",
        action="store_false",
        help="Ejecutar modo headless; no recomendado para Guadalajara",
    )
    parser.set_defaults(headed=True)
    parser.add_argument("--task-name", default="Scraper-FarmaciasGuadalajara")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    if args.max_load_more < 1 or args.max_load_more > 500:
        parser.error("--max-load-more debe estar entre 1 y 500")
    if args.timeout < 10:
        parser.error("--timeout debe ser al menos 10 segundos")

    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    request_id = uuid.uuid4().hex

    request = {
        "request_id": request_id,
        "created_at": _now(),
        "category": args.category,
        "max_load_more": args.max_load_more,
        "browser_channel": args.browser_channel,
        "headed": args.headed,
    }

    _write_json(REQUEST_PATH, request)
    if RESULT_PATH.exists():
        RESULT_PATH.unlink()

    trigger = subprocess.run(
        ["schtasks.exe", "/Run", "/TN", args.task_name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if trigger.returncode != 0:
        print("No se pudo iniciar la tarea programada.")
        if trigger.stdout:
            print(trigger.stdout.strip())
        if trigger.stderr:
            print(trigger.stderr.strip())
        print(
            "Instala primero la tarea con scripts\\install_fg_task.ps1 "
            "o valida el nombre con Get-ScheduledTask."
        )
        return trigger.returncode or 1

    print(f"Solicitud enviada: {request_id}")
    print(f"Categoría: {args.category}")
    print(f"Navegador: {args.browser_channel}")
    print(f"Headed: {args.headed}")
    print("Esperando resultado...")

    deadline = time.monotonic() + args.timeout
    last_status = None
    while time.monotonic() < deadline:
        payload = _read_result_for(request_id)
        if payload:
            status = str(payload.get("status") or "")
            if status != last_status:
                print(f"Estado: {status}")
                last_status = status

            if status in {"ok", "error"}:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                exit_code = payload.get("exit_code")
                if isinstance(exit_code, int):
                    return exit_code
                return 0 if status == "ok" else 1

        time.sleep(2)

    print(f"TIMEOUT: no hubo resultado final en {args.timeout} segundos.")
    print(f"Revisa: {RESULT_PATH}")
    return 124


if __name__ == "__main__":
    raise SystemExit(main())
