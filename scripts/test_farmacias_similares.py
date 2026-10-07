from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS
from scraper.config import load_categories, load_locations
from scraper.io_utils import atomic_output_path
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
    available_products = 0
    unavailable_products = 0
    availability_unknown = 0
    if not df.empty:
        current = pd.to_numeric(df["price_current"], errors="coerce")
        regular = pd.to_numeric(df["price_regular"], errors="coerce")
        promotional_products = int((current < regular).sum())

        availability = (
            df["availability_status"]
            .fillna("UNKNOWN")
            .astype(str)
            .str.strip()
            .str.upper()
        )
        available_products = int(availability.eq("AVAILABLE").sum())
        unavailable_products = int(availability.eq("UNAVAILABLE").sum())
        availability_unknown = int(availability.eq("UNKNOWN").sum())

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
        "observed_products": meta.get("observed_products"),
        "unobserved_products": meta.get("unobserved_products"),
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
        "available_products": available_products,
        "unavailable_products": unavailable_products,
        "availability_unknown": availability_unknown,
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
        "observed_products",
        "unobserved_products",
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
        "available_products",
        "unavailable_products",
        "availability_unknown",
        "blocked_detected",
        "manual_verification_required",
        "manual_verification_resolved",
        "pages_visited",
    ):
        print(f"{key:29}: {summary[key]}")
    if error:
        print(f"{'error':29}: {error}")

    return df, summary, pages, no_price_rows, error_rows


def main() -> int:
    categories = load_categories(
        ROOT / "config" / "farmacias-similares" / "categories.yaml"
    )
    location = next(
        x
        for x in load_locations(ROOT / "config" / "locations.yaml")
        if x.id == "similares-online"
    )

    print("=" * 72)
    print("FARMACIAS SIMILARES - TEST CATEGORIAS ACTIVAS")
    print("=" * 72)
    print("La prueba:")
    print("  1. Detecta el total publicado por cada categoría.")
    print("  2. Recorre la paginación hasta cubrir el total.")
    print("  3. Visita cada ficha de producto.")
    print("  4. Obtiene SKU desde 'Referencia'.")
    print("  5. Obtiene precio actual y regular desde la ficha.")
    print("  6. Valida consistencia de precios promocionales.")
    print("")
    print("Categorías:")
    for category in categories:
        print(
            f"  - {category.id}: "
            f"{category.department} > {category.subcategory}"
        )

    frames = []
    summaries = []
    pages = []
    no_price_rows = []
    error_rows = []

    for category in categories:
        (
            df,
            summary,
            category_pages,
            category_no_price,
            category_errors,
        ) = run_category(category, location)

        frames.append(df)
        summaries.append(summary)
        pages.extend(category_pages)
        no_price_rows.extend(category_no_price)
        error_rows.extend(category_errors)

    concentrated = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=COLUMNS)
    )
    summary_df = pd.DataFrame(summaries)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output_path(OUTPUT_PATH) as temporary_output:
        with pd.ExcelWriter(temporary_output, engine="openpyxl") as writer:
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

    print("")
    print("=" * 72)
    print("RESUMEN FARMACIAS SIMILARES")
    print("=" * 72)
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
                "promotional_products",
                "promotion_price_errors",
                "blocked_detected",
            ]
        ].to_string(index=False)
    )

    print("")
    print(f"Total filas: {len(concentrated)}")
    print(f"Archivo: {OUTPUT_PATH}")
    print("Diagnósticos: diagnostics\\farmacias_similares_*")

    acceptable = True
    for summary, frame in zip(summaries, frames):
        if frame.empty:
            available_or_unknown = frame
        else:
            status = (
                frame["availability_status"]
                .fillna("UNKNOWN")
                .astype(str)
                .str.upper()
            )
            available_or_unknown = frame.loc[~status.eq("UNAVAILABLE")]

        priced_required = int(
            available_or_unknown["price_current"].notna().sum()
        ) if not available_or_unknown.empty else 0
        sku_required = int(
            available_or_unknown["sku"]
            .fillna("")
            .astype(str)
            .str.strip()
            .ne("")
            .sum()
        ) if not available_or_unknown.empty else 0
        url_required = int(
            available_or_unknown["url"]
            .fillna("")
            .astype(str)
            .str.strip()
            .ne("")
            .sum()
        ) if not available_or_unknown.empty else 0
        required_count = len(available_or_unknown)

        acceptable = acceptable and (
            summary["status"] == "SUCCESS"
            and summary["products"] > 0
            and bool(summary["discovery_complete"])
            and sku_required == required_count
            and priced_required == required_count
            and url_required == required_count
            and summary["products_without_price"] == 0
            and summary["detail_errors"] == 0
            and summary["promotion_price_errors"] == 0
        )

    return 0 if acceptable else 2


if __name__ == "__main__":
    raise SystemExit(main())
