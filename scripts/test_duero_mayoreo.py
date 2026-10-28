from __future__ import annotations

import argparse
import json
import re
import shutil
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
from scraper.io_utils import atomic_output_path
from duero_pagination_audit import audit_collection
from duero_packaging import classify_pack

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
    """Backward-compatible count helper; preserve evidence-sensitive parser."""
    return classify_pack(title, variant_title).units

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
        presentation = classify_pack(title, variant_title)
        units = presentation.units
        single = units == 1
        availability = variant.get("available")
        available = int(availability) if isinstance(availability, bool) else None
        result.append({
            "scrape_timestamp": stamp,
            "retailer": RETAILER,
            "city": None,
            "state": None,
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
            "package_type": presentation.package_type,
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


def collect_category(request, category, max_pages: int, delay_seconds: float = 0.7):
    """Verify *all* API pages with limit 50 and 250 before certifying them.

    A partial page is not end-of-pagination evidence. Both passes must reach
    an actual empty page and agree on product IDs, variant IDs and commercial
    fields; neither pass relies on HTML or an inferred storefront count.
    """
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    products, audit, page_log = audit_collection(
        request, category, max_pages=max_pages,
        delay_seconds=delay_seconds,
    )
    records = []
    seen = set()
    for product in products:
        for row in product_rows(product, category, stamp):
            key = (row["sku"], row["url"])
            if key in seen:
                audit["errors"].append(
                    "SKU y URL repetidos: " + str(key)
                )
                continue
            seen.add(key)
            records.append(row)

    df = pd.DataFrame(records, columns=COLUMNS)
    api_variants = int(audit["variant_count_api"])
    if len(df) != api_variants:
        audit["errors"].append(
            f"Variantes API={api_variants} vs exportadas={len(df)}: "
            "no se permiten variantes descartadas por precio o identificador"
        )
    verified = bool(audit["pagination_verified"]) and not audit["errors"]
    summary = {
        "retailer": RETAILER,
        "category_id": category.id,
        "category": category.name,
        "status": "SUCCESS" if verified and len(df) > 0 else "PARTIAL",
        "products": len(df),
        "catalogue_products_seen": audit["catalogue_products_seen"],
        "variants_seen_api": api_variants,
        "pages_visited": sum(
            int(not page["terminal_empty"])
            for page in page_log
            if page["limit"] == audit["first_pass_limit"]
        ),
        "pages_requested": len(page_log),
        "pagination_complete": verified,
        "pagination_verified": verified,
        "independent_match": bool(audit["independent_match"]),
        "terminal_page_limit50": audit["terminal_page_limit50"],
        "terminal_page_limit250": audit["terminal_page_limit250"],
        "catalogue_count_source": audit["catalogue_count_source"],
        "sku_complete": int(df["sku"].fillna("").astype(str).str.strip().ne("").sum()) if len(df) else 0,
        "merchant_sku_complete": int((df["sku"].fillna("").astype(str).str.strip().ne("") & ~df["sku"].fillna("").astype(str).str.startswith("SHOPIFY-VARIANT-")).sum()) if len(df) else 0,
        "price_complete": int(df["price_current"].notna().sum()) if len(df) else 0,
        "url_complete": int(df["url"].fillna("").astype(str).str.strip().ne("").sum()) if len(df) else 0,
        "pack_units_complete": int(df["units_per_package"].notna().sum()) if len(df) else 0,
        "unit_price_complete": int(df["price_per_unit"].notna().sum()) if len(df) else 0,
        "availability_complete": int(df["is_available"].notna().sum()) if len(df) else 0,
        "store_context_verified": False,
        "blocked_detected": any(
            "HTTP 403" in error or "HTTP 429" in error
            for error in audit["errors"]
        ),
        "errors": " | ".join(audit["errors"]),
    }
    return df, summary, page_log, audit

def main():
    parser = argparse.ArgumentParser(description="Prueba aislada Mayorista Duero, Fase 5")
    parser.add_argument("--category", default="all")
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument(
        "--delay-seconds", type=float, default=0.7,
        help="Pausa entre solicitudes API; default 0.7 segundos.",
    )
    args = parser.parse_args()
    if args.max_pages < 2:
        parser.error("--max-pages debe ser >= 2 para verificar página terminal")
    if args.delay_seconds < 0:
        parser.error("--delay-seconds debe ser >= 0")

    categories = load_categories(ROOT / "config" / "duero-mayoreo" / "categories.yaml")
    if args.category != "all":
        categories = [c for c in categories if c.id == args.category]
        if not categories:
            parser.error("Categoria no configurada")

    frames, summaries, page_log, audit_detail = [], [], [], []
    with sync_playwright() as playwright:
        # APIRequest.new_context does not accept browser-only locale.
        # Request language is conveyed through ordinary HTTP headers.
        request = playwright.request.new_context(
            user_agent="Mozilla/5.0 (compatible; ProductCatalogAudit/1.0)",
            extra_http_headers={
                "Accept": "application/json",
                "Accept-Language": "es-MX,es;q=0.9",
            },
        )
        try:
            for category in categories:
                df, summary, category_pages, audit = collect_category(
                    request, category, args.max_pages, args.delay_seconds
                )
                frames.append(df)
                summaries.append(summary)
                page_log.extend(category_pages)
                audit_detail.append(audit)
        finally:
            request.dispose()

    # Preserve the canonical schema without pandas concat's all-NA dtype warning.
    consolidated = pd.DataFrame(
        [row for frame in frames for row in frame.to_dict("records")],
        columns=COLUMNS,
    )
    report = pd.DataFrame(summaries)
    pages = pd.DataFrame(page_log)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    # Never lose yesterday's or an earlier partial Duero capture on re-audit.
    if OUTPUT.exists():
        checkpoint_dir = ROOT / "output" / "checkpoints_validos"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        archive = checkpoint_dir / (
            "before_duero_audit_"
            + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".xlsx"
        )
        shutil.copy2(OUTPUT, archive)
        print(f"Captura Duero anterior respaldada: {archive}")

    with atomic_output_path(OUTPUT) as temporary:
        with pd.ExcelWriter(temporary, engine="openpyxl") as writer:
            consolidated.to_excel(writer, sheet_name="Concentrado", index=False)
            report.to_excel(writer, sheet_name="Resumen", index=False)
            pages.to_excel(writer, sheet_name="AuditoriaPaginacion", index=False)

    report_dir = ROOT / "output" / "reportes_integracion"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    base = report_dir / ("duero_paginacion_" + stamp)
    pages.to_csv(base.with_suffix(".csv"), encoding="utf-8-sig", index=False)
    payload = {
        "retailer": RETAILER,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "categories": audit_detail,
        "all_api_paginations_verified": bool(
            len(report) == len(categories)
            and report["pagination_verified"].eq(True).all()
            and report["independent_match"].eq(True).all()
        ),
        "store_context_verified": False,
        "storefront_catalogue_count_verified": False,
    }
    # Keep machine-readable audit concise; request-by-request evidence is CSV/XLSX.
    base.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print("")
    print("=" * 80)
    print("DUERO - AUDITORIA EXHAUSTIVA DE PAGINACION")
    print("=" * 80)
    print(report[
        ["category_id", "status", "catalogue_products_seen",
         "products", "pages_visited", "pages_requested",
         "terminal_page_limit50", "terminal_page_limit250",
         "pagination_verified", "independent_match", "errors"]
    ].to_string(index=False))
    print(f"Variantes exportadas: {len(consolidated)}")
    print(f"Salida: {OUTPUT}")
    print(f"Detalle por página: {base.with_suffix('.csv')}")
    print(f"Diagnóstico: {base.with_suffix('.json')}")
    print(
        "ALCANCE: paginación de API Shopify contrastada con límites 50 y 250. "
        "NO se ha comparado contra el total visual del storefront."
    )
    print(
        "PRECIOS PUBLICOS: las capturas SUCCESS pueden integrarse sin "
        "comprobacion de ciudad, sucursal o codigo postal. "
        "No se certifican precios especificos por tienda."
    )
    return 0 if len(report) == len(categories) and report["status"].eq("SUCCESS").all() else 2

if __name__ == "__main__":
    raise SystemExit(main())
