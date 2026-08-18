from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

CATEGORIES = ("cuidado-bucal", "cuidado-de-la-ropa")


def run(cmd: list[str]) -> int:
    print("\n$", " ".join(cmd))
    return subprocess.call(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepara la sesión de SC Toreo y ejecuta las dos categorías de Walmart."
    )
    parser.add_argument("--profile-dir", default=".walmart_profile")
    parser.add_argument("--skip-session-setup", action="store_true")
    parser.add_argument("--max-pages", type=int, default=100)
    args = parser.parse_args()

    profile = Path(args.profile_dir)

    if not args.skip_session_setup:
        rc = run(
            [
                sys.executable,
                "scripts/walmart_prepare_session.py",
                "--profile-dir",
                str(profile),
                "--store-id",
                "2344",
            ]
        )
        if rc != 0:
            print("No se pudo preparar la sesión de Walmart.")
            return rc

    failures: list[tuple[str, int]] = []
    for category in CATEGORIES:
        rc = run(
            [
                sys.executable,
                "main.py",
                "--retailer",
                "walmart",
                "--category",
                category,
                "--store",
                "sc-toreo",
                "--profile-dir",
                str(profile),
                "--max-load-more",
                str(args.max_pages),
            ]
        )
        if rc != 0:
            failures.append((category, rc))

    print("\nArchivos esperados:")
    print("  output/walmart_cuidado_bucal_sc_toreo.xlsx")
    print("  output/walmart_cuidado_de_la_ropa_sc_toreo.xlsx")

    if failures:
        print("\nUna o más categorías no terminaron correctamente:")
        for category, rc in failures:
            print(f"  - {category}: exit code {rc}")
        print("Revisa diagnostics/ antes de reintentar.")
        return 1

    print("\nExtracción completa finalizada para SC Toreo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
