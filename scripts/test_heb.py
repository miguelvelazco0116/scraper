from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from playwright.sync_api import sync_playwright

from main import COLUMNS
from scraper.config import load_categories, load_locations
from scraper.quality_rules import meets_rate, valid_after_errors
from scraper.retailers.heb import HEBBlocked, HEBNetworkUnavailable, HEBScraper

OUTPUT_PATH = ROOT / "output" / "heb_phase5_test.xlsx"


def _summary_is_acceptable(summary: dict) -> bool:
    products = int(summary.get("products") or 0)
    target = int(summary.get("target_products") or 0)
    if products <= 0 or target <= 0:
        return False
    return bool(
        str(summary.get("status") or "").upper() == "SUCCESS"
        and meets_rate(products, target)
        and meets_rate(summary.get("sku_complete") or 0, products)
        and meets_rate(summary.get("price_complete") or 0, products)
        and meets_rate(summary.get("url_complete") or 0, products)
        and meets_rate(summary.get("availability_complete") or 0, products)
        and int(summary.get("store_context_complete") or 0) == products
        and bool(summary.get("store_context_verified"))
        and valid_after_errors(products, summary.get("price_order_errors") or 0)
        and str(summary.get("catalogue_source") or "") in {
            "productSearchV3",
            "dom_fallback",
            "productSearchV3+dom_fallback",
        }
        and (
            int(summary.get("structured_responses") or 0) > 0
            or int(summary.get("dom_rows") or 0) > 0
        )
        and not bool(summary.get("blocked_detected"))
    )


def _read_sheet(name: str) -> pd.DataFrame:
    try:
        return pd.read_excel(
            OUTPUT_PATH,
            sheet_name=name,
            dtype={"category_id": str, "sku": str, "store_id": str},
        )
    except Exception:
        return pd.DataFrame()


def _load_existing_valid_results(categories) -> dict[str, dict]:
    if not OUTPUT_PATH.exists():
        return {}
    concentrated = _read_sheet("Concentrado")
    summary_df = _read_sheet("Resumen")
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

    configured = {item.id for item in categories}
    results: dict[str, dict] = {}
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
        results[category_id] = {"frame": frame, "summary": summary}
    return results


def _write_output(results: dict[str, dict], category_order: list[str]):
    ordered = [results[item] for item in category_order if item in results]
    frames = [item["frame"] for item in ordered if not item["frame"].empty]
    summaries = [item["summary"] for item in ordered]
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
    return concentrated, summary_df


def list_cdp_pages(browser) -> list[str]:
    urls: list[str] = []
    for context in browser.contexts:
        for page in context.pages:
            urls.append(page.url or "about:blank")
    return urls


def find_heb_page(browser):
    candidates = []
    for context in browser.contexts:
        for page in context.pages:
            url = (page.url or "").casefold()
            if "heb.com.mx" in url:
                candidates.append(page)

    if not candidates:
        return None

    expected_path = (
        "/cuidado-personal-y-belleza/higiene-dental"
    )
    for page in candidates:
        if expected_path in (page.url or "").casefold():
            return page

    # Si el usuario sólo abrió HEB home, usar esa misma pestaña y
    # navegar desde ahí, igual que Soriana.
    return candidates[-1]


def run_category(category, location, page):
    print("")
    print("-" * 72)
    print(f"HEB | {category.id}")
    print("-" * 72)
    print(f"Departamento : {category.department}")
    print(f"Categoría    : {category.name}")
    print(f"Subcategoría : {category.subcategory}")
    print(f"URL          : {category.url}")
    print("")

    scraper = HEBScraper(
        headless=False,
        browser_channel="chrome",
        wait_ms=1_100,
        max_show_more_clicks=80,
    )
    rows = []
    status = "SUCCESS"
    error = None

    try:
        rows = scraper.scrape_category_on_page(
            page,
            category,
            location,
        )
        status = str(scraper.run_meta.get("status") or "SUCCESS")
    except HEBBlocked as exc:
        status = "BLOCKED"
        error = str(exc)
    except HEBNetworkUnavailable as exc:
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
    current = pd.to_numeric(df["price_current"], errors="coerce") if not df.empty else pd.Series(dtype=float)
    regular = pd.to_numeric(df["price_regular"], errors="coerce") if not df.empty else pd.Series(dtype=float)
    availability = pd.to_numeric(df["is_available"], errors="coerce") if not df.empty else pd.Series(dtype=float)

    summary = {
        "retailer": "HEB",
        "department": category.department,
        "category": category.name,
        "subcategory": category.subcategory,
        "category_id": category.id,
        "status": status,
        "target_products": meta.get("target_products"),
        "products": len(df),
        "coverage": meta.get("coverage"),
        "sku_complete": int(df["sku"].fillna("").astype(str).str.strip().ne("").sum()) if not df.empty else 0,
        "price_complete": int(current.notna().sum()),
        "url_complete": int(df["url"].fillna("").astype(str).str.strip().ne("").sum()) if not df.empty else 0,
        "availability_complete": int(availability.isin([0, 1]).sum()),
        "store_context_complete": int(
            df["store_context_verified"]
            .fillna(False)
            .map(bool)
            .sum()
        ) if not df.empty else 0,
        "available_1": int(availability.eq(1).sum()),
        "unavailable_0": int(availability.eq(0).sum()),
        "price_order_errors": int((current > regular).fillna(False).sum()),
        "structured_responses": int(meta.get("structured_responses") or 0),
        "dom_rows": int(meta.get("dom_rows") or 0),
        "catalogue_source": str(meta.get("catalogue_source") or ""),
        "pagination_method": str(meta.get("pagination_method") or ""),
        "pages_visited": int(meta.get("pages_visited") or 0),
        "show_more_clicks": int(meta.get("show_more_clicks") or 0),
        "numeric_page_clicks": int(meta.get("numeric_page_clicks") or 0),
        "native_next_clicks": int(meta.get("native_next_clicks") or 0),
        "scroll_rounds": int(meta.get("scroll_rounds") or 0),
        "response_errors": int(meta.get("response_errors") or 0),
        "blocked_detected": bool(meta.get("blocked_detected")),
        "store_context_verified": bool(
            df["store_context_verified"].fillna(False).map(bool).all()
        ) if not df.empty else False,
        "store_context_method": str(
            meta.get("store_context_method") or ""
        ),
        "store": str(meta.get("store") or ""),
        "store_id": str(meta.get("store_id") or ""),
        "postal_code": str(meta.get("postal_code") or ""),
        "error": error,
    }

    print("RESULTADO")
    for key in (
        "status", "target_products", "products", "coverage",
        "sku_complete", "price_complete", "url_complete",
        "availability_complete", "store_context_complete",
        "available_1", "unavailable_0",
        "price_order_errors", "structured_responses",
        "dom_rows", "catalogue_source",
        "pagination_method", "pages_visited",
        "show_more_clicks", "numeric_page_clicks",
        "native_next_clicks", "scroll_rounds", "response_errors",
        "blocked_detected", "store_context_verified",
        "store_context_method", "store", "store_id", "postal_code",
    ):
        print(f"{key:29}: {summary[key]}")
    if error:
        print(f"{'error':29}: {error}")

    return df, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="HEB - Fase 5")
    parser.add_argument("--category", default="all")
    parser.add_argument("--retry-failed-at-end", type=int, default=1)
    parser.add_argument("--cdp-url", required=True)
    args = parser.parse_args()

    configured_categories = load_categories(
        ROOT / "config" / "heb" / "categories.yaml"
    )
    categories = list(configured_categories)
    if args.category != "all":
        categories = [item for item in categories if item.id == args.category]
        if not categories:
            raise SystemExit(f"Categoría HEB no encontrada: {args.category}")

    location = next(
        item
        for item in load_locations(ROOT / "config" / "locations.yaml")
        if item.id == "heb-online"
    )

    category_order = [item.id for item in configured_categories]
    category_by_id = {item.id: item for item in configured_categories}
    results = _load_existing_valid_results(configured_categories)
    if args.category != "all":
        results.pop(args.category, None)

    print("=" * 72)
    print("FASE 5 - HEB")
    print("=" * 72)
    print("Umbral        : >= 90%")
    print("Disponibilidad: 1 disponible / 0 no disponible")
    print("Fuente        : Chrome existente + DOM / VTEX si aparece")
    print("")

    if results:
        print("CATEGORÍAS HEB YA VÁLIDAS HOY:")
        for category in configured_categories:
            if category.id in results:
                print(f"- {category.id}: REUTILIZADA, NO SE DESCARGA DE NUEVO")
        print("")

    print(f"CDP           : {args.cdp_url}")
    print("Chrome        : SESIÓN EXISTENTE; el script no lo cierra.")
    print("")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(
                args.cdp_url
            )
        except Exception as exc:
            print("ERROR: no fue posible conectarse al Chrome abierto.")
            print(f"{type(exc).__name__}: {exc}")
            return 10

        visible_pages = list_cdp_pages(browser)
        print("Pestañas visibles por CDP:")
        for index, url in enumerate(visible_pages, start=1):
            print(f"  [{index}] {url}")
        print("")

        page = find_heb_page(browser)
        if page is None:
            print(
                "ERROR: la instancia de Chrome conectada por CDP "
                "no expone ninguna pestaña de HEB."
            )
            print(
                "Abre https://www.heb.com.mx/ en la MISMA ventana/perfil "
                "donde habilitaste remote debugging."
            )
            return 11

        print(f"Pestaña HEB detectada: {page.url}")
        print(
            "HEB fue abierto manualmente; el scraper navegará desde "
            "esa misma pestaña a cada categoría pendiente si hace falta."
        )
        print("")

        failed_ids: list[str] = []

        for category in categories:
            if category.id in results:
                continue
            df, summary = run_category(
                category,
                location,
                page,
            )
            results[category.id] = {
                "frame": df,
                "summary": summary,
            }
            _write_output(results, category_order)

            if (
                not _summary_is_acceptable(summary)
                and str(summary.get("status") or "").upper()
                != "BLOCKED"
            ):
                failed_ids.append(category.id)

            if str(summary.get("status") or "").upper() == "BLOCKED":
                print(
                    "HEB reportó bloqueo en la sesión manual. "
                    "Se detiene sin reintentar."
                )
                break

        for retry_round in range(
            1,
            args.retry_failed_at_end + 1,
        ):
            if not failed_ids:
                break

            pending = list(failed_ids)
            failed_ids = []
            print("")
            print("=" * 72)
            print(
                f"REINTENTO HEB {retry_round}/"
                f"{args.retry_failed_at_end}"
            )
            print("=" * 72)
            print(
                "Sólo se reintentan: "
                + ", ".join(pending)
            )

            for category_id in pending:
                category = category_by_id[category_id]
                df, summary = run_category(
                    category,
                    location,
                    page,
                )
                results[category_id] = {
                    "frame": df,
                    "summary": summary,
                }
                _write_output(results, category_order)

                if str(summary.get("status") or "").upper() == "BLOCKED":
                    print(
                        "HEB reportó bloqueo. "
                        "No se harán más reintentos."
                    )
                    failed_ids = []
                    break

                if not _summary_is_acceptable(summary):
                    failed_ids.append(category_id)

        # No browser.close(): la sesión pertenece al usuario.

    concentrated, summary_df = _write_output(results, category_order)

    print("")
    print("=" * 72)
    print("RESUMEN HEB")
    print("=" * 72)
    if not summary_df.empty:
        print(
            summary_df[
                [
                    "department", "category_id", "status",
                    "target_products", "products", "coverage",
                    "sku_complete", "price_complete", "url_complete",
                    "availability_complete", "available_1",
                    "unavailable_0", "price_order_errors",
                    "structured_responses", "dom_rows",
                    "catalogue_source", "pagination_method",
                    "pages_visited", "show_more_clicks",
                    "numeric_page_clicks", "native_next_clicks",
                    "scroll_rounds",
                    "blocked_detected",
                    "store_context_complete",
                    "store_context_verified",
                    "store_context_method",
                    "store", "store_id", "postal_code",
                ]
            ].to_string(index=False)
        )

    expected = configured_categories if args.category == "all" else categories
    acceptable = bool(
        expected
        and all(
            item.id in results
            and _summary_is_acceptable(results[item.id]["summary"])
            for item in expected
        )
    )

    print("")
    print(f"Total filas: {len(concentrated)}")
    print(f"Archivo: {OUTPUT_PATH}")
    print("Diagnósticos: diagnostics\\heb\\")
    if acceptable:
        print("Resultado Fase 5 / HEB: TODAS LAS CATEGORÍAS VÁLIDAS.")
    else:
        pending = [
            item.id
            for item in expected
            if item.id not in results
            or not _summary_is_acceptable(results[item.id]["summary"])
        ]
        print("Resultado Fase 5 / HEB: pendientes: " + ", ".join(pending))

    return 0 if acceptable else 2


if __name__ == "__main__":
    raise SystemExit(main())
