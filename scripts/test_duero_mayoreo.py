from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import COLUMNS
from scraper.config import load_categories

OUTPUT = ROOT / "output" / "duero_mayoreo_phase5_test.xlsx"
RETAILER = "Mayorista Duero"
PAGE_SIZE = 250
HOST = "dueromayoreo.mayoreoenlinea.mx"


def money(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        # Shopify collections/products.json exposes prices as decimal MXN strings.
        # Do not reinterpret integer-valued prices as cents without evidence.
        result = Decimal(str(value).replace(",", "").strip())
        return float(result) if result >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def pack_units(title: str, variant_title: str):
    text = f"{title} {variant_title}"
    patterns = (
        r"(?i)\\b(?:caja|paquete|pack|display)\\s*(?:de|con)?\\s*(\\d{1,4})\\s*(?:pzas?|piezas?|unidades?|uds?|art[ií]culos?)\\b",
        r"(?i)\\b(\\d{1,4})\\s*(?:pzas?|piezas?|unidades?|uds?|art[ií]culos?)\\s*(?:por|x|/)\\s*(?:caja|paquete|pack)\\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            count = int(match.group(1))
            if 1 <= count <= 1000:
                return count
    return None


def product_rows(product: dict, category, stamp: str) -> list[dict]:
    title = str(product.get("title") or "").strip()
    handle = str(product.get("handle") or "").strip()
    vendor = str(product.get("vendor") or "").strip()
    if not title or not handle:
        return []
    product_url = f"https://{HOST}/products/{handle}"
    result = []
    variants = product.get("variants") or []
    for variant in variants:
        variant_title = str(variant.get("title") or "").strip()
        price = money(variant.get("price"))
        if price is None or price <= 0:
            continue
        regular = money(variant.get("compare_at_price"))
        if regular is not None and regular < price:
            regular = None
        sku = str(variant.get("sku") or "").strip()
        if not sku:
            # Explicitly distinguish synthetic identifiers from a merchant SKU.
            sku = f"SHOPIFY-VARIANT-{variant.get('id')}" if variant.get("id") else ""
        units = pack_units(title, variant_title)
        single = units == 1
        availability = variant.get("available")
        available = int(availability) if isinstance(availability, bool) else None
        result.append({
            "scrape_timestamp": stamp,
            "retailer": RETAILER,
            "city": "Zamora",
            "state": "Michoacán",
            "postal_code": None,
            "store": "Duero Mayoreo - catálogo online",
            "store_id": None,
            "department": category.department,
            "category": category.name,
            "subcategory": category.subcategory,
            "sub_subcategory": category.sub_subcategory,
            "category_id": category.id,
            "sku": sku,
            "brand": vendor or None,
            "product": title if variant_title.casefold() in ("", "default title") else f"{title} - {variant_title}",
            "price_current": price,
            "price_regular": regular,
            "promotion": bool(regular is not None and regular > price),
            "package_type": "CAJA" if re.search(r"(?i)\\bcaja\\b", f"{title} {variant_title}") else None,
            "units_per_package": units,
            "is_single_item": single if units is not None else None,
            "price_per_unit": round(price / units, 4) if units else None,
            "single_item_presentation": variant_title if single else None,
            "single_item_price": price if single else None,
            "availability_status": "AVAILABLE" if available == 1 else "UNAVAILABLE" if available == 0 else "UNKNOWN",
            "is_available": available,
            "availability_raw": "shopify_variant_available; postal_code_not_verified" if available is not None else None,
            "pickup_available": None,
            "store_context_verified": False,
            "store_context_method": "public_collection_api_no_postal_code",
            "url": product_url + (f"?variant={variant.get('id')}" if variant.get("id") else ""),
            "price_raw": str(variant.get("price")),
        })
    return result


def collect_category(request, category, max_pages: int):
    slug = urlsplit(category.url).path.rstrip("/").split("/")[-1]
    base = f"https://{HOST}/collections/{slug}/products.json"
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    records = []
    seen = set()
    errors = []
    complete = False
    pages = 0
    products_seen = 0

    for page in range(1, max_pages + 1):
        url = f"{base}?limit={PAGE_SIZE}&page={page}"
        try:
            response = request.get(url, timeout=60000)
            if response.status != 200:
                errors.append(f"page={page}: HTTP {response.status}")
                break
            data = response.json()
            products = data.get("products")
            if not isinstance(products, list):
                errors.append(f"page={page}: no products array")
                break
        except Exception as exc:
            errors.append(f"page={page}: {type(exc).__name__}: {exc}")
            break

        pages += 1
        products_seen += len(products)
        for product in products:
            for row in product_rows(product, category, stamp):
                key = (row["sku"], row["url"])
                if key not in seen:
                    seen.add(key)
                    records.append(row)
        print(f"{category.id}: page={page}, products={len(products)}, variants={len(records)}", flush=True)
        if len(products) < PAGE_SIZE:
            complete = True
            break

    df = pd.DataFrame(records, columns=COLUMNS)
    summary = {
        "retailer": RETAILER,
        "category_id": category.id,
        "category": category.name,
        "status": "SUCCESS" if complete and not errors and len(df) > 0 else "PARTIAL",
        "products": len(df),
        "catalogue_products_seen": products_seen,
        "pages_visited": pages,
        "pagination_complete": complete,
        "sku_complete": int(df["sku"].fillna("").astype(str).str.strip().ne("").sum()) if len(df) else 0,
        "merchant_sku_complete": int((~df["sku"].fillna("").astype(str).str.startswith("SHOPIFY-VARIANT-")).sum()) if len(df) else 0,
        "price_complete": int(df["price_current"].notna().sum()) if len(df) else 0,
        "url_complete": int(df["url"].fillna("").astype(str).str.strip().ne("").sum()) if len(df) else 0,
        "pack_units_complete": int(df["units_per_package"].notna().sum()) if len(df) else 0,
        "unit_price_complete": int(df["price_per_unit"].notna().sum()) if len(df) else 0,
        "availability_complete": int(df["is_available"].notna().sum()) if len(df) else 0,
        "store_context_verified": False,
        "blocked_detected": any("HTTP 403" in e or "HTTP 429" in e for e in errors),
        "errors": " | ".join(errors),
    }
    return df, summary


def main():
    parser = argparse.ArgumentParser(description="Prueba aislada Mayorista Duero, Fase 5")
    parser.add_argument("--category", default="all")
    parser.add_argument("--max-pages", type=int, default=30)
    args = parser.parse_args()
    if args.max_pages < 1:
        parser.error("--max-pages debe ser >= 1")

    categories = load_categories(ROOT / "config" / "duero-mayoreo" / "categories.yaml")
    if args.category != "all":
        categories = [c for c in categories if c.id == args.category]
        if not categories:
            parser.error("Categoria no configurada")

    frames, summaries = [], []
    with sync_playwright() as playwright:
        request = playwright.request.new_context(
            user_agent="Mozilla/5.0 (compatible; ProductCatalogAudit/1.0)",
            locale="es-MX",
            extra_http_headers={"Accept": "application/json"},
        )
        try:
            for category in categories:
                df, summary = collect_category(request, category, args.max_pages)
                frames.append(df)
                summaries.append(summary)
        finally:
            request.dispose()

    consolidated = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    report = pd.DataFrame(summaries)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        consolidated.to_excel(writer, sheet_name="Concentrado", index=False)
        report.to_excel(writer, sheet_name="Resumen", index=False)
    print(report.to_string(index=False))
    print(f"Salida: {OUTPUT}")
    print("INTEGRACION OMITIDA: no se ha validado el contexto de tienda ni empaque.")
    return 0 if len(report) == len(categories) and report["status"].eq("SUCCESS").all() else 2


if __name__ == "__main__":
    raise SystemExit(main())
