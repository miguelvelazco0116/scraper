from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from openpyxl.styles import Font

from main import COLUMNS
from scraper.quality_rules import meets_rate, valid_after_errors

CATEGORY_ORDER = [
    "vias-respiratorias",
    "lavanderia",
    "cuidado-bucal",
    "preservativos",
]

OUTPUT = ROOT / "output" / "farmacias_guadalajara_phase2_test.xlsx"
TEMP_DIR = ROOT / "output" / "_farmacias_guadalajara_phase2_raw_cdp"
DIAG_DIR = ROOT / "diagnostics" / "phase2" / "farmacias_guadalajara_raw_cdp"



def _run_category(
    *,
    category_id: str,
    cdp_url: str,
    category_output: Path,
    meta_path: Path,
    attempts: int = 2,
) -> subprocess.CompletedProcess:
    last: subprocess.CompletedProcess | None = None

    for attempt in range(1, attempts + 1):
        category_output.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)

        if attempt > 1:
            print(
                f"RETRY {attempt}/{attempts}: {category_id} "
                "usando el mismo método raw-CDP validado"
            )
            time.sleep(2.0)

        cmd = [
            sys.executable,
            str(
                ROOT
                / "scripts"
                / "scrape_farmacias_guadalajara_phase2_raw_cdp.py"
            ),
            "--category",
            category_id,
            "--ws-url",
            cdp_url,
            "--output",
            str(category_output),
        ]

        last = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
        )

        if last.returncode == 0:
            return last

        print(
            f"Intento {attempt}/{attempts} falló "
            f"para {category_id}: return_code={last.returncode}"
        )

    assert last is not None
    return last


def _invalid_product_name(value: object) -> bool:
    text = str(value or "").strip()
    lowered = text.casefold()
    if not text:
        return True
    return (
        lowered in {"price", "sale price", "precio", "oferta"}
        or lowered.startswith("price reduced from")
        or lowered.startswith("price reduced to")
        or lowered.startswith("reduced from")
        or lowered.startswith("reduced to")
    )


def _summary_is_acceptable(summary: dict) -> bool:
    """Reuse only complete, numeric, validated category results.

    Excel loads blank metrics as NaN; bool(float('nan')) is True,
    so expressions such as int(value or 0) are unsafe.
    """
    if str(summary.get("status") or "").strip().upper() != "SUCCESS":
        return False

    def metric(name: str) -> int | None:
        value = pd.to_numeric(summary.get(name), errors="coerce")
        if pd.isna(value):
            return None
        try:
            return int(value) if float(value) >= 0 else None
        except (TypeError, ValueError, OverflowError):
            return None

    products = metric("products")
    target = metric("target_products")
    if products is None or target is None or products <= 0 or target <= 0:
        return False

    metrics = {
        name: metric(name)
        for name in (
            "product_name_complete", "product_name_invalid",
            "sku_complete", "price_complete", "url_complete",
        )
    }
    if any(value is None for value in metrics.values()):
        return False

    return bool(
        meets_rate(products, target)
        and meets_rate(metrics["product_name_complete"], products)
        and valid_after_errors(products, metrics["product_name_invalid"])
        and meets_rate(metrics["sku_complete"], products)
        and meets_rate(metrics["price_complete"], products)
        and meets_rate(metrics["url_complete"], products)
    )


def _load_existing_valid_results(
    categories: list[str],
) -> dict[str, tuple[pd.DataFrame, dict]]:
    if not OUTPUT.exists():
        return {}
    try:
        concentrated = pd.read_excel(
            OUTPUT,
            sheet_name="Concentrado",
            dtype={"sku": str, "store_id": str, "category_id": str},
        )
        summary_df = pd.read_excel(
            OUTPUT,
            sheet_name="Resumen",
            dtype={"category_id": str},
        )
    except Exception:
        return {}

    if concentrated.empty or summary_df.empty:
        return {}

    today = datetime.now().astimezone().date().isoformat()
    dates = set(
        concentrated["scrape_timestamp"]
        .dropna()
        .astype(str)
        .str.slice(0, 10)
    )
    if dates != {today}:
        return {}

    configured = set(categories)
    results: dict[str, tuple[pd.DataFrame, dict]] = {}
    for _, row in summary_df.iterrows():
        category_id = str(row.get("category_id") or "").strip()
        if category_id not in configured:
            continue
        summary = row.to_dict()
        if not _summary_is_acceptable(summary):
            continue
        frame = concentrated.loc[
            concentrated["category_id"].astype(str).eq(category_id)
        ].copy()
        if len(frame) != int(summary["products"]):
            continue
        results[category_id] = (frame, summary)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prueba completa de Farmacias Guadalajara usando CDP directo."
    )
    parser.add_argument("--cdp-url", required=True)
    parser.add_argument("--category", default="all")
    args = parser.parse_args()

    configured_categories = list(CATEGORY_ORDER)
    categories = list(configured_categories)
    if args.category != "all":
        if args.category not in CATEGORY_ORDER:
            raise SystemExit(f"Categoría no encontrada: {args.category}")
        categories = [args.category]

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    existing = (
        _load_existing_valid_results(configured_categories)
        if args.category == "all"
        else {}
    )
    frames: list[pd.DataFrame] = [
        existing[item][0].copy()
        for item in configured_categories
        if item in existing
    ]
    summaries: list[dict] = [
        dict(existing[item][1])
        for item in configured_categories
        if item in existing
    ]
    if existing:
        print("CATEGORÍAS GUADALAJARA YA VÁLIDAS HOY:")
        for category_id in configured_categories:
            if category_id in existing:
                print(
                    f"- {category_id}: REUTILIZADA, NO SE DESCARGA DE NUEVO"
                )
        categories = [
            item for item in categories if item not in existing
        ]

    print("=" * 84)
    print("FARMACIAS GUADALAJARA - PRUEBA COMPLETA CDP DIRECTO")
    print("=" * 84)
    print(f"Categorías : {', '.join(categories)}")
    print("")

    for index, category_id in enumerate(categories, start=1):
        print("-" * 84)
        print(f"[{index}/{len(categories)}] {category_id}")

        category_output = TEMP_DIR / f"{category_id}.xlsx"
        meta_path = DIAG_DIR / f"{category_id}_meta.json"

        completed = _run_category(
            category_id=category_id,
            cdp_url=args.cdp_url,
            category_output=category_output,
            meta_path=meta_path,
            attempts=2,
        )

        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}

        frame = pd.DataFrame(columns=COLUMNS)
        if category_output.exists():
            try:
                frame = pd.read_excel(
                    category_output,
                    sheet_name="Concentrado",
                    dtype={"sku": str, "store_id": str},
                )
            except Exception:
                frame = pd.DataFrame(columns=COLUMNS)

        if not frame.empty:
            for column in COLUMNS:
                if column not in frame.columns:
                    frame[column] = None
            frame = frame[COLUMNS].copy()
            frames.append(frame)

        target = meta.get("target_products")
        products = len(frame)
        coverage = (
            round(products / target, 4)
            if isinstance(target, int) and target > 0
            else None
        )

        if completed.returncode == 0:
            status = "SUCCESS"
            if products == 0:
                status = "EMPTY"
            elif coverage is not None and coverage < 0.90:
                status = "PARTIAL"
        else:
            status = f"ERROR_{completed.returncode}"

        product_name_complete = int(
            frame["product"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not frame.empty else 0
        product_name_invalid = int(
            frame["product"].apply(_invalid_product_name).sum()
        ) if not frame.empty else 0
        sku_complete = int(
            frame["sku"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not frame.empty else 0
        price_complete = int(
            frame["price_current"].notna().sum()
        ) if not frame.empty else 0
        url_complete = int(
            frame["url"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not frame.empty else 0

        if completed.returncode == 0 and status == "SUCCESS":
            if (
                not meets_rate(product_name_complete, products)
                or not valid_after_errors(products, product_name_invalid)
                or not meets_rate(sku_complete, products)
                or not meets_rate(price_complete, products)
                or not meets_rate(url_complete, products)
            ):
                status = "PARTIAL"

        retry_attempts = 1 if completed.returncode == 0 else 2
        summary = {
            "retailer": "Farmacias Guadalajara",
            "category_id": category_id,
            "status": status,
            "target_products": target,
            "product_links": meta.get("product_links"),
            "products": products,
            "product_name_complete": product_name_complete,
            "product_name_invalid": product_name_invalid,
            "coverage": coverage,
            "sku_complete": sku_complete,
            "price_complete": price_complete,
            "url_complete": url_complete,
            "expansion_rounds": len(meta.get("expansion_trace") or []),
            "return_code": completed.returncode,
            "max_attempts": 2,
        }
        summaries.append(summary)

        print(
            f"FULL_TEST: {status} | target={target} | "
            f"products={products} | coverage={coverage} | "
            f"name={product_name_complete} | invalid_name={product_name_invalid} | "
            f"sku={sku_complete} | price={price_complete} | url={url_complete}"
        )

        if completed.returncode != 0:
            print(
                "La prueba se detiene después del retry controlado "
                "para no forzar la sesión."
            )
            break

    concentrated = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=COLUMNS)
    )
    summary_df = pd.DataFrame(summaries)

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        concentrated.to_excel(writer, index=False, sheet_name="Concentrado")
        summary_df.to_excel(writer, index=False, sheet_name="Resumen")

        for sheet_name in ("Concentrado", "Resumen"):
            ws = writer.book[sheet_name]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = Font(bold=True)

    print("")
    print("=" * 84)
    print("RESUMEN FINAL - FARMACIAS GUADALAJARA")
    print("=" * 84)
    if not summary_df.empty:
        print(summary_df.to_string(index=False))
    print(f"Filas totales: {len(concentrated)}")
    print(f"Output      : {OUTPUT}")
    print("Consolidado : sin cambios")

    if summary_df.empty:
        return 2

    expected_count = (
        len(configured_categories)
        if args.category == "all"
        else 1
    )
    if len(summary_df) != expected_count:
        return 2
    acceptable = all(
        _summary_is_acceptable(row)
        for row in summary_df.to_dict("records")
    )
    return 0 if acceptable else 2


if __name__ == "__main__":
    raise SystemExit(main())
