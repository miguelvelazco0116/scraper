from __future__ import annotations

import argparse
import base64
import io
import os
import subprocess
import sys
import time
from pathlib import Path

import pyautogui
import pyperclip
from openai import OpenAI


ROOT = Path(__file__).resolve().parents[1]
BROWSER_SCRIPT = ROOT / "scripts" / "fg_manual_browser.js"
IMPORTER = ROOT / "scripts" / "import_fg_manual.py"

CATEGORY_URLS = {
    "cuidado-bucal": (
        "https://www.farmaciasguadalajara.com/"
        "super/higiene-y-belleza/cuidado-bucal"
    ),
    "lavanderia": (
        "https://www.farmaciasguadalajara.com/super/hogar/lavanderia-"
    ),
    "preservativos": (
        "https://www.farmaciasguadalajara.com/"
        "farmacia/salud-sexual/preservativos"
    ),
    "vias-respiratorias": (
        "https://www.farmaciasguadalajara.com/"
        "farmacia/medicina/respiratorio/vias-respiratorias"
    ),
}

EDGE_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]


def _edge_executable() -> Path:
    for candidate in EDGE_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No se encontró Microsoft Edge")


def _open_normal_edge(url: str) -> None:
    edge = _edge_executable()
    subprocess.Popen(
        [str(edge), "--new-window", url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _screenshot_data_url() -> str:
    image = pyautogui.screenshot()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _as_dict(value):
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    raise TypeError(f"No se pudo convertir acción: {type(value)!r}")


def _normalize_key(key: str) -> str:
    mapping = {
        "CTRL": "ctrl",
        "CONTROL": "ctrl",
        "ALT": "alt",
        "SHIFT": "shift",
        "ENTER": "enter",
        "RETURN": "enter",
        "TAB": "tab",
        "ESC": "esc",
        "ESCAPE": "esc",
        "BACKSPACE": "backspace",
        "DELETE": "delete",
        "SPACE": "space",
        "ARROWUP": "up",
        "ARROWDOWN": "down",
        "ARROWLEFT": "left",
        "ARROWRIGHT": "right",
        "META": "win",
        "WIN": "win",
        "WINDOWS": "win",
        "F12": "f12",
    }
    raw = str(key)
    return mapping.get(raw.upper(), raw.lower())


def _execute_action(raw_action) -> None:
    action = _as_dict(raw_action)
    kind = action.get("type")

    if kind == "screenshot":
        return
    if kind == "wait":
        time.sleep(float(action.get("seconds") or 2.0))
        return
    if kind == "click":
        pyautogui.click(
            int(action["x"]),
            int(action["y"]),
            button=str(action.get("button") or "left"),
        )
        return
    if kind == "double_click":
        pyautogui.doubleClick(
            int(action["x"]),
            int(action["y"]),
            interval=0.12,
            button=str(action.get("button") or "left"),
        )
        return
    if kind == "move":
        pyautogui.moveTo(
            int(action["x"]),
            int(action["y"]),
            duration=0.15,
        )
        return
    if kind == "type":
        pyautogui.write(str(action.get("text") or ""), interval=0.01)
        return
    if kind == "keypress":
        keys = action.get("keys") or []
        keys = [_normalize_key(x) for x in keys]
        if not keys:
            return
        if len(keys) == 1:
            pyautogui.press(keys[0])
        else:
            pyautogui.hotkey(*keys)
        return
    if kind == "scroll":
        x = action.get("x")
        y = action.get("y")
        if x is not None and y is not None:
            pyautogui.moveTo(int(x), int(y), duration=0.1)
        scroll_y = int(action.get("scroll_y") or action.get("delta_y") or 0)
        scroll_x = int(action.get("scroll_x") or action.get("delta_x") or 0)
        if scroll_y:
            clicks = max(1, abs(scroll_y) // 100)
            pyautogui.scroll(-clicks if scroll_y > 0 else clicks)
        if scroll_x:
            clicks = max(1, abs(scroll_x) // 100)
            pyautogui.hscroll(clicks if scroll_x > 0 else -clicks)
        return
    if kind == "drag":
        path = action.get("path") or []
        if not path:
            return
        first = path[0]
        pyautogui.moveTo(int(first["x"]), int(first["y"]), duration=0.1)
        pyautogui.mouseDown()
        try:
            for point in path[1:]:
                pyautogui.moveTo(
                    int(point["x"]),
                    int(point["y"]),
                    duration=0.08,
                )
        finally:
            pyautogui.mouseUp()
        return

    raise RuntimeError(f"Acción de computer use no soportada: {kind}")


def _computer_calls(response):
    return [
        item
        for item in response.output
        if getattr(item, "type", None) == "computer_call"
    ]


def _run_gpt_desktop(category: str, model: str, max_turns: int) -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "Falta OPENAI_API_KEY. Configúrala como variable de entorno; "
            "no la guardes en el repositorio."
        )

    script_text = BROWSER_SCRIPT.read_text(encoding="utf-8")
    pyperclip.copy(script_text)

    url = CATEGORY_URLS[category]
    _open_normal_edge(url)
    time.sleep(6)

    client = OpenAI()
    prompt = f"""
Controla únicamente la interfaz visible de Microsoft Edge en esta PC.

Objetivo:
1. La página pública de Farmacias Guadalajara para la categoría
   {category!r} ya fue abierta en Edge normal.
2. Espera a que la página termine de cargar.
3. Abre DevTools con F12 y entra a la pestaña Console.
4. Hay un script local, revisado por el usuario, ya copiado en el portapapeles.
   Pégalo en Console con Ctrl+V y ejecútalo con Enter. No modifiques el script.
5. Si Edge muestra su advertencia normal que impide pegar en DevTools, sigue
   únicamente la instrucción visible necesaria para habilitar el pegado y
   después pega el script.
6. Espera hasta que la consola indique que terminó y descargó un JSON de
   Farmacias Guadalajara.
7. No inicies sesión, no compres nada, no cambies dirección, no introduzcas
   datos personales y no salgas de farmaciasguadalajara.com salvo DevTools.
8. Si aparece CAPTCHA, 'verifica que eres humano', Access Denied o cualquier
   desafío anti-bot, DETENTE. No intentes resolverlo ni evadirlo.
9. Cuando la descarga haya terminado, responde brevemente con DONE.

Usa computer exclusivamente para la interacción visual.
"""

    response = client.responses.create(
        model=model,
        tools=[{"type": "computer"}],
        input=prompt,
    )

    for _ in range(max_turns):
        calls = _computer_calls(response)
        if not calls:
            print(response.output_text or "GPT terminó sin mensaje.")
            return

        next_input = []
        for call in calls:
            pending = getattr(call, "pending_safety_checks", None) or []
            if pending:
                details = "; ".join(
                    str(getattr(x, "message", None) or getattr(x, "code", x))
                    for x in pending
                )
                raise RuntimeError(
                    "Computer use solicitó una confirmación de seguridad. "
                    f"No se confirmó automáticamente: {details}"
                )

            actions = getattr(call, "actions", None) or []
            for action in actions:
                _execute_action(action)
                time.sleep(0.15)

            next_input.append(
                {
                    "type": "computer_call_output",
                    "call_id": call.call_id,
                    "output": {
                        "type": "computer_screenshot",
                        "image_url": _screenshot_data_url(),
                        "detail": "original",
                    },
                }
            )

        response = client.responses.create(
            model=model,
            tools=[{"type": "computer"}],
            previous_response_id=response.id,
            input=next_input,
        )

    raise RuntimeError(
        f"GPT alcanzó el límite de {max_turns} turnos sin finalizar."
    )


def _find_download(category: str, started_at: float, timeout: int) -> Path:
    downloads = Path.home() / "Downloads"
    pattern = f"farmacias_guadalajara_{category}_*.json"
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        candidates = sorted(
            downloads.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for candidate in candidates:
            if candidate.stat().st_mtime >= started_at - 2:
                return candidate
        time.sleep(1)

    raise RuntimeError(
        f"No apareció un JSON nuevo para {category} en {downloads}"
    )


def _import_json(path: Path) -> int:
    completed = subprocess.run(
        [sys.executable, str(IMPORTER), "--input", str(path)],
        cwd=ROOT,
        check=False,
    )
    return int(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Usa OpenAI computer use para operar Edge normal y ejecutar "
            "la extracción validada de Farmacias Guadalajara."
        )
    )
    parser.add_argument(
        "--category",
        choices=sorted(CATEGORY_URLS),
        required=True,
    )
    parser.add_argument(
        "--model",
        default=os.getenv("OPENAI_COMPUTER_MODEL", "gpt-6.1-sol"),
    )
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--download-timeout", type=int, default=180)
    args = parser.parse_args()

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.08

    started_at = time.time()
    print(f"GPT computer-use model: {args.model}")
    print(f"Categoría: {args.category}")
    print("Abriendo Edge normal, sin Playwright/CDP...")

    try:
        _run_gpt_desktop(
            args.category,
            model=args.model,
            max_turns=args.max_turns,
        )
        json_path = _find_download(
            args.category,
            started_at=started_at,
            timeout=args.download_timeout,
        )
    except Exception as exc:
        print(f"GPT SCRAPER ERROR: {type(exc).__name__}: {exc}")
        return 2

    print(f"JSON descargado: {json_path}")
    print("Importando y validando...")
    return _import_json(json_path)


if __name__ == "__main__":
    raise SystemExit(main())
