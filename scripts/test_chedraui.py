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
from scraper.io_utils import atomic_output_path
from scraper.retailers.chedraui import (
    ChedrauiBlocked,
    ChedrauiStoreContextError,
)
from scraper.retailers.chedraui_polanco_api import ChedrauiScraper


OUTPUT_PATH = ROOT / "output" / "chedraui_test.xlsx"


def _write_output(
    rows: list[dict],
    summaries: list[dict],
) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=COLUMNS)
    summary = pd.DataFrame(summaries)

    with atomic_output_path(OUTPUT_PATH) as temporary:
        with pd.ExcelWriter(temporary, engine="openpyxl") as writer:
            frame.to_excel(writer, index=False, sheet_name="Concentrado")
            summary.to_excel(writer, index=False, sheet_name="Resumen")

            workbook = writer.book
            for sheet_name in ("Concentrado", "Resumen"):
                ws = workbook[sheet_name]
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions
                for cell in ws[1]:
                    font = copy(cell.font)
                    font.bold = True
                    cell.font = font


def _quality(frame: pd.DataFrame, meta: dict) -> tuple[str, dict]:
    products = len(frame)
    target = meta.get("displayed_category_products")

    if frame.empty:
        return "EMPTY", {
            "products": 0,
            "target_products": target,
            "coverage": None,
            "sku_complete": 0,
            "url_complete": 0,
            "price_required": 0,
            "price_required_complete": 0,
            "store_context_verified": bool(
                meta.get("store_context_verified")
            ),
            "price_order_errors": 0,
        }

    availability = (
        frame["availability_status"]
        .fillna("UNKNOWN")
        .astype(str)
        .str.strip()
        .str.upper()
    )
    price_required_mask = ~availability.eq("UNAVAILABLE")
    price_required = int(price_required_mask.sum())

    current = pd.to_numeric(
        frame["price_current"],
        errors="coerce",
    )
    regular = pd.to_numeric(
        frame["price_regular"],
        errors="coerce",
    )

    price_required_complete = int(
        (
            price_required_mask
            & current.notna()
            & current.gt(0)
        ).sum()
    )
    price_order_errors = int(
        (
            current.notna()
            & regular.notna()
            & regular.lt(current)
        ).sum()
    )

    sku_complete = int(
        frame["sku"]
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
        .sum()
    )
    url_complete = int(
        frame["url"]
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
        .sum()
    )
    store_context_verified = bool(
        meta.get("store_context_verified")
    )

    coverage = None
    if target:
        coverage = products / int(target)

    complete = (
        products > 0
        and price_required_complete == price_required
        and price_order_errors == 0
        and sku_complete == products
        and url_complete == products
        and store_context_verified
        and (
            target is None
            or products >= int(target)
        )
    )

    return ("COMPLETE" if complete else "PARTIAL"), {
        "products": products,
        "target_products": target,
        "coverage": coverage,
        "sku_complete": sku_complete,
        "url_complete": url_complete,
        "price_required": price_required,
        "price_required_complete": price_required_complete,
        "store_context_verified": store_context_verified,
        "price_order_errors": price_order_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prueba aislada Chedraui Polanco."
    )
    parser.add_argument(
        "--category",
        choices=["higiene-bucal", "lavanderia", "all"],
        default="higiene-bucal",
    )
    parser.add_argument(
        "--profile-dir",
        default=".chedraui_profile",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Abre Chrome visible.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=100,
    )
    args = parser.parse_args()

    categories = load_categories(
        ROOT / "config" / "chedraui" / "categories.yaml"
    )
    if args.category != "all":
        categories = [
            item for item in categories
            if item.id == args.category
        ]

    locations = {
        item.id: item
        for item in load_locations(
            ROOT / "config" / "locations.yaml"
        )
    }
    location = locations["chedraui-polanco"]

    all_rows: list[dict] = []
    summaries: list[dict] = []

    print("=" * 72)
    print("CHEDRAUI POLANCO - PRUEBA AISLADA")
    print("=" * 72)
    print(f"Categorias : {len(categories)}")
    print(f"Tienda     : {location.store} ({location.store_id})")
    print(f"CP         : {location.postal_code}")
    print(f"Profile    : {args.profile_dir}")
    print("Consolidado: sin cambios")
    print("")

    for index, category in enumerate(categories, start=1):
        print("-" * 72)
        print(f"[{index}/{len(categories)}] {category.id}")
        print(category.url)
        print("-" * 72)

        scraper = ChedrauiScraper(
            headless=not args.headed,
            browser_channel="chrome",
            profile_dir=args.profile_dir,
            max_pages=args.max_pages,
        )

        rows: list[dict] = []
        error = None

        try:
            rows = scraper.scrape_category(
                category,
                location,
            )
        except ChedrauiBlocked as exc:
            error = f"BLOCKED: {exc}"
            print(error)
        except ChedrauiStoreContextError as exc:
            error = f"STORE_CONTEXT_ERROR: {exc}"
            print(error)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            print(f"ERROR: {error}")

        frame = pd.DataFrame(rows, columns=COLUMNS)
        if not frame.empty:
            frame = frame.drop_duplicates(
                subset=["sku", "url"],
                keep="last",
            ).reset_index(drop=True)
            all_rows.extend(frame.to_dict("records"))

        status, metrics = _quality(
            frame,
            scraper.run_meta or {},
        )

        if error:
            if error.startswith("BLOCKED"):
                status = "BLOCKED"
            elif error.startswith("STORE_CONTEXT_ERROR"):
                status = "STORE_CONTEXT_ERROR"
            else:
                status = "ERROR"

        summary = {
            "category_id": category.id,
            "status": status,
            **metrics,
            "available_products": int(
                frame["availability_status"]
                .fillna("UNKNOWN")
                .astype(str)
                .str.upper()
                .eq("AVAILABLE")
                .sum()
            ) if not frame.empty else 0,
            "unavailable_products": int(
                frame["availability_status"]
                .fillna("UNKNOWN")
                .astype(str)
                .str.upper()
                .eq("UNAVAILABLE")
                .sum()
            ) if not frame.empty else 0,
            "availability_unknown": int(
                frame["availability_status"]
                .fillna("UNKNOWN")
                .astype(str)
                .str.upper()
                .eq("UNKNOWN")
                .sum()
            ) if not frame.empty else 0,
            "store_context_method": (
                scraper.run_meta.get("store_context_method")
            ),
            "api_price_pages": len(
                scraper.run_meta.get("api_price_pages") or []
            ),
            "structured_price_missing_pages": len(
                scraper.run_meta.get(
                    "structured_price_missing_pages"
                )
                or []
            ),
            "pagination_gaps": len(
                scraper.run_meta.get(
                    "internal_pagination_gaps"
                )
                or []
            ),
            "error": error,
        }
        summaries.append(summary)
        _write_output(all_rows, summaries)

        print("RESULTADO")
        for key in (
            "status",
            "products",
            "target_products",
            "coverage",
            "sku_complete",
            "url_complete",
            "price_required",
            "price_required_complete",
            "store_context_verified",
            "store_context_method",
            "api_price_pages",
            "structured_price_missing_pages",
            "pagination_gaps",
            "price_order_errors",
        ):
            print(f"{key:32}: {summary.get(key)}")
        print(f"Output: {OUTPUT_PATH}")
        print("")

    print("=" * 72)
    print("RESUMEN CHEDRAUI")
    print("=" * 72)
    for item in summaries:
        print(
            f"{item['category_id']}: {item['status']} "
            f"({item['products']}/{item['target_products']})"
        )

    print("")
    print(f"Archivo: {OUTPUT_PATH}")
    print("Consolidado: sin cambios")

    return 0 if all(
        item["status"] == "COMPLETE"
        for item in summaries
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
