from __future__ import annotations

import argparse
import sys
from copy import copy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS
from scraper.config import load_categories, load_locations
from scraper.retailers.benavides import (
    BenavidesBlocked,
    BenavidesNetworkUnavailable,
    BenavidesScraper,
)


OUTPUT_PATH = ROOT / "output" / "benavides_test.xlsx"


def _write_output(
    frames: list[pd.DataFrame],
    summaries: list[dict],
    pages: list[dict],
    detail_errors: list[dict],
    no_price_rows: list[dict],
    availability_errors: list[dict],
) -> None:
    concentrated = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=COLUMNS)
    )
    summary_df = pd.DataFrame(summaries)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as writer:
        concentrated.to_excel(writer, index=False, sheet_name="Concentrado")
        summary_df.to_excel(writer, index=False, sheet_name="Resumen")
        pd.DataFrame(pages).to_excel(writer, index=False, sheet_name="Paginas")
        pd.DataFrame(detail_errors).to_excel(
            writer, index=False, sheet_name="Errores"
        )
        pd.DataFrame(no_price_rows).to_excel(
            writer, index=False, sheet_name="SinPrecio"
        )
        pd.DataFrame(availability_errors).to_excel(
            writer, index=False, sheet_name="SinDisponibilidad"
        )

        workbook = writer.book
        for sheet_name in workbook.sheetnames:
            ws = workbook[sheet_name]
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
                    48,
                )
                ws.column_dimensions[cells[0].column_letter].width = width


def run_category(category, location, *, headed: bool, max_pages: int):
    print("")
    print("-" * 76)
    print(f"FARMACIAS BENAVIDES | {category.id}")
    print("-" * 76)
    print(f"Departamento : {category.department}")
    print(f"Categoría    : {category.name}")
    print(f"Subcategoría : {category.subcategory}")
    print(f"URL          : {category.url}")
    print("")

    scraper = BenavidesScraper(
        headless=not headed,
        browser_channel="chrome",
        max_pages=max_pages,
        wait_ms=450,
        detail_pause_ms=60,
    )

    rows = []
    status = "ERROR"
    error = None

    try:
        rows = scraper.scrape_category(category, location)
        status = str(scraper.run_meta.get("status") or "PARTIAL")
    except BenavidesBlocked as exc:
        status = "BLOCKED"
        error = str(exc)
    except BenavidesNetworkUnavailable as exc:
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
    products = len(df)
    sku_complete = int(
        df["sku"].fillna("").astype(str).str.strip().ne("").sum()
    ) if not df.empty else 0
    price_complete = int(
        pd.to_numeric(df["price_current"], errors="coerce").notna().sum()
    ) if not df.empty else 0
    regular_price_complete = int(
        pd.to_numeric(df["price_regular"], errors="coerce").notna().sum()
    ) if not df.empty else 0
    url_complete = int(
        df["url"].fillna("").astype(str).str.strip().ne("").sum()
    ) if not df.empty else 0
    availability_complete = int(
        pd.to_numeric(df["is_available"], errors="coerce").isin([0, 1]).sum()
    ) if not df.empty else 0
    available = int(
        pd.to_numeric(df["is_available"], errors="coerce").eq(1).sum()
    ) if not df.empty else 0
    unavailable = int(
        pd.to_numeric(df["is_available"], errors="coerce").eq(0).sum()
    ) if not df.empty else 0

    current = pd.to_numeric(
        df["price_current"], errors="coerce"
    ) if not df.empty else pd.Series(dtype=float)
    regular = pd.to_numeric(
        df["price_regular"], errors="coerce"
    ) if not df.empty else pd.Series(dtype=float)
    promotional_products = int((current < regular).sum()) if products else 0
    price_order_errors = int(
        ((regular.notna()) & (current.notna()) & (current > regular)).sum()
    ) if products else 0

    target = meta.get("target_products")
    links = meta.get("product_links")
    discovery_complete = bool(meta.get("discovery_complete"))
    detail_errors = meta.get("detail_errors") or []
    no_price = meta.get("products_without_price") or []
    unknown_availability = meta.get("unknown_availability") or []

    if status not in {"BLOCKED", "NETWORK_UNAVAILABLE", "ERROR"}:
        if (
            target is None
            or int(target) != products
            or int(target) != int(links or 0)
            or not discovery_complete
            or sku_complete != products
            or price_complete != products
            or url_complete != products
            or availability_complete != products
            or detail_errors
            or price_order_errors
        ):
            status = "PARTIAL"
        else:
            status = "SUCCESS"

    summary = {
        "retailer": "Farmacias Benavides",
        "department": category.department,
        "category": category.name,
        "subcategory": category.subcategory,
        "category_id": category.id,
        "status": status,
        "target_products": target,
        "product_links": links,
        "discovery_complete": discovery_complete,
        "products": products,
        "sku_complete": sku_complete,
        "price_complete": price_complete,
        "regular_price_complete": regular_price_complete,
        "url_complete": url_complete,
        "availability_complete": availability_complete,
        "available": available,
        "unavailable": unavailable,
        "promotional_products": promotional_products,
        "price_order_errors": price_order_errors,
        "detail_errors": len(detail_errors),
        "products_without_price": len(no_price),
        "unknown_availability": len(unknown_availability),
        "pages_visited": len(meta.get("pages") or []),
        "blocked_detected": bool(meta.get("blocked_detected")),
        "error": error,
    }

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
        "availability_complete",
        "available",
        "unavailable",
        "promotional_products",
        "price_order_errors",
        "detail_errors",
        "products_without_price",
        "unknown_availability",
        "pages_visited",
        "blocked_detected",
    ):
        print(f"{key:29}: {summary[key]}")
    if error:
        print(f"{'error':29}: {error}")

    pages = [
        {"category_id": category.id, **item}
        for item in (meta.get("pages") or [])
    ]
    errors = [
        {"category_id": category.id, **item}
        for item in detail_errors
    ]
    no_price_rows = [
        {"category_id": category.id, **item}
        for item in no_price
    ]
    availability_rows = [
        {"category_id": category.id, **item}
        for item in unknown_availability
    ]
    return (
        df,
        summary,
        pages,
        errors,
        no_price_rows,
        availability_rows,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test completo de Farmacias Benavides"
    )
    parser.add_argument("--category", default="all")
    parser.add_argument("--max-pages", type=int, default=60)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    categories = load_categories(
        ROOT / "config" / "benavides" / "categories.yaml"
    )
    if args.category != "all":
        categories = [x for x in categories if x.id == args.category]
    if not categories:
        raise SystemExit(f"Categoría no encontrada: {args.category}")

    location = next(
        x
        for x in load_locations(ROOT / "config" / "locations.yaml")
        if x.id == "benavides-online"
    )

    print("=" * 76)
    print("FARMACIAS BENAVIDES - TEST COMPLETO")
    print("=" * 76)
    print(f"Categorías : {len(categories)}")
    print(f"Navegador  : {'VISIBLE' if args.headed else 'HEADLESS'}")
    print(f"MaxPages   : {args.max_pages}")
    print(f"Salida     : {OUTPUT_PATH}")
    print("")
    print("Gates:")
    print("  1. target publicado == links descubiertos == productos extraídos")
    print("  2. SKU completo")
    print("  3. precio actual completo")
    print("  4. URL completa")
    print("  5. disponibilidad binaria completa (1 disponible / 0 no disponible)")
    print("  6. cero errores de detalle y cero price_order_errors")
    print("")
    print("Categorías:")
    for category in categories:
        print(f"  - {category.id}: {category.url}")

    frames: list[pd.DataFrame] = []
    summaries: list[dict] = []
    pages: list[dict] = []
    detail_errors: list[dict] = []
    no_price_rows: list[dict] = []
    availability_errors: list[dict] = []

    # For an isolated category rerun, preserve the other successful categories
    # already present in today's workbook and replace only the requested one.
    if args.category != "all" and OUTPUT_PATH.exists():
        try:
            existing_concentrated = pd.read_excel(
                OUTPUT_PATH,
                sheet_name="Concentrado",
                dtype={"sku": str, "store_id": str},
            )
            existing_summary = pd.read_excel(
                OUTPUT_PATH,
                sheet_name="Resumen",
                dtype={"category_id": str},
            )

            keep = existing_concentrated[
                existing_concentrated["category_id"]
                .astype(str)
                .ne(args.category)
            ].copy()
            if not keep.empty:
                frames.append(keep)

            keep_summary = existing_summary[
                existing_summary["category_id"]
                .astype(str)
                .ne(args.category)
            ].copy()
            if not keep_summary.empty:
                summaries.extend(keep_summary.to_dict("records"))

            for sheet_name, target in (
                ("Paginas", pages),
                ("Errores", detail_errors),
                ("SinPrecio", no_price_rows),
                ("SinDisponibilidad", availability_errors),
            ):
                try:
                    existing_sheet = pd.read_excel(
                        OUTPUT_PATH,
                        sheet_name=sheet_name,
                        dtype={"category_id": str},
                    )
                except Exception:
                    continue
                existing_sheet = existing_sheet[
                    existing_sheet["category_id"]
                    .astype(str)
                    .ne(args.category)
                ].copy()
                if not existing_sheet.empty:
                    target.extend(existing_sheet.to_dict("records"))
        except Exception as exc:
            print(
                "ADVERTENCIA: no fue posible preservar el workbook previo; "
                f"se continuará sólo con la categoría actual: {exc}",
                flush=True,
            )

    for category in categories:
        (
            df,
            summary,
            category_pages,
            category_errors,
            category_no_price,
            category_availability,
        ) = run_category(
            category,
            location,
            headed=args.headed,
            max_pages=args.max_pages,
        )
        frames.append(df)
        summaries.append(summary)
        pages.extend(category_pages)
        detail_errors.extend(category_errors)
        no_price_rows.extend(category_no_price)
        availability_errors.extend(category_availability)

        _write_output(
            frames,
            summaries,
            pages,
            detail_errors,
            no_price_rows,
            availability_errors,
        )
        print(f"Output actualizado: {OUTPUT_PATH}")

    summary_df = pd.DataFrame(summaries)
    total = sum(len(frame) for frame in frames)

    print("")
    print("=" * 76)
    print("RESUMEN FARMACIAS BENAVIDES")
    print("=" * 76)
    for item in summaries:
        print(
            f"{item['category_id']}: {item['status']} "
            f"({item['products']}/{item['target_products']}) "
            f"sku={item['sku_complete']}/{item['products']} "
            f"price={item['price_complete']}/{item['products']} "
            f"url={item['url_complete']}/{item['products']} "
            f"availability={item['availability_complete']}/{item['products']} "
            f"[1={item['available']} 0={item['unavailable']}]"
        )

    print("")
    print(f"Total filas: {total}")
    print(f"Archivo: {OUTPUT_PATH}")
    print("Diagnósticos: diagnostics\\benavides_*")

    acceptable = bool(summaries) and all(
        item["status"] == "SUCCESS"
        for item in summaries
    )
    return 0 if acceptable else 2


if __name__ == "__main__":
    raise SystemExit(main())
