"""Six-category Farmacias del Ahorro isolated capture; never integrates a master."""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import COLUMNS
from run_phase1_production import _run_ahorro
from scraper.config import load_categories, load_locations
from scraper.io_utils import atomic_output_path

OUT = ROOT / "output" / "farmacias_del_ahorro_all_test.xlsx"

def main():
    categories = load_categories(ROOT / "config" / "farmacias-del-ahorro" / "categories.yaml")
    location = next(p for p in load_locations(ROOT / "config" / "locations.yaml") if p.id == "fahorro-online")
    frames, summaries = _run_ahorro(categories, location, low_memory=True)
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    summary = pd.DataFrame(summaries)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        folder = ROOT / "output" / "checkpoints_validos"
        folder.mkdir(parents=True, exist_ok=True)
        checkpoint = folder / ("before_ahorro_all_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".xlsx")
        shutil.copy2(OUT, checkpoint)
        print("Checkpoint previo:", checkpoint)
    with atomic_output_path(OUT) as temp:
        with pd.ExcelWriter(temp, engine="openpyxl") as excel:
            combined.to_excel(excel, sheet_name="Concentrado", index=False)
            summary.to_excel(excel, sheet_name="Resumen", index=False)
    print(summary[["category_id", "status", "products"]].to_string(index=False))
    print("Total productos:", len(combined), "| Salida:", OUT)
    print("Master productivo: SIN MODIFICAR")
    return 0 if len(summary) == len(categories) and summary["status"].eq("COMPLETE").all() else 2

if __name__ == "__main__":
    raise SystemExit(main())
