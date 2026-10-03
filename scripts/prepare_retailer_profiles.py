from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright


RETAILERS = {
    "soriana": {
        "url": "https://www.soriana.com/",
        "profile": ROOT / ".soriana_profile",
        "message": (
            "Verifica que el home cargue normalmente. Si aparece una "
            "verificación del sitio, complétala manualmente. No es necesario "
            "navegar categorías."
        ),
    },
    "chedraui": {
        "url": "https://www.chedraui.com.mx/",
        "profile": ROOT / ".chedraui_profile",
        "message": (
            "Verifica que la ubicación sea Chedraui Selecto México Polanco. "
            "Si no lo es, selecciona Pickup > Polanco (CP 11500) manualmente "
            "una sola vez."
        ),
    },
}


def prepare(retailer: str) -> None:
    cfg = RETAILERS[retailer]
    profile = Path(cfg["profile"])
    profile.mkdir(parents=True, exist_ok=True)

    print("")
    print("=" * 72)
    print(f"PREPARAR PERFIL: {retailer.upper()}")
    print("=" * 72)
    print(f"Perfil : {profile}")
    print(f"URL    : {cfg['url']}")
    print(cfg["message"])
    print("")
    print(
        "Cuando la sesión esté lista, vuelve a esta consola y presiona ENTER."
    )

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel="chrome",
            headless=False,
            locale="es-MX",
            viewport={"width": 1440, "height": 1000},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(
            cfg["url"],
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_timeout(2_000)
        input()
        context.close()

    print(f"Perfil guardado: {profile}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepara perfiles persistentes locales de retailers."
    )
    parser.add_argument(
        "--retailer",
        choices=["soriana", "chedraui", "all"],
        default="all",
    )
    args = parser.parse_args()

    targets = list(RETAILERS) if args.retailer == "all" else [args.retailer]
    for retailer in targets:
        prepare(retailer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
