from __future__ import annotations

import json
import re
import shutil
import sys
from copy import copy
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS
from scraper.io_utils import atomic_output_path

COMPONENT_DIR = ROOT / "output" / "validated_phase3_components"
OUTPUT_PATH = ROOT / "output" / "concentrado_fase3.xlsx"
BASELINE_PATH = ROOT / "output" / "fase3_muestra_inicial_valida.xlsx"
BASELINE_META = ROOT / "output" / "fase3_muestra_inicial_valida.json"
DIAG_PATH = ROOT / "diagnostics" / "phase3_components" / "last_build.json"

EXPECTED = [
    ("Bodega Aurrera", "cuidado-bucal"),
    ("Bodega Aurrera", "cuidado-de-la-ropa"),
    ("Walmart", "cuidado-bucal"),
    ("Walmart", "cuidado-de-la-ropa"),
    ("Walmart", "depilacion-y-rasurado"),
]


def _slug(value: str) -> str:
    text = value.casefold().strip()
    text = (
        text.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ü", "u")
        .replace("ñ", "n")
    )
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _component_path(retailer: str, category_id: str) -> Path:
    return COMPONENT_DIR / f"{_slug(retailer)}__{category_id}.xlsx"


def _canonical(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for column in COLUMNS:
        if column not in work.columns:
            work[column] = None
    return work[COLUMNS]


def _retailer_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for retailer, subset in frame.groupby("retailer", dropna=False):
        rows.append(
            {
                "retailer": retailer,
                "products": len(subset),
                "categories": int(
                    subset["category_id"]
                    .fillna("")
                    .astype(str)
                    .replace("", pd.NA)
                    .nunique(dropna=True)
                ),
                "sku_complete": int(
                    subset["sku"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .ne("")
                    .sum()
                ),
                "price_complete": int(
                    pd.to_numeric(
                        subset["price_current"],
                        errors="coerce",
                    ).notna().sum()
                ),
                "url_complete": int(
                    subset["url"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .ne("")
                    .sum()
                ),
            }
        )
    return pd.DataFrame(rows).sort_values("retailer").reset_index(drop=True)


def _style(writer: pd.ExcelWriter) -> None:
    wb = writer.book
    for name in ("Concentrado", "Resumen", "Retailers", "Control"):
        ws = wb[name]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            font = copy(cell.font)
            font.bold = True
            cell.font = font
        for cells in ws.columns:
            values = [
                str(cell.value) if cell.value is not None else ""
                for cell in cells[:250]
            ]
            width = min(
                max(max((len(v) for v in values), default=0) + 2, 10),
                42,
            )
            ws.column_dimensions[cells[0].column_letter].width = width


def _write_diag(payload: dict[str, Any]) -> None:
    DIAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DIAG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def main() -> int:
    missing: list[str] = []
    frames: list[pd.DataFrame] = []
    summaries: list[pd.DataFrame] = []
    component_rows: list[dict[str, Any]] = []

    for retailer, category_id in EXPECTED:
        path = _component_path(retailer, category_id)
        if not path.exists():
            missing.append(f"{retailer} / {category_id}")
            continue

        frame = pd.read_excel(
            path,
            sheet_name="Concentrado",
            dtype={"sku": str, "store_id": str, "category_id": str},
        )
        summary = pd.read_excel(
            path,
            sheet_name="Resumen",
            dtype={"category_id": str},
        )
        control = pd.read_excel(path, sheet_name="Control")

        if frame.empty or summary.empty or control.empty:
            missing.append(
                f"{retailer} / {category_id} (componente inválido)"
            )
            continue

        status = str(control.iloc[0].get("status") or "").strip().upper()
        if status != "COMPONENT_VALIDATED":
            missing.append(
                f"{retailer} / {category_id} (status={status!r})"
            )
            continue

        frame = _canonical(frame)
        summary = summary.copy()
        summary["source_status"] = summary["status"].astype(str)
        summary["status"] = "COMPLETE"
        if "source_phase" in summary.columns:
            summary["source_phase"] = "FASE 3"
        else:
            summary.insert(0, "source_phase", "FASE 3")

        frames.append(frame)
        summaries.append(summary)
        component_rows.append(
            {
                "retailer": retailer,
                "category_id": category_id,
                "products": len(frame),
                "component": str(path),
            }
        )

    if missing:
        payload = {
            "status": "NOT_BUILT",
            "missing_components": missing,
            "component_dir": str(COMPONENT_DIR),
        }
        _write_diag(payload)
        print("FASE 3 NO CONSTRUIDA. Faltan componentes validados:")
        for item in missing:
            print(f"- {item}")
        return 2

    concentrated = _canonical(
        pd.concat(frames, ignore_index=True)
    )
    summary = pd.concat(
        summaries,
        ignore_index=True,
        sort=False,
    )

    expected_pairs = set(EXPECTED)
    observed_pairs = set(
        zip(
            summary["retailer"].astype(str),
            summary["category_id"].astype(str),
        )
    )
    if observed_pairs != expected_pairs:
        payload = {
            "status": "NOT_BUILT",
            "reason": "component_set_mismatch",
            "expected": sorted(expected_pairs),
            "observed": sorted(observed_pairs),
        }
        _write_diag(payload)
        print("ERROR: conjunto de componentes distinto al esperado.")
        return 2

    now = datetime.now().astimezone()
    built_at = now.isoformat(timespec="seconds")

    control = pd.DataFrame(
        [
            {
                "status": "PRODUCTIVE",
                "built_at": built_at,
                "products": len(concentrated),
                "categories": len(summary),
                "retailers": 2,
                "all_categories_complete": True,
                "assembly_method": "validated_components_no_rescrape",
                "output": str(OUTPUT_PATH),
            }
        ]
    )
    retailers = _retailer_summary(concentrated)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output_path(OUTPUT_PATH) as temporary:
        with pd.ExcelWriter(temporary, engine="openpyxl") as writer:
            concentrated.to_excel(
                writer,
                index=False,
                sheet_name="Concentrado",
            )
            summary.to_excel(
                writer,
                index=False,
                sheet_name="Resumen",
            )
            retailers.to_excel(
                writer,
                index=False,
                sheet_name="Retailers",
            )
            control.to_excel(
                writer,
                index=False,
                sheet_name="Control",
            )
            pd.DataFrame(component_rows).to_excel(
                writer,
                index=False,
                sheet_name="Componentes",
            )
            _style(writer)

    baseline_created = False
    if not BASELINE_PATH.exists():
        shutil.copy2(OUTPUT_PATH, BASELINE_PATH)
        BASELINE_META.write_text(
            json.dumps(
                {
                    "status": "VALID_PHASE3_BASELINE",
                    "built_at": built_at,
                    "products": len(concentrated),
                    "categories": len(summary),
                    "retailers": 2,
                    "assembly_method": "validated_components_no_rescrape",
                    "source": str(OUTPUT_PATH),
                    "baseline": str(BASELINE_PATH),
                    "components": component_rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        baseline_created = True

    payload = {
        "status": "PRODUCTIVE",
        "built_at": built_at,
        "products": len(concentrated),
        "categories": len(summary),
        "retailers": 2,
        "assembly_method": "validated_components_no_rescrape",
        "output": str(OUTPUT_PATH),
        "baseline": str(BASELINE_PATH),
        "baseline_created": baseline_created,
        "components": component_rows,
    }
    _write_diag(payload)

    print("=" * 80)
    print("FASE 3 CONSTRUIDA DESDE COMPONENTES VALIDADOS")
    print("=" * 80)
    print(f"Filas       : {len(concentrated)}")
    print(f"Categorías  : {len(summary)}")
    print("Retailers   : 2")
    print(f"Archivo     : {OUTPUT_PATH}")
    print(f"Baseline    : {BASELINE_PATH}")
    print("Re-scraping : NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
