from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.retailers.soriana import (
    SorianaBlocked,
    SorianaScraper,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preparar/verificar la sesión persistente de Soriana"
    )
    parser.add_argument(
        "--profile-dir",
        default=str(ROOT / ".soriana_profile"),
    )
    parser.add_argument(
        "--force-check",
        action="store_true",
        help="Consulta el homepage aunque el circuit breaker siga en cooldown.",
    )
    args = parser.parse_args()

    scraper = SorianaScraper(
        headless=False,
        browser_channel="chrome",
        diagnostics_dir=ROOT / "diagnostics" / "soriana_session",
        profile_dir=Path(args.profile_dir),
        circuit_cooldown_seconds=600,
    )

    try:
        result = scraper.prepare_session(force_check=args.force_check)
    except SorianaBlocked as exc:
        print(f"BLOCKED: {exc}")
        print(
            "El perfil sigue bloqueado. No se lanzarán categorías hasta "
            "que venza el cooldown."
        )
        return 2

    status = result.get("status")
    if status == "DEFERRED":
        print(
            "DEFERRED: circuit breaker activo; "
            f"remaining_seconds={result.get('remaining_seconds')}"
        )
        return 6

    print("SORIANA_SESSION_READY")
    print(f"http_status={result.get('http_status')}")
    print(f"url={result.get('url')}")
    print(f"profile_dir={result.get('profile_dir')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
