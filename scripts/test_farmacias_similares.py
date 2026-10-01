from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS
from scraper.config import load_categories, load_locations
from scraper.retailers.farmacias_similares import (
    FarmaciasSimilaresBlocked,
    FarmaciasSimilaresNetworkUnavailable,
    FarmaciasSimilaresScraper,
)


OUTPUT_PATH = ROOT / "output" / "farmacias_similares_aparato_respiratorio_test.xlsx"


def main() -> int:
    category = next(
        x
        for x in load_categories(
            ROOT / "config" / "farmacias-similares" / "categories.yaml"
        )
        if x.id == "aparato-respiratorio"
    )
    location = next(
        x
        for x in load_locations(ROOT / "config" / "locations.yaml")
        if x.id == "similares-online"
    )

    print("=" * 72)
    print("FARMACIAS SIMILARES - TEST APARATO RESPIRATORIO")
    print("=" * 72)
    print(f"Categoría    : {category.name}")
    print(f"Subcategoría : {category.subcategory}")
    print(f"URL          : {category.url}")
    print(f"Salida       : {OUTPUT_PATH}")
    print("")
    print("La prueba:")
    print("  1. Detecta el total publicado por la categoría.")
    print("  2. Recorre la paginación hasta cubrir el total.")
    print("  3. Visita cada ficha de producto.")
    print("  4. Obtiene SKU desde 'Referencia'.")
    print("  5. Obtiene precio actual y regular desde la ficha.")
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

    summary = pd.DataFrame(
        [
            {
                "retailer": "Farmacias Similares",
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
        ]
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Concentrado")
        summary.to_excel(writer, index=False, sheet_name="Resumen")
        pd.DataFrame(meta.get("pages") or []).to_excel(
            writer,
            index=False,
            sheet_name="Paginas",
        )
        pd.DataFrame(no_price).to_excel(
            writer,
            index=False,
            sheet_name="SinPrecio",
        )
        pd.DataFrame(detail_errors).to_excel(
            writer,
            index=False,
            sheet_name="Errores",
        )

    print("")
    print("=" * 72)
    print("RESULTADO FARMACIAS SIMILARES")
    print("=" * 72)
    row = summary.iloc[0]
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
        print(f"{key:29}: {row[key]}")
    if error:
        print(f"{'error':29}: {error}")

    print("")
    print(f"Archivo: {OUTPUT_PATH}")
    print("Diagnósticos: diagnostics\\farmacias_similares_*")

    acceptable = (
        status == "SUCCESS"
        and len(df) > 0
        and bool(row["discovery_complete"])
        and int(row["sku_complete"]) == len(df)
        and int(row["price_complete"]) == len(df)
        and int(row["regular_price_complete"]) == len(df)
        and int(row["url_complete"]) == len(df)
        and int(row["products_without_price"]) == 0
        and int(row["detail_errors"]) == 0
        and int(row["promotion_price_errors"]) == 0
    )

    return 0 if acceptable else 2


if __name__ == "__main__":
    raise SystemExit(main())
