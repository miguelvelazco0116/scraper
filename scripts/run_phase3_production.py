from __future__ import annotations

import argparse
import json
import shutil
import subprocess
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
from scraper.quality_rules import meets_rate, valid_after_errors

OUTPUT_PATH = ROOT / "output" / "concentrado_fase3.xlsx"
BASELINE_PATH = ROOT / "output" / "fase3_muestra_inicial_valida.xlsx"
BASELINE_META = ROOT / "output" / "fase3_muestra_inicial_valida.json"

BODEGA_OUTPUT = ROOT / "output" / "bodega_aurrera_test.xlsx"
WALMART_OUTPUT = ROOT / "output" / "walmart_phase3_test.xlsx"

DIAG_DIR = ROOT / "diagnostics" / "phase3_production"
DIAG_PATH = DIAG_DIR / "last_run.json"

EXPECTED_BODEGA_CATEGORIES = 2
EXPECTED_WALMART_CATEGORIES = 3


def _canonical(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for column in COLUMNS:
        if column not in work.columns:
            work[column] = None
    return work[COLUMNS]


def _run_child(label: str, cmd: list[str]) -> int:
    print("")
    print("=" * 84)
    print(label)
    print("=" * 84)
    completed = subprocess.run(cmd, cwd=ROOT, text=True)
    print(f"{label} exit_code={completed.returncode}")
    return int(completed.returncode)


def _read_sheet(path: Path, sheet: str) -> pd.DataFrame:
    return pd.read_excel(
        path,
        sheet_name=sheet,
        dtype={"sku": str, "store_id": str, "category_id": str},
    )


def _validate_bodega(
    frame: pd.DataFrame,
    summary: pd.DataFrame,
) -> list[str]:
    notes: list[str] = []
    if len(summary) != EXPECTED_BODEGA_CATEGORIES:
        notes.append(
            f"Bodega categorías {len(summary)}/{EXPECTED_BODEGA_CATEGORIES}"
        )
        return notes

    if not summary["status"].astype(str).eq("SUCCESS").all():
        notes.append("Bodega tiene categorías no SUCCESS")

    products = pd.to_numeric(summary["products"], errors="coerce").fillna(-1)
    if int(products.sum()) != len(frame):
        notes.append(
            f"Bodega suma products={int(products.sum())} != filas={len(frame)}"
        )

    for metric in ("sku_complete", "price_complete", "url_complete"):
        values = pd.to_numeric(summary[metric], errors="coerce").fillna(-2)
        if not all(
            meets_rate(value, total)
            for value, total in zip(values, products)
        ):
            notes.append(f"Bodega {metric} < 90%")

    if "catalogue_gate_ok" not in summary.columns:
        notes.append("Bodega sin catalogue_gate_ok")
    elif not summary["catalogue_gate_ok"].map(bool).all():
        notes.append("Bodega cobertura/paginación por debajo del gate")

    discovered = pd.to_numeric(
        summary["sources_discovered"], errors="coerce"
    ).fillna(-1)
    visited = pd.to_numeric(
        summary["sources_visited"], errors="coerce"
    ).fillna(-2)
    if not discovered.eq(visited).all():
        notes.append("Bodega fuentes descubiertas != visitadas")

    price_errors = pd.to_numeric(
        summary["price_validation_errors"], errors="coerce"
    ).fillna(-1)
    if not all(
        valid_after_errors(total, errors)
        for total, errors in zip(products, price_errors)
    ):
        notes.append("Bodega validación de precios < 90%")

    if summary["blocked_detected"].map(bool).any():
        notes.append("Bodega blocked_detected=True")

    return notes


def _validate_walmart(
    frame: pd.DataFrame,
    summary: pd.DataFrame,
    control: pd.DataFrame,
) -> list[str]:
    notes: list[str] = []
    if len(summary) != EXPECTED_WALMART_CATEGORIES:
        notes.append(
            f"Walmart categorías {len(summary)}/{EXPECTED_WALMART_CATEGORIES}"
        )
        return notes

    if not summary["status"].astype(str).eq("COMPLETE").all():
        notes.append("Walmart tiene categorías no COMPLETE")

    if control.empty:
        notes.append("Walmart Control vacío")
    else:
        status = str(control.iloc[0].get("status") or "").strip().upper()
        if status != "PRODUCTIVE":
            notes.append(f"Walmart Control status={status!r}")

    products = pd.to_numeric(summary["products"], errors="coerce").fillna(-1)
    if int(products.sum()) != len(frame):
        notes.append(
            f"Walmart suma products={int(products.sum())} != filas={len(frame)}"
        )

    for metric in (
        "sku_complete",
        "price_complete",
        "url_complete",
        "store_context_complete",
    ):
        values = pd.to_numeric(summary[metric], errors="coerce").fillna(-2)
        if not all(
            meets_rate(value, total)
            for value, total in zip(values, products)
        ):
            notes.append(f"Walmart {metric} < 90%")

    if not summary["store_context_verified"].map(bool).all():
        notes.append("Walmart store_context_verified=False")

    if "catalogue_gate_ok" not in summary.columns:
        notes.append("Walmart sin catalogue_gate_ok")
    elif not summary["catalogue_gate_ok"].map(bool).all():
        notes.append("Walmart cobertura/paginación por debajo del gate")

    price_errors = pd.to_numeric(
        summary["price_order_errors"], errors="coerce"
    ).fillna(-1)
    if not all(
        valid_after_errors(total, errors)
        for total, errors in zip(products, price_errors)
    ):
        notes.append("Walmart orden de precios válido < 90%")

    duplicates = pd.to_numeric(
        summary["duplicates_sku_url"], errors="coerce"
    ).fillna(-1)
    if not duplicates.eq(0).all():
        notes.append("Walmart duplicates_sku_url > 0")

    return notes


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
                        subset["price_current"], errors="coerce"
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


def _write_diagnostic(payload: dict[str, Any]) -> None:
    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    DIAG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _create_baseline(
    *,
    generated_at: str,
    products: int,
    bodega_products: int,
    walmart_products: int,
) -> bool:
    if BASELINE_PATH.exists():
        return False

    try:
        shutil.copy2(OUTPUT_PATH, BASELINE_PATH)
        with atomic_output_path(BASELINE_META) as temporary:
            temporary.write_text(
                json.dumps(
                    {
                        "status": "VALID_PHASE3_BASELINE",
                        "generated_at": generated_at,
                        "source": str(OUTPUT_PATH),
                        "baseline": str(BASELINE_PATH),
                        "products": products,
                        "bodega_aurrera_products": bodega_products,
                        "walmart_products": walmart_products,
                        "categories": 5,
                        "retailers": [
                            "Bodega Aurrera",
                            "Walmart",
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    except Exception:
        BASELINE_PATH.unlink(missing_ok=True)
        BASELINE_META.unlink(missing_ok=True)
        raise
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fase 3 productiva sobre Chrome abierto: "
            "Bodega Aurrera + Walmart."
        )
    )
    parser.add_argument(
        "--cdp-url",
        required=True,
        help="WebSocket CDP del Chrome abierto manualmente.",
    )
    args = parser.parse_args()

    now = datetime.now().astimezone()
    generated_at = now.isoformat(timespec="seconds")

    print("=" * 84)
    print("SCRAPER PRODUCTIVO - FASE 3")
    print("=" * 84)
    print("Retailers : Bodega Aurrera + Walmart")
    print("Categorías: 2 + 3 = 5")
    print("Navegador : Chrome abierto manualmente")
    print(f"CDP       : {args.cdp_url}")
    print(f"Salida    : {OUTPUT_PATH}")
    print("")

    # Do not delete retailer checkpoints here. The child runners reuse
    # same-day successful categories and retry only pending failures.

    bodega_code = _run_child(
        "BODEGA AURRERA",
        [
            sys.executable,
            str(ROOT / "scripts" / "test_bodega_aurrera.py"),
            "--cdp-url",
            args.cdp_url,
        ],
    )
    if bodega_code != 0:
        _write_diagnostic(
            {
                "status": "NOT_PUBLISHED",
                "generated_at": generated_at,
                "bodega_exit_code": bodega_code,
                "walmart_exit_code": None,
                "notes": ["Bodega Aurrera falló; Walmart no se ejecutó."],
            }
        )
        print("FASE 3 NO PUBLICADA.")
        return 2

    walmart_code = _run_child(
        "WALMART",
        [
            sys.executable,
            str(ROOT / "scripts" / "test_walmart_phase3.py"),
            "--cdp-url",
            args.cdp_url,
        ],
    )

    notes: list[str] = []
    if walmart_code != 0:
        notes.append(f"Walmart exit_code={walmart_code}")

    try:
        bodega_frame = _canonical(
            _read_sheet(BODEGA_OUTPUT, "Concentrado")
        )
        bodega_summary = _read_sheet(BODEGA_OUTPUT, "Resumen")
    except Exception as exc:
        notes.append(
            f"No se pudo leer Bodega: {type(exc).__name__}: {exc}"
        )
        bodega_frame = pd.DataFrame(columns=COLUMNS)
        bodega_summary = pd.DataFrame()

    try:
        walmart_frame = _canonical(
            _read_sheet(WALMART_OUTPUT, "Concentrado")
        )
        walmart_summary = _read_sheet(WALMART_OUTPUT, "Resumen")
        walmart_control = _read_sheet(WALMART_OUTPUT, "Control")
    except Exception as exc:
        notes.append(
            f"No se pudo leer Walmart: {type(exc).__name__}: {exc}"
        )
        walmart_frame = pd.DataFrame(columns=COLUMNS)
        walmart_summary = pd.DataFrame()
        walmart_control = pd.DataFrame()

    if not bodega_summary.empty:
        notes.extend(
            _validate_bodega(bodega_frame, bodega_summary)
        )
    if not walmart_summary.empty:
        notes.extend(
            _validate_walmart(
                walmart_frame,
                walmart_summary,
                walmart_control,
            )
        )

    if notes:
        _write_diagnostic(
            {
                "status": "NOT_PUBLISHED",
                "generated_at": generated_at,
                "bodega_exit_code": bodega_code,
                "walmart_exit_code": walmart_code,
                "notes": notes,
            }
        )
        print("")
        print("FASE 3 NO PUBLICADA")
        for note in notes:
            print(f"- {note}")
        print(f"Diagnóstico: {DIAG_PATH}")
        return 2

    bodega_phase_summary = bodega_summary.copy()
    bodega_phase_summary["source_status"] = (
        bodega_phase_summary["status"].astype(str)
    )
    bodega_phase_summary["status"] = "COMPLETE"

    walmart_phase_summary = walmart_summary.copy()

    summary = pd.concat(
        [bodega_phase_summary, walmart_phase_summary],
        ignore_index=True,
        sort=False,
    )
    summary.insert(0, "source_phase", "FASE 3")

    concentrated = pd.concat(
        [bodega_frame, walmart_frame],
        ignore_index=True,
    )
    concentrated = _canonical(concentrated)

    bodega_products = len(bodega_frame)
    walmart_products = len(walmart_frame)

    control = pd.DataFrame(
        [
            {
                "status": "PRODUCTIVE",
                "generated_at": generated_at,
                "products": len(concentrated),
                "categories": len(summary),
                "retailers": 2,
                "bodega_aurrera_products": bodega_products,
                "walmart_products": walmart_products,
                "all_categories_complete": True,
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
            _style(writer)

    baseline_created = _create_baseline(
        generated_at=generated_at,
        products=len(concentrated),
        bodega_products=bodega_products,
        walmart_products=walmart_products,
    )

    payload = {
        "status": "PRODUCTIVE",
        "generated_at": generated_at,
        "products": len(concentrated),
        "categories": len(summary),
        "bodega_aurrera_products": bodega_products,
        "walmart_products": walmart_products,
        "output": str(OUTPUT_PATH),
        "baseline": str(BASELINE_PATH),
        "baseline_created": baseline_created,
    }
    _write_diagnostic(payload)

    print("")
    print("=" * 84)
    print("RESUMEN FASE 3")
    print("=" * 84)
    print(
        summary[
            [
                "retailer",
                "category_id",
                "status",
                "products",
                "sku_complete",
                "price_complete",
                "url_complete",
                "quality_notes",
            ]
        ].to_string(index=False)
    )
    print("")
    print("FASE 3 PUBLICADA CORRECTAMENTE")
    print(f"Filas productivas : {len(concentrated)}")
    print(f"Bodega Aurrera    : {bodega_products}")
    print(f"Walmart           : {walmart_products}")
    print(f"Archivo productivo: {OUTPUT_PATH}")
    print(f"Baseline Fase 3   : {BASELINE_PATH}")
    print(
        "Baseline status   : "
        + (
            "CREADA - MUESTRA INICIAL VALIDA"
            if baseline_created
            else "CONSERVADA"
        )
    )
    print(f"Diagnóstico       : {DIAG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
