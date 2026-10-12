from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS, deduplicate_catalog
from scraper.config import load_categories, load_locations
from scraper.retailers.walmart import WalmartBlocked, WalmartStoreContextError
from scraper.retailers.walmart_persistent import WalmartPersistentScraper
from scraper.retailers.walmart_storage_state import WalmartStorageStateScraper

OUTPUT_PATH = ROOT / "output" / "walmart_phase3_test.xlsx"


def _count_nonempty(series: pd.Series) -> int:
    return int(series.fillna("").astype(str).str.strip().ne("").sum())


def run_category(
    category,
    location,
    *,
    profile_dir: Path | None,
    storage_state: Path | None,
    headless: bool,
    max_pages: int,
):
    print("")
    print("-" * 78)
    print(f"WALMART | {category.id}")
    print("-" * 78)
    print(f"Tienda      : {location.store} ({location.store_id})")
    print(f"CP          : {location.postal_code}")
    print(f"URL         : {category.url}")

    kwargs = {
        "headless": headless,
        "max_pages": max_pages,
        "manual_verification_timeout_ms": 180_000,
    }
    if storage_state is not None:
        scraper = WalmartStorageStateScraper(
            storage_state_path=storage_state,
            **kwargs,
        )
        session_method = "storage_state"
    else:
        scraper = WalmartPersistentScraper(
            user_data_dir=profile_dir,
            **kwargs,
        )
        session_method = "persistent_profile"

    rows = []
    error = None
    source_status = "SUCCESS"

    try:
        rows = scraper.scrape_category(category, location)
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
    store_context_meta = bool(meta.get("store_context_verified"))

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
    if pages_visited <= 0:
        notes.append("sin páginas visitadas")

    status = "COMPLETE" if not notes else "PARTIAL"

    summary = {
        "retailer": "Walmart",
        "department": category.department,
        "category": category.name,
        "subcategory": category.subcategory,
        "category_id": category.id,
        "status": status,
        "source_status": source_status,
        "products": products,
        "sku_complete": sku_complete,
        "price_complete": price_complete,
        "url_complete": url_complete,
        "store_context_complete": store_context_complete,
        "store_context_verified": store_context_meta,
        "store_context_method": meta.get("store_context_method"),
        "pages_visited": pages_visited,
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
        "products",
        "sku_complete",
        "price_complete",
        "url_complete",
        "store_context_complete",
        "store_context_verified",
        "store_context_method",
        "pages_visited",
        "price_order_errors",
        "duplicates_sku_url",
    ):
        print(f"{key:28}: {summary[key]}")
    if error:
        print(f"{'error':28}: {error}")

    return frame, summary, meta


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prueba completa Walmart para Fase 3."
    )
    parser.add_argument("--profile-dir", default=".walmart_profile")
    parser.add_argument("--storage-state")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-pages", type=int, default=100)
    args = parser.parse_args()

    profile_dir = Path(args.profile_dir).resolve()
    storage_state = (
        Path(args.storage_state).resolve()
        if args.storage_state
        else None
    )
    if storage_state is not None and not storage_state.exists():
        raise SystemExit(
            f"Storage state Walmart no encontrado: {storage_state}"
        )
    if storage_state is None and not profile_dir.exists():
        raise SystemExit(
            f"Perfil Walmart no encontrado: {profile_dir}. "
            "Ejecuta scripts/walmart_prepare_session.py primero."
        )

    categories = list(
        load_categories(ROOT / "config" / "walmart" / "categories.yaml")
    )
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
    print(
        f"Sesión     : {'storage state' if storage_state else profile_dir}"
    )
    print("")

    frames = []
    summaries = []
    metas = []

    for category in categories:
        frame, summary, meta = run_category(
            category,
            location,
            profile_dir=profile_dir,
            storage_state=storage_state,
            headless=args.headless,
            max_pages=args.max_pages,
        )
        frames.append(frame)
        summaries.append(summary)
        metas.append(
            {
                "category_id": category.id,
                "meta_json": json.dumps(
                    meta,
                    ensure_ascii=False,
                    default=str,
                ),
            }
        )

    concentrated = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=COLUMNS)
    )
    summary_df = pd.DataFrame(summaries)

    all_complete = bool(
        len(summary_df) == len(categories)
        and not summary_df.empty
        and summary_df["status"].eq("COMPLETE").all()
    )
    control = pd.DataFrame(
        [
            {
                "status": "PRODUCTIVE" if all_complete else "PARTIAL",
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

    print("")
    print("=" * 78)
    print("RESUMEN WALMART FASE 3")
    print("=" * 78)
    print(
        summary_df[
            [
                "category_id",
                "status",
                "products",
                "sku_complete",
                "price_complete",
                "url_complete",
                "store_context_complete",
                "store_context_verified",
                "pages_visited",
                "price_order_errors",
                "duplicates_sku_url",
                "quality_notes",
            ]
        ].to_string(index=False)
    )
    print("")
    print(f"Total filas: {len(concentrated)}")
    print(f"Archivo: {OUTPUT_PATH}")
    return 0 if all_complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
