from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUBMIT = ROOT / "scripts" / "fg_submit.py"

CATEGORIES = [
    "cuidado-bucal",
    "lavanderia",
    "preservativos",
    "vias-respiratorias",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ejecuta Farmacias Guadalajara vía Task Scheduler + Edge nativo/CDP"
    )
    parser.add_argument("--category", default="all")
    parser.add_argument("--max-load-more", type=int, default=100)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--browser-channel", default="msedge-cdp")
    args = parser.parse_args()

    categories = CATEGORIES if args.category == "all" else [args.category]
    invalid = [x for x in categories if x not in CATEGORIES]
    if invalid:
        raise SystemExit(f"Categoría no válida: {invalid[0]}")

    results: list[tuple[str, int]] = []

    for category in categories:
        print()
        print("=" * 72)
        print(f"Farmacias Guadalajara / {category}")
        print("=" * 72)

        command = [
            sys.executable,
            str(SUBMIT),
            "--category",
            category,
            "--max-load-more",
            str(args.max_load_more),
            "--browser-channel",
            args.browser_channel,
            "--timeout",
            str(args.timeout),
        ]

        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
        )
        results.append((category, int(completed.returncode)))

        if completed.returncode != 0:
            print(f"ERROR [{category}] exit_code={completed.returncode}")
            break

    print()
    print("RESUMEN FARMACIAS GUADALAJARA")
    for category, returncode in results:
        status = "SUCCESS" if returncode == 0 else "ERROR"
        print(f"{category}: {status} (exit_code={returncode})")

    return 0 if results and all(code == 0 for _, code in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
