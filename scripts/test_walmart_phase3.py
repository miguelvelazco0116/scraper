from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from playwright.sync_api import sync_playwright

from main import COLUMNS, deduplicate_catalog
from scraper.config import load_categories, load_locations
from scraper.retailers.walmart import (
    WalmartBlocked,
    WalmartScraper,
    WalmartStoreContextError,
)

OUTPUT_PATH = ROOT / "output" / "walmart_phase3_test.xlsx"


def _count_nonempty(series: pd.Series) -> int:
    return int(series.fillna("").astype(str).str.strip().ne("").sum())


def find_walmart_page(browser):
    candidates = []
    for context in browser.contexts:
        for page in context.pages:
            if "walmart.com.mx" in (page.url or "").casefold():
                candidates.append(page)
    return candidates[-1] if candidates else None


def run_category(
    category,
    location,
    *,
    page,
    max_pages: int,
):
    print("")
    print("-" * 78)
    print(f"WALMART | {category.id}")
    print("-" * 78)
    print(f"Tienda      : {location.store} ({location.store_id})")
    print(f"CP          : {location.postal_code}")
    print(f"URL         : {category.url}")

    scraper = WalmartScraper(
        headless=False,
        max_pages=max_pages,
        wait_ms=2500,
        manual_verification_timeout_ms=180_000,
    )
    session_method = "existing_chrome_cdp"

    rows = []
    error = None
    source_status = "SUCCESS"

    try:
        rows = scraper.scrape_category_on_page(
            page,
            category,
            location,
        )
    except WalmartBlocked as exc:
        source_status = "BLOCKED"
        error = str(exc)
    except WalmartStoreContextError as exc:
        source_status = "STORE_CONTEXT_ERROR"
        error = str(exc)
    except Exception as exc:
        source_status = "ERROR"
        error = f"{type(exc).__name__}: {exc}"

    frame = pd.DataFrame(rows, columns=COLUMNS)
    if not frame.empty:
        frame = deduplicate_catalog(frame)
        frame = frame.sort_values(
            ["brand", "product"],
            na_position="last",
        ).reset_index(drop=True)

    products = len(frame)
    sku_complete = _count_nonempty(frame["sku"]) if products else 0
    price_complete = (
        int(pd.to_numeric(frame["price_current"], errors="coerce").notna().sum())
        if products
        else 0
    )
    url_complete = _count_nonempty(frame["url"]) if products else 0
    store_context_complete = (
        int(
            frame["store_context_verified"]
            .map(lambda value: False if pd.isna(value) else bool(value))
            .sum()
        )
        if products
        else 0
    )
    current = (
        pd.to_numeric(frame["price_current"], errors="coerce")
        if products
        else pd.Series(dtype=float)
    )
    regular = (
        pd.to_numeric(frame["price_regular"], errors="coerce")
        if products
        else pd.Series(dtype=float)
    )
    price_order_errors = (
        int((current.notna() & regular.notna() & regular.lt(current)).sum())
        if products
        else 0
    )
    duplicate_rows = (
        int(frame.duplicated(subset=["sku", "url"]).sum())
        if products
        else 0
    )

    meta = scraper.run_meta or {}
    pages_visited = len(meta.get("pages") or [])
    sources = meta.get("sources") or []
    sources_discovered = int(meta.get("sources_discovered") or 0)
    sources_visited = len(sources)
    pagination_methods = " | ".join(
        dict.fromkeys(
            str(item.get("pagination_method") or "")
            for item in sources
            if item.get("pagination_method")
        )
    )
    sponsored_banner_clicks = sum(
        int(item.get("sponsored_banner_clicks") or 0)
        for item in sources
    )
    store_context_meta = bool(meta.get("store_context_verified"))
    target_products = meta.get("target_products")
    coverage = meta.get("coverage")
    coverage_ok = bool(
        coverage is not None and float(coverage) >= 0.90
    )
    pagination_verified = bool(meta.get("pagination_verified"))
    catalogue_gate_ok = bool(
        coverage_ok
        or (
            target_products in (None, 0)
            and pagination_verified
        )
    )

    notes = []
    if source_status != "SUCCESS":
        notes.append(f"source_status={source_status}")
    if products <= 0:
        notes.append("sin productos")
    if sku_complete != products:
        notes.append(f"sku {sku_complete}/{products}")
    if price_complete != products:
        notes.append(f"precio {price_complete}/{products}")
    if url_complete != products:
        notes.append(f"url {url_complete}/{products}")
    if store_context_complete != products or not store_context_meta:
        notes.append(
            f"store_context {store_context_complete}/{products}"
        )
    if price_order_errors:
        notes.append(f"price_order_errors={price_order_errors}")
    if duplicate_rows:
        notes.append(f"duplicados={duplicate_rows}")
    if sponsored_banner_clicks:
        notes.append(
            f"sponsored_banner_clicks={sponsored_banner_clicks}"
        )
    if pages_visited <= 0:
        notes.append("sin páginas visitadas")
    if sources_discovered <= 0 or sources_visited != sources_discovered:
        notes.append(
            f"fuentes {sources_visited}/{sources_discovered}"
        )
    if not catalogue_gate_ok:
        if target_products not in (None, 0):
            notes.append(
                f"cobertura {coverage if coverage is not None else 0:.1%} < 90%"
            )
        else:
            notes.append("paginación no verificada")

    status = "COMPLETE" if not notes else "PARTIAL"

    summary = {
        "retailer": "Walmart",
        "department": category.department,
        "category": category.name,
        "subcategory": category.subcategory,
        "category_id": category.id,
        "status": status,
        "source_status": source_status,
        "target_products": target_products,
        "products": products,
        "coverage": coverage,
        "coverage_threshold": 0.90,
        "coverage_ok": coverage_ok,
        "catalogue_gate_ok": catalogue_gate_ok,
        "sku_complete": sku_complete,
        "price_complete": price_complete,
        "url_complete": url_complete,
        "store_context_complete": store_context_complete,
        "store_context_verified": store_context_meta,
        "store_context_method": meta.get("store_context_method"),
        "sources_discovered": sources_discovered,
        "sources_visited": sources_visited,
        "pages_visited": pages_visited,
        "pagination_methods": pagination_methods,
        "sponsored_banner_clicks": sponsored_banner_clicks,
        "pagination_verified": pagination_verified,
        "pagination_stop_reason": meta.get("pagination_stop_reason"),
        "total_pages_hint": meta.get("total_pages_hint"),
        "price_order_errors": price_order_errors,
        "duplicates_sku_url": duplicate_rows,
        "manual_verification_required": bool(
            meta.get("manual_verification_required")
        ),
        "manual_verification_resolved": bool(
            meta.get("manual_verification_resolved")
        ),
        "session_method": session_method,
        "quality_notes": "; ".join(notes),
        "error": error,
    }

    print("RESULTADO")
    for key in (
        "status",
        "source_status",
        "target_products",
        "products",
        "coverage",
        "coverage_threshold",
        "coverage_ok",
        "catalogue_gate_ok",
        "sku_complete",
        "price_complete",
        "url_complete",
        "store_context_complete",
        "store_context_verified",
        "store_context_method",
        "sources_discovered",
        "sources_visited",
        "pages_visited",
        "pagination_methods",
        "sponsored_banner_clicks",
        "pagination_verified",
        "pagination_stop_reason",
        "price_order_errors",
        "duplicates_sku_url",
    ):
        print(f"{key:28}: {summary[key]}")
    if error:
        print(f"{'error':28}: {error}")

    return frame, summary, meta


def _summary_is_acceptable(summary: dict) -> bool:
    return bool(
        summary["status"] == "COMPLETE"
        and summary["source_status"] == "SUCCESS"
        and summary["products"] > 0
        and summary["sku_complete"] == summary["products"]
        and summary["price_complete"] == summary["products"]
        and summary["url_complete"] == summary["products"]
        and summary["store_context_complete"] == summary["products"]
        and bool(summary["store_context_verified"])
        and summary["sources_discovered"] == summary["sources_visited"]
        and summary["catalogue_gate_ok"]
        and summary["price_order_errors"] == 0
        and summary["duplicates_sku_url"] == 0
        and summary["sponsored_banner_clicks"] == 0
    )


def _write_results(
    results: dict[str, tuple],
    category_order: list[str],
    location,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    ordered = [
        results[category_id]
        for category_id in category_order
        if category_id in results
    ]

    frames = [item[0] for item in ordered if not item[0].empty]
    summaries = [item[1] for item in ordered]
    metas = [
        {
            "category_id": category_id,
            "meta_json": json.dumps(
                results[category_id][2],
                ensure_ascii=False,
                default=str,
            ),
        }
        for category_id in category_order
        if category_id in results
    ]

    concentrated = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=COLUMNS)
    )
    summary_df = pd.DataFrame(summaries)

    all_complete = bool(
        summaries
        and all(_summary_is_acceptable(item) for item in summaries)
    )

    capture_dates = set()
    if not concentrated.empty and "scrape_timestamp" in concentrated.columns:
        capture_dates = set(
            concentrated["scrape_timestamp"]
            .dropna()
            .astype(str)
            .str.slice(0, 10)
        )
    capture_date = (
        next(iter(capture_dates))
        if len(capture_dates) == 1
        else datetime.now().astimezone().date().isoformat()
    )

    control = pd.DataFrame(
        [
            {
                "status": "PRODUCTIVE" if all_complete else "PARTIAL",
                "capture_date": capture_date,
                "products": len(concentrated),
                "categories": len(summary_df),
                "store": location.store,
                "store_id": location.store_id,
                "postal_code": location.postal_code,
                "all_categories_complete": all_complete,
            }
        ]
    )

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
        control.to_excel(
            writer,
            index=False,
            sheet_name="Control",
        )
        pd.DataFrame(metas).to_excel(
            writer,
            index=False,
            sheet_name="Meta",
        )

    return concentrated, summary_df


def _load_existing_valid_results(
    categories,
) -> dict[str, tuple]:
    """Reuse only valid Walmart categories already captured today."""
    if not OUTPUT_PATH.exists():
        return {}

    try:
        concentrated = pd.read_excel(
            OUTPUT_PATH,
            sheet_name="Concentrado",
            dtype={"sku": str, "store_id": str, "category_id": str},
        )
        summary_df = pd.read_excel(
            OUTPUT_PATH,
            sheet_name="Resumen",
            dtype={"category_id": str},
        )
        meta_df = pd.read_excel(
            OUTPUT_PATH,
            sheet_name="Meta",
            dtype={"category_id": str},
        )
    except Exception:
        return {}

    if concentrated.empty or summary_df.empty:
        return {}

    row_dates = set(
        concentrated["scrape_timestamp"]
        .dropna()
        .astype(str)
        .str.slice(0, 10)
    )
    today = datetime.now().astimezone().date().isoformat()
    if row_dates != {today}:
        return {}

    configured_ids = {item.id for item in categories}
    meta_by_id: dict[str, dict] = {}
    if not meta_df.empty and {
        "category_id",
        "meta_json",
    }.issubset(meta_df.columns):
        for _, row in meta_df.iterrows():
            category_id = str(row.get("category_id") or "").strip()
            if not category_id:
                continue
            try:
                meta_by_id[category_id] = json.loads(
                    str(row.get("meta_json") or "{}")
                )
            except Exception:
                meta_by_id[category_id] = {}

    results: dict[str, tuple] = {}
    for _, summary_row in summary_df.iterrows():
        category_id = str(
            summary_row.get("category_id") or ""
        ).strip()
        if category_id not in configured_ids:
            continue

        summary = summary_row.to_dict()
        if not _summary_is_acceptable(summary):
            continue

        frame = concentrated.loc[
            concentrated["category_id"]
            .astype(str)
            .eq(category_id)
        ].copy()

        if len(frame) != int(summary["products"]):
            continue

        results[category_id] = (
            frame,
            summary,
            meta_by_id.get(category_id, {}),
        )

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Walmart Fase 3 usando Chrome abierto manualmente."
        )
    )
    parser.add_argument("--cdp-url", required=True)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument(
        "--category",
        help="Ejecuta solo un category_id de Walmart.",
    )
    parser.add_argument(
        "--retry-failed-at-end",
        type=int,
        default=1,
        help=(
            "Reintentos al final exclusivamente para categorías fallidas. "
            "Las categorías COMPLETE no se vuelven a descargar."
        ),
    )
    args = parser.parse_args()

    if args.retry_failed_at_end < 0:
        print("ERROR: --retry-failed-at-end debe ser >= 0")
        return 12

    categories = list(
        load_categories(ROOT / "config" / "walmart" / "categories.yaml")
    )
    if args.category:
        categories = [
            item
            for item in categories
            if item.id == args.category
        ]
        if not categories:
            print(
                f"ERROR: category_id Walmart no configurado: {args.category}"
            )
            return 12

    location = next(
        x
        for x in load_locations(ROOT / "config" / "locations.yaml")
        if x.id == "sc-toreo"
    )

    print("=" * 78)
    print("WALMART - FASE 3 - PRUEBA COMPLETA")
    print("=" * 78)
    print(f"Categorías : {len(categories)}")
    print(f"Tienda     : {location.store} ({location.store_id})")
    print(f"CP         : {location.postal_code}")
    print("Sesión     : Chrome existente por CDP")
    print(f"CDP        : {args.cdp_url}")
    print("El script NO abrirá ni cerrará Chrome.")
    print("")

    category_order = [category.id for category in categories]
    results: dict[str, tuple] = (
        _load_existing_valid_results(categories)
        if not args.category
        else {}
    )

    if results:
        print("")
        print("CATEGORÍAS WALMART YA VÁLIDAS HOY:")
        for category_id in category_order:
            if category_id in results:
                print(f"- {category_id}: REUTILIZADA, NO SE DESCARGA DE NUEVO")
        _write_results(results, category_order, location)

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(
                args.cdp_url
            )
        except Exception as exc:
            print("ERROR: no fue posible conectarse al Chrome abierto.")
            print(f"{type(exc).__name__}: {exc}")
            return 10

        page = find_walmart_page(browser)
        if page is None:
            print(
                "ERROR: abre una pestaña de Walmart "
                "antes de ejecutar el test."
            )
            return 11

        print(f"Pestaña Walmart detectada: {page.url}")
        print("")
        print("PRIMERA PASADA:")
        print("- Cada categoría se descarga una sola vez.")
        print("- Las categorías COMPLETE quedan preservadas.")
        print("- Los reintentos se hacen sólo al final y sólo sobre fallas.")
        print("")

        failed_ids: list[str] = []
        category_by_id = {item.id: item for item in categories}

        for category in categories:
            if category.id in results:
                print("")
                print(
                    f"WALMART | {category.id}: "
                    "YA VÁLIDA HOY, SE OMITE DESCARGA."
                )
                continue

            result = run_category(
                category,
                location,
                page=page,
                max_pages=args.max_pages,
            )
            results[category.id] = result
            _write_results(results, category_order, location)

            if not _summary_is_acceptable(result[1]):
                failed_ids.append(category.id)

            print(
                "Checkpoint Walmart actualizado. "
                "Pausa entre categorías: 4 segundos.",
                flush=True,
            )
            page.wait_for_timeout(4_000)

        for retry_round in range(1, args.retry_failed_at_end + 1):
            if not failed_ids:
                break

            pending_ids = list(failed_ids)
            failed_ids = []

            print("")
            print("=" * 78)
            print(
                f"REINTENTO FINAL WALMART {retry_round}/"
                f"{args.retry_failed_at_end}"
            )
            print("=" * 78)
            print(
                "Sólo se reintentan: "
                + ", ".join(pending_ids)
            )

            for category_id in pending_ids:
                category = category_by_id[category_id]
                result = run_category(
                    category,
                    location,
                    page=page,
                    max_pages=args.max_pages,
                )
                results[category_id] = result
                _write_results(results, category_order, location)

                if not _summary_is_acceptable(result[1]):
                    failed_ids.append(category_id)

                print(
                    "Checkpoint Walmart actualizado. "
                    "Pausa entre reintentos: 4 segundos.",
                    flush=True,
                )
                page.wait_for_timeout(4_000)

        # No browser.close(): la sesión pertenece al usuario.

    concentrated, summary_df = _write_results(
        results,
        category_order,
        location,
    )

    print("")
    print("=" * 78)
    print("RESUMEN WALMART FASE 3")
    print("=" * 78)
    print(
        summary_df[
            [
                "category_id",
                "status",
                "target_products",
                "products",
                "coverage",
                "coverage_threshold",
                "sku_complete",
                "price_complete",
                "url_complete",
                "store_context_complete",
                "store_context_verified",
                "sources_discovered",
                "sources_visited",
                "pages_visited",
                "pagination_methods",
                "sponsored_banner_clicks",
                "pagination_verified",
                "price_order_errors",
                "duplicates_sku_url",
                "quality_notes",
            ]
        ].to_string(index=False)
    )
    print("")
    print(f"Total filas: {len(concentrated)}")
    print(f"Archivo: {OUTPUT_PATH}")

    all_complete = bool(
        len(summary_df) == len(categories)
        and not summary_df.empty
        and all(
            _summary_is_acceptable(results[item.id][1])
            for item in categories
        )
    )

    if all_complete:
        print("Resultado Walmart: TODAS LAS CATEGORÍAS VÁLIDAS.")
    else:
        failed = [
            item.id
            for item in categories
            if item.id not in results
            or not _summary_is_acceptable(results[item.id][1])
        ]
        print(
            "Resultado Walmart: quedan categorías pendientes: "
            + ", ".join(failed)
        )

    return 0 if all_complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
