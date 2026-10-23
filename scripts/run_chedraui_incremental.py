"""Capture ONLY missing store/category cases from Phase 1, without integration.

Reuse strict same-day progress and validated immutable isolated checkpoints.
Every attempted case uses existing test_chedraui.py and its independent
store-context and data-quality gates. Never overwrite the productive master.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.phase1_state import phase1_inventory, today

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true", help="Navegador visible.")
    parser.add_argument("--dry-run", action="store_true", help="Mostrar pendientes sin navegar.")
    args = parser.parse_args()
    day = today()

    inventory, _ = phase1_inventory(ROOT, day)
    chedraui = inventory.loc[inventory["retailer"].eq("Chedraui")]
    initial_complete = int(chedraui["estado"].eq("COMPLETE").sum())
    pending = chedraui.loc[chedraui["accion"].eq("REINTENTAR")]
    print("=" * 72, flush=True)
    print(f"CHEDRAUI INCREMENTAL - {day}", flush=True)
    print(f"Categorías x tienda: 20 | COMPLETE reutilizados: {initial_complete}", flush=True)
    print(f"Casos que requieren captura: {len(pending)}", flush=True)
    print(pending[["tienda", "store_id", "categoria", "estado"]].to_string(index=False), flush=True)
    print("Master e integración: SIN CAMBIOS", flush=True)
    print("Evidencia selección de tienda: diagnostics/chedraui_store_steps/", flush=True)
    print("=" * 72, flush=True)
    if args.dry_run:
        return 0

    attempted = 0
    failures = 0
    blocked_stores: set[str] = set()
    for _, case in pending.iterrows():
        if day != today():
            print("FECHA CAMBIÓ: se detiene para evitar mezclar días.", flush=True)
            return 3
        store, category = case["tienda"], case["categoria"]
        if store in blocked_stores:
            print(f"OMITIDO {store}/{category}: selección de tienda no verificada.", flush=True)
            continue

        # Recheck progress between attempts: another same-day isolated run
        # may have created a valid checkpoint. Do not repeat verified work.
        fresh, _ = phase1_inventory(ROOT, day)
        entry = fresh.loc[fresh["tienda"].eq(store) & fresh["categoria"].eq(category)]
        if not entry.empty and entry.iloc[0]["estado"] == "COMPLETE":
            print(f"REUTILIZADO {store}/{category}: checkpoint certificado.", flush=True)
            continue

        cmd = [sys.executable, str(ROOT / "scripts" / "test_chedraui.py"),
               "--store", store, "--category", category,
               "--store-context-retries", "2"]
        if args.headed:
            cmd.append("--headed")
        print(f"\nCAPTURANDO {store}/{category}", flush=True)
        attempted += 1
        env = os.environ.copy()
        env["CHEDRAUI_TRACE_STORE"] = "1"
        code = subprocess.call(cmd, cwd=ROOT, env=env)
        refreshed, _ = phase1_inventory(ROOT, day)
        result = refreshed.loc[refreshed["tienda"].eq(store) & refreshed["categoria"].eq(category)]
        success = not result.empty and result.iloc[0]["estado"] == "COMPLETE"
        if success:
            print(f"VALIDADO {store}/{category}: checkpoint guardado.", flush=True)
        else:
            failures += 1
            print(f"PENDIENTE {store}/{category} (exit_code={code}).", flush=True)
            # Context failures are store-level. Stop other pending categories
            # in this store until a human validates the directory selection.
            output = ROOT / "output" / f"chedraui_{store.removeprefix('chedraui-').replace('-', '_')}_test.xlsx"
            try:
                import pandas as pd
                summary = pd.read_excel(output, sheet_name="Resumen")
                if (summary["status"].astype(str).str.upper() == "STORE_CONTEXT_ERROR").any():
                    blocked_stores.add(store)
                    print(f"TIENDA BLOQUEADA PARA ESTA CORRIDA: {store}", flush=True)
            except (OSError, KeyError, ValueError):
                blocked_stores.add(store)
                print(f"TIENDA REQUIERE REVISIÓN MANUAL: {store}", flush=True)

    final, _ = phase1_inventory(ROOT, day)
    completed = int(final["estado"].eq("COMPLETE").sum())
    chedraui_final = final.loc[final["retailer"].eq("Chedraui")]
    completed_c = int(chedraui_final["estado"].eq("COMPLETE").sum())
    print("\n" + "=" * 72, flush=True)
    print("RESUMEN CHEDRAUI INCREMENTAL", flush=True)
    print(f"Fase 1 validada: {completed}/{len(final)}", flush=True)
    print(f"Chedraui validado: {completed_c}/{len(chedraui_final)}", flush=True)
    print(f"Casos intentados: {attempted}; fallidos: {failures}", flush=True)
    print("Master: SIN CAMBIOS; checkpoints SAME-DAY reutilizables.", flush=True)
    print("=" * 72, flush=True)
    return 0 if completed_c == len(chedraui_final) else 2

if __name__ == "__main__":
    raise SystemExit(main())
