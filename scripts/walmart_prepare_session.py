from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser(description="Preparar una sesión persistente de Walmart México")
    parser.add_argument("--profile-dir", default=".walmart_profile")
    parser.add_argument("--store-id", default="2344")
    args = parser.parse_args()

    profile = Path(args.profile_dir)
    profile.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            locale="es-MX",
            viewport={"width": 1440, "height": 1000},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(f"https://www.walmart.com.mx/tienda/{args.store_id}", wait_until="domcontentloaded", timeout=120_000)

        print("\nSe abrió Walmart en un navegador normal con perfil persistente.")
        print("1. Completa manualmente cualquier verificación que Walmart solicite.")
        print("2. Confirma que la tienda seleccionada sea SC Toreo y CP 11220.")
        print("3. No cierres el navegador hasta regresar aquí.")
        input("\nCuando la sesión esté lista, presiona ENTER para guardarla y cerrar... ")
        context.close()

    print(f"Perfil guardado en: {profile.resolve()}")
    print("No subas esta carpeta a GitHub ni la compartas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
