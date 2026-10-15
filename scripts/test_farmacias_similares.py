from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS
from scraper.config import load_categories, load_locations
from scraper.quality_rules import meets_rate, valid_after_errors
from scraper.retailers.farmacias_similares import (
    FarmaciasSimilaresBlocked,
    FarmaciasSimilaresNetworkUnavailable,
    FarmaciasSimilaresScraper,
)


OUTPUT_PATH = ROOT / "output" / "farmacias_similares_test.xlsx"


def run_category(category, location):
    print("")
    print("-" * 72)
    print(f"FARMACIAS SIMILARES | {category.id}")
    print("-" * 72)
    print(f"Departamento : {category.department}")
    print(f"Categoría    : {category.name}")
    print(f"Subcategoría : {category.subcategory}")
    print(f"URL          : {category.url}")
    print("")

    scraper = FarmaciasSimilaresScraper(
        headless=False,
        browser_channel="chrome",
        max_pages=20,
        wait_ms=900,
        manual_verification_timeout_ms=180_000,
    )

    rows = []
    status = "SUCCESS"
    error = None

    try:
        rows = scraper.scrape_category(category, location)
        status = str(scraper.run_meta.get("status") or "SUCCESS")
    except FarmaciasSimilaresBlocked as exc:
        status = "BLOCKED"
        error = str(exc)
    except FarmaciasSimilaresNetworkUnavailable as exc:
        status = "NETWORK_UNAVAILABLE"
        error = str(exc)
    except Exception as exc:
        status = "ERROR"
        error = f"{type(exc).__name__}: {exc}"

    df = pd.DataFrame(rows, columns=COLUMNS)
    if not df.empty:
        df = (
            df.drop_duplicates(subset=["sku", "url"], keep="last")
            .sort_values(["brand", "product"], na_position="last")
            .reset_index(drop=True)
        )

    meta = scraper.run_meta or {}
    no_price = meta.get("products_without_price") or []
    detail_errors = meta.get("detail_errors") or []

    promotional_products = 0
    promotion_price_errors = 0
    if not df.empty:
        current = pd.to_numeric(df["price_current"], errors="coerce")
        regular = pd.to_numeric(df["price_regular"], errors="coerce")
        promotional_products = int((current < regular).sum())

        promo_text = df["promotion"].fillna("").astype(str).str.strip()
        promotion_price_errors = int(
            (
                promo_text.ne("")
                & (
                    current.isna()
                    | regular.isna()
                    | (current >= regular)
                )
            ).sum()
        )

    summary = {
        "retailer": "Farmacias Similares",
        "department": category.department,
        "category": category.name,
        "subcategory": category.subcategory,
        "category_id": category.id,
        "status": status,
        "target_products": meta.get("target_products"),
        "product_links": meta.get("product_links"),
        "discovery_complete": bool(meta.get("discovery_complete")),
        "products": len(df),
        "sku_complete": int(
            df["sku"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not df.empty else 0,
        "price_complete": int(df["price_current"].notna().sum())
        if not df.empty else 0,
        "regular_price_complete": int(df["price_regular"].notna().sum())
        if not df.empty else 0,
        "url_complete": int(
            df["url"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not df.empty else 0,
        "products_without_price": len(no_price),
        "detail_errors": len(detail_errors),
        "promotional_products": promotional_products,
        "promotion_price_errors": promotion_price_errors,
        "blocked_detected": bool(meta.get("blocked_detected")),
        "manual_verification_required": bool(
            meta.get("manual_verification_required")
        ),
        "manual_verification_resolved": bool(
            meta.get("manual_verification_resolved")
        ),
        "pages_visited": len(meta.get("pages") or []),
        "error": error,
    }

    pages = [
        {"category_id": category.id, **item}
        for item in (meta.get("pages") or [])
    ]
    no_price_rows = [
        {"category_id": category.id, **item}
        for item in no_price
    ]
    error_rows = [
        {"category_id": category.id, **item}
        for item in detail_errors
    ]

    print("RESULTADO")
    for key in (
        "status",
        "target_products",
        "product_links",
        "discovery_complete",
        "products",
        "sku_complete",
        "price_complete",
        "regular_price_complete",
        "url_complete",
        "products_without_price",
        "detail_errors",
        "promotional_products",
        "promotion_price_errors",
        "blocked_detected",
        "manual_verification_required",
        "manual_verification_resolved",
        "pages_visited",
    ):
        print(f"{key:29}: {summary[key]}")
    if error:
        print(f"{'error':29}: {error}")

    return df, summary, pages, no_price_rows, error_rows



def _summary_is_acceptable(summary: dict) -> bool:
    products = int(summary.get("products") or 0)
    target = int(summary.get("target_products") or 0)
    if products <= 0 or target <= 0:
        return False

    product_links = int(summary.get("product_links") or 0)
    return bool(
        str(summary.get("status") or "").upper() == "SUCCESS"
        and bool(summary.get("discovery_complete"))
        and meets_rate(products, target)
        and (
            product_links <= 0
            or meets_rate(product_links, target)
        )
        and meets_rate(summary.get("sku_complete") or 0, products)
        and meets_rate(summary.get("price_complete") or 0, products)
        and meets_rate(
            summary.get("regular_price_complete") or 0,
            products,
        )
        and meets_rate(summary.get("url_complete") or 0, products)
        and valid_after_errors(
            products,
            summary.get("detail_errors") or 0,
        )
        and valid_after_errors(
            products,
            summary.get("promotion_price_errors") or 0,
        )
        and not bool(summary.get("blocked_detected"))
    )


def _read_existing_sheet(name: str) -> pd.DataFrame:
    try:
        return pd.read_excel(
            OUTPUT_PATH,
            sheet_name=name,
            dtype={"category_id": str, "sku": str, "store_id": str},
        )
    except Exception:
        return pd.DataFrame()


def _load_existing_valid_results(categories) -> dict[str, dict]:
    """Reuse successful Farmacias Similares categories captured today."""
    if not OUTPUT_PATH.exists():
        return {}

    concentrated = _read_existing_sheet("Concentrado")
    summary_df = _read_existing_sheet("Resumen")
    if concentrated.empty or summary_df.empty:
        return {}

    if "scrape_timestamp" not in concentrated.columns:
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

    optional = {
        name: _read_existing_sheet(name)
        for name in ("Paginas", "SinPrecio", "Errores")
    }
    configured_ids = {item.id for item in categories}
    results: dict[str, dict] = {}

    for _, row in summary_df.iterrows():
        category_id = str(row.get("category_id") or "").strip()
        if category_id not in configured_ids:
            continue

        summary = row.to_dict()
        if not _summary_is_acceptable(summary):
            continue

        frame = concentrated.loc[
            concentrated["category_id"].astype(str).eq(category_id)
        ].copy()
        if len(frame) != int(summary["products"]):
            continue

        payload = {"frame": frame, "summary": summary}
        for sheet_name, sheet in optional.items():
            payload[sheet_name] = (
                sheet.loc[
                    sheet["category_id"].astype(str).eq(category_id)
                ].to_dict("records")
                if "category_id" in sheet.columns
                else []
            )
        results[category_id] = payload

    return results


def _write_output(
    results: dict[str, dict],
    category_order: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    ordered = [
        results[category_id]
        for category_id in category_order
        if category_id in results
    ]

    frames = [
        item["frame"]
        for item in ordered
        if not item["frame"].empty
    ]
    summaries = [item["summary"] for item in ordered]
    pages = [
        row
        for item in ordered
        for row in item.get("Paginas", [])
    ]
    no_price_rows = [
        row
        for item in ordered
        for row in item.get("SinPrecio", [])
    ]
    error_rows = [
        row
        for item in ordered
        for row in item.get("Errores", [])
    ]

    concentrated = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=COLUMNS)
    )
    summary_df = pd.DataFrame(summaries)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as writer:
        concentrated.to_excel(
            writer,
            index=False,
            sheet_name="Concentrado",
        )
        summary_df.to_excel(
            writer,
            index=False,
            sheet_name="Resumen",
        )
        pd.DataFrame(pages).to_excel(
            writer,
            index=False,
            sheet_name="Paginas",
        )
        pd.DataFrame(no_price_rows).to_excel(
            writer,
            index=False,
            sheet_name="SinPrecio",
        )
        pd.DataFrame(error_rows).to_excel(
            writer,
            index=False,
            sheet_name="Errores",
        )

    return concentrated, summary_df


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Farmacias Similares - test categorías activas"
    )
    parser.add_argument(
        "--category",
        default="all",
        help="category_id o 'all'",
    )
    parser.add_argument(
        "--retry-failed-at-end",
        type=int,
        default=1,
        help=(
            "Reintentos al final sólo para categorías fallidas. "
            "Las categorías SUCCESS válidas no se descargan de nuevo."
        ),
    )
    args = parser.parse_args()

    if args.retry_failed_at_end < 0:
        print("ERROR: --retry-failed-at-end debe ser >= 0")
        return 12

    configured_categories = load_categories(
        ROOT / "config" / "farmacias-similares" / "categories.yaml"
    )
    categories = list(configured_categories)
    if args.category != "all":
        categories = [
            item
            for item in categories
            if item.id == args.category
        ]
        if not categories:
            raise SystemExit(
                f"Categoría no encontrada: {args.category}"
            )

    location = next(
        x
        for x in load_locations(ROOT / "config" / "locations.yaml")
        if x.id == "similares-online"
    )

    category_order = [item.id for item in categories]
    results: dict[str, dict] = {}

    if args.category == "all":
        results = _load_existing_valid_results(configured_categories)

    print("=" * 72)
    print("FASE 5 - FARMACIAS SIMILARES")
    print("=" * 72)
    print("Umbral     : >= 90%")
    print("Reintentos : sólo categorías fallidas")
    print("")
    print("La prueba:")
    print("  1. Detecta el total publicado por cada categoría.")
    print("  2. Recorre la paginación hasta cubrir el catálogo.")
    print("  3. Visita cada ficha de producto.")
    print("  4. Obtiene SKU desde 'Referencia'.")
    print("  5. Obtiene precio actual y regular.")
    print("  6. Valida consistencia de precios promocionales.")
    print("")

    if results:
        print("CATEGORÍAS SIMILARES YA VÁLIDAS HOY:")
        for category in configured_categories:
            if category.id in results:
                print(
                    f"- {category.id}: REUTILIZADA, "
                    "NO SE DESCARGA DE NUEVO"
                )
        print("")

    category_by_id = {
        item.id: item
        for item in categories
    }
    failed_ids: list[str] = []

    for category in categories:
        if category.id in results:
            continue

        (
            df,
            summary,
            category_pages,
            category_no_price,
            category_errors,
        ) = run_category(category, location)

        results[category.id] = {
            "frame": df,
            "summary": summary,
            "Paginas": category_pages,
            "SinPrecio": category_no_price,
            "Errores": category_errors,
        }
        _write_output(results, category_order)

        if not _summary_is_acceptable(summary):
            failed_ids.append(category.id)

    for retry_round in range(1, args.retry_failed_at_end + 1):
        if not failed_ids:
            break

        pending_ids = list(failed_ids)
        failed_ids = []

        print("")
        print("=" * 72)
        print(
            f"REINTENTO FINAL SIMILARES {retry_round}/"
            f"{args.retry_failed_at_end}"
        )
        print("=" * 72)
        print(
            "Sólo se reintentan: "
            + ", ".join(pending_ids)
        )

        for category_id in pending_ids:
            category = category_by_id[category_id]
            (
                df,
                summary,
                category_pages,
                category_no_price,
                category_errors,
            ) = run_category(category, location)

            results[category_id] = {
                "frame": df,
                "summary": summary,
                "Paginas": category_pages,
                "SinPrecio": category_no_price,
                "Errores": category_errors,
            }
            _write_output(results, category_order)

            if not _summary_is_acceptable(summary):
                failed_ids.append(category_id)

    concentrated, summary_df = _write_output(
        results,
        category_order,
    )

    print("")
    print("=" * 72)
    print("RESUMEN FARMACIAS SIMILARES")
    print("=" * 72)
    if not summary_df.empty:
        print(
            summary_df[
                [
                    "department",
                    "category_id",
                    "status",
                    "target_products",
                    "product_links",
                    "discovery_complete",
                    "products",
                    "sku_complete",
                    "price_complete",
                    "regular_price_complete",
                    "url_complete",
                    "detail_errors",
                    "promotion_price_errors",
                    "blocked_detected",
                ]
            ].to_string(index=False)
        )

    print("")
    print(f"Total filas: {len(concentrated)}")
    print(f"Archivo: {OUTPUT_PATH}")
    print("Diagnósticos: diagnostics\\farmacias_similares_*")

    acceptable = bool(
        len(summary_df) == len(categories)
        and not summary_df.empty
        and all(
            category.id in results
            and _summary_is_acceptable(
                results[category.id]["summary"]
            )
            for category in categories
        )
    )

    if acceptable:
        print(
            "Resultado Fase 5 / Similares: "
            "TODAS LAS CATEGORÍAS VÁLIDAS."
        )
    else:
        pending = [
            category.id
            for category in categories
            if category.id not in results
            or not _summary_is_acceptable(
                results[category.id]["summary"]
            )
        ]
        print(
            "Resultado Fase 5 / Similares: pendientes: "
            + ", ".join(pending)
        )

    return 0 if acceptable else 2


if __name__ == "__main__":
    raise SystemExit(main())
