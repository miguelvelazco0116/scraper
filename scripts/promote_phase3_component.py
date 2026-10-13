from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS
from scraper.io_utils import atomic_output_path

COMPONENT_DIR = ROOT / "output" / "validated_phase3_components"
CHECKPOINT_DIR = (
    ROOT / "output" / "checkpoints_validos" / "phase3_components"
)


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


def component_path(retailer: str, category_id: str) -> Path:
    return COMPONENT_DIR / f"{_slug(retailer)}__{category_id}.xlsx"


def _canonical(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for column in COLUMNS:
        if column not in work.columns:
            work[column] = None
    return work[COLUMNS]


def _bool_value(value) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes", "si", "sí"}
    return bool(value)


def _validate(
    retailer: str,
    category_id: str,
    frame: pd.DataFrame,
    summary: pd.DataFrame,
) -> list[str]:
    notes: list[str] = []

    if frame.empty:
        notes.append("Concentrado filtrado vacío")
        return notes
    if summary.empty:
        notes.append("Resumen filtrado vacío")
        return notes
    if len(summary) != 1:
        notes.append(f"Resumen tiene {len(summary)} filas; se esperaba 1")
        return notes

    row = summary.iloc[0]
    products_raw = pd.to_numeric(
        row.get("products"),
        errors="coerce",
    )
    products = 0 if pd.isna(products_raw) else int(products_raw)
    if products <= 0:
        notes.append("products <= 0")
    if products != len(frame):
        notes.append(f"products={products} != filas={len(frame)}")

    for metric in ("sku_complete", "price_complete", "url_complete"):
        observed = pd.to_numeric(row.get(metric), errors="coerce")
        if pd.isna(observed) or int(observed) != products:
            notes.append(f"{metric} incompleto")

    if not _bool_value(row.get("catalogue_gate_ok")):
        notes.append("catalogue_gate_ok=False")

    normalized_retailer = retailer.strip().casefold()
    status = str(row.get("status") or "").strip().upper()

    if normalized_retailer == "bodega aurrera":
        if status != "SUCCESS":
            notes.append(f"status={status!r}; se esperaba SUCCESS")
        price_errors = pd.to_numeric(
            row.get("price_validation_errors"), errors="coerce"
        )
        if pd.isna(price_errors) or int(price_errors) != 0:
            notes.append("price_validation_errors != 0")

    elif normalized_retailer == "walmart":
        if status != "COMPLETE":
            notes.append(f"status={status!r}; se esperaba COMPLETE")

        store_complete = pd.to_numeric(
            row.get("store_context_complete"), errors="coerce"
        )
        if pd.isna(store_complete) or int(store_complete) != products:
            notes.append("store_context_complete incompleto")
        if not _bool_value(row.get("store_context_verified")):
            notes.append("store_context_verified=False")

        price_errors = pd.to_numeric(
            row.get("price_order_errors"), errors="coerce"
        )
        if pd.isna(price_errors) or int(price_errors) != 0:
            notes.append("price_order_errors != 0")

        duplicates = pd.to_numeric(
            row.get("duplicates_sku_url"), errors="coerce"
        )
        if pd.isna(duplicates) or int(duplicates) != 0:
            notes.append("duplicates_sku_url != 0")

    else:
        notes.append(f"Retailer no soportado para Fase 3: {retailer}")

    frame_retailers = set(frame["retailer"].dropna().astype(str))
    if frame_retailers != {retailer}:
        notes.append(
            f"Retailers en filas={sorted(frame_retailers)}; esperado={retailer}"
        )

    frame_categories = set(
        frame["category_id"].dropna().astype(str)
    )
    if frame_categories != {category_id}:
        notes.append(
            "category_id en filas="
            f"{sorted(frame_categories)}; esperado={category_id}"
        )

    return notes


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Congela una categoría ya validada de Fase 3 sin volver a scrapear."
        )
    )
    parser.add_argument("--source", required=True)
    parser.add_argument("--retailer", required=True)
    parser.add_argument("--category-id", required=True)
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_absolute():
        source = (ROOT / source).resolve()

    if not source.exists():
        print(f"ERROR: no existe el workbook fuente: {source}")
        return 2

    frame = pd.read_excel(
        source,
        sheet_name="Concentrado",
        dtype={"sku": str, "store_id": str, "category_id": str},
    )
    summary = pd.read_excel(
        source,
        sheet_name="Resumen",
        dtype={"category_id": str},
    )

    frame = frame.loc[
        frame["retailer"].astype(str).eq(args.retailer)
        & frame["category_id"].astype(str).eq(args.category_id)
    ].copy()
    summary = summary.loc[
        summary["retailer"].astype(str).eq(args.retailer)
        & summary["category_id"].astype(str).eq(args.category_id)
    ].copy()

    frame = _canonical(frame)
    notes = _validate(
        args.retailer,
        args.category_id,
        frame,
        summary,
    )
    if notes:
        print("COMPONENTE NO PROMOVIDO:")
        for note in notes:
            print(f"- {note}")
        return 2

    now = datetime.now().astimezone()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    promoted_at = now.isoformat(timespec="seconds")

    COMPONENT_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    destination = component_path(args.retailer, args.category_id)
    checkpoint = (
        CHECKPOINT_DIR
        / f"{destination.stem}__{stamp}.xlsx"
    )

    control = pd.DataFrame(
        [
            {
                "status": "COMPONENT_VALIDATED",
                "retailer": args.retailer,
                "category_id": args.category_id,
                "products": len(frame),
                "promoted_at": promoted_at,
                "source": str(source),
                "component": str(destination),
            }
        ]
    )

    with atomic_output_path(destination) as temporary:
        with pd.ExcelWriter(temporary, engine="openpyxl") as writer:
            frame.to_excel(
                writer,
                index=False,
                sheet_name="Concentrado",
            )
            summary.to_excel(
                writer,
                index=False,
                sheet_name="Resumen",
            )
            control.to_excel(
                writer,
                index=False,
                sheet_name="Control",
            )

    shutil.copy2(destination, checkpoint)
    checkpoint.with_suffix(".json").write_text(
        json.dumps(
            {
                "status": "COMPONENT_VALIDATED",
                "retailer": args.retailer,
                "category_id": args.category_id,
                "products": len(frame),
                "promoted_at": promoted_at,
                "source": str(source),
                "component": str(destination),
                "checkpoint": str(checkpoint),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("=" * 80)
    print("COMPONENTE FASE 3 VALIDADO Y CONGELADO")
    print("=" * 80)
    print(f"Retailer   : {args.retailer}")
    print(f"Categoría  : {args.category_id}")
    print(f"Filas      : {len(frame)}")
    print(f"Componente : {destination}")
    print(f"Checkpoint : {checkpoint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
