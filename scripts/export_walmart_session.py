from __future__ import annotations

import argparse
import base64
import gzip
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

STORE_URL = "https://www.walmart.com.mx/tienda/2344"


def main() -> int:
    parser = argparse.ArgumentParser(description="Exportar una sesión verificada de Walmart/SC Toreo")
    parser.add_argument("--out", default="walmart_session.json")
    parser.add_argument("--secret-out", default="walmart_session.secret.txt")
    parser.add_argument("--profile-dir", default=".walmart_export_profile")
    args = parser.parse_args()

    out = Path(args.out).resolve()
    secret_out = Path(args.secret_out).resolve()
    profile = Path(args.profile_dir).resolve()
    profile.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            locale="es-MX",
            viewport={"width": 1440, "height": 1000},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(STORE_URL, wait_until="domcontentloaded", timeout=120_000)
            print("\nEn Chromium:")
            print("1. Completa manualmente cualquier verificación que Walmart muestre.")
            print("2. Confirma SC Toreo / CP 11220 como tienda de Pickup.")
            print("3. Abre una categoría o producto y verifica que el contexto de tienda sea correcto.")
            print("4. Regresa a esta terminal y presiona ENTER.\n")
            input()

            storage_state = context.storage_state(indexed_db=True)
            session_storage: dict[str, dict[str, str]] = {}
            for current in context.pages:
                try:
                    host = current.evaluate("() => window.location.hostname")
                    if host and "walmart.com.mx" in host:
                        values = current.evaluate(
                            "() => Object.fromEntries(Object.entries(window.sessionStorage))"
                        )
                        session_storage[host] = values
                except Exception:
                    continue

            payload = {
                "format": "walmart-portable-session-v1",
                "store": "SC Toreo",
                "store_id": "2344",
                "postal_code": "11220",
                "storage_state": storage_state,
                "session_storage": session_storage,
            }
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            out.write_bytes(raw)

            secret_value = base64.b64encode(gzip.compress(raw, compresslevel=9)).decode("ascii")
            secret_out.write_text(secret_value, encoding="ascii")

            print(f"Sesión exportada: {out}")
            print(f"Valor comprimido para GitHub Secret: {secret_out}")
            print(f"Tamaño del Secret: {len(secret_value):,} caracteres")
            if len(secret_value) > 48_000:
                print("ADVERTENCIA: el valor supera ~48 KB; elimina datos innecesarios y vuelve a exportar.")
            print("IMPORTANTE: ambos archivos contienen estado sensible. No los subas al repositorio.")
        finally:
            context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
