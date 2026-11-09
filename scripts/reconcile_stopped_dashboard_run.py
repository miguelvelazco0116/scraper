"""Release only a cancelled, orphaned Streamlit job (never touch captures)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.recovery import reconcile_requested_stop


def main():
    parser = argparse.ArgumentParser(
        description="Verificar paro solicitado, lock y procesos antes de liberar RUNNING")
    parser.add_argument("--run-id", default=None,
                        help="Folio completo de 32 caracteres; opcional si hay un solo paro")
    args = parser.parse_args()
    try:
        result = reconcile_requested_stop(ROOT, args.run_id)
    except (ValueError, RuntimeError, OSError) as exc:
        print("RECUPERACION BLOQUEADA: " + str(exc))
        return 2
    print("FOLIO: " + result["run_id"])
    print("ESTADO: " + str(result["status"]))
    print(result["message"])
    if result.get("backup"):
        print("Respaldo de metadatos: " + str(result["backup"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
