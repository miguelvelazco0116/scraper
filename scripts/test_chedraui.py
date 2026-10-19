from __future__ import annotations

import argparse
import sys
import time
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
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=COLUMNS)
    summary = pd.DataFrame(summaries)

    with atomic_output_path(output_path) as temporary:
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
            "structured_price_missing_pages": len(
                meta.get("structured_price_missing_pages") or []
            ),
            "pagination_gaps": len(
                meta.get("internal_pagination_gaps") or []
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
    structured_missing = len(
        meta.get("structured_price_missing_pages") or []
    )
    pagination_gaps = len(
        meta.get("internal_pagination_gaps") or []
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
        and structured_missing == 0
        and pagination_gaps == 0
        and (
            target is None
            or (
                int(target) > 0
                and products / int(target) >= 0.90
            )
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
        "structured_price_missing_pages": structured_missing,
        "pagination_gaps": pagination_gaps,
        "price_order_errors": price_order_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prueba aislada de Chedraui por tienda."
    )
    parser.add_argument(
        "--category",
        choices=[
            "higiene-bucal",
            "lavanderia",
            "desodorantes-y-antitranspirantes",
            "depilacion",
            "respiratorios",
            "candidates",
            "all",
            "full",
        ],
        default="all",
    )
    parser.add_argument(
        "--store",
        choices=[
            "chedraui-polanco",
            "chedraui-tepeyac",
            "chedraui-cancun-kabah",
            "chedraui-puebla-centro-sur",
        ],
        default="chedraui-polanco",
    )
    parser.add_argument(
        "--profile-dir",
        default=None,
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
    parser.add_argument(
        "--category-delay-seconds",
        type=int,
        default=0,
        help="Pausa entre categorías dentro de la misma tienda.",
    )
    args = parser.parse_args()

    active_categories = load_categories(
        ROOT / "config" / "chedraui" / "categories.yaml"
    )
    candidate_categories = load_categories(
        ROOT / "config" / "chedraui" / "candidate_categories.yaml"
    )
    candidate_ids = {item.id for item in candidate_categories}

    if args.category == "all":
        # Preserve historical semantics: only currently productive categories.
        categories = active_categories
    elif args.category == "full":
        # Exhaustive validation mode: productive + candidate categories.
        categories = active_categories + candidate_categories
    elif args.category == "candidates":
        categories = candidate_categories
    elif args.category in candidate_ids:
        categories = [
            item for item in candidate_categories
            if item.id == args.category
        ]
    else:
        categories = [
            item for item in active_categories
            if item.id == args.category
        ]

    locations = {
        item.id: item
        for item in load_locations(
            ROOT / "config" / "locations.yaml"
        )
    }
    location = locations[args.store]
    profile_dir = (
        args.profile_dir
        or (
            ".chedraui_profile"
            if args.store == "chedraui-polanco"
            else f".chedraui_profile_{args.store.removeprefix('chedraui-')}"
        )
    )
    output_path = (
        ROOT
        / "output"
        / f"chedraui_{args.store.removeprefix('chedraui-').replace('-', '_')}_test.xlsx"
    )

    all_rows: list[dict] = []
    summaries: list[dict] = []

    print("=" * 72)
    print("CHEDRAUI - PRUEBA AISLADA POR TIENDA")
    print("=" * 72)
    print(f"Categorias : {len(categories)}")
    print(f"Tienda     : {location.store} ({location.store_id})")
    print(f"CP         : {location.postal_code}")
    print(f"Profile    : {profile_dir}")
    print(f"Delay cat. : {args.category_delay_seconds} segundos")
    print("Consolidado: sin cambios")
    print("")

    for index, category in enumerate(categories, start=1):
        print("-" * 72)
        print(f"[{index}/{len(categories)}] {category.id}")
        print(category.url)
        print("-" * 72)

        scraper = ChedrauiScraper(
            # Store certification uses the visible directory UI:
            # State -> City/Zone -> exact store.
            headless=False,
            browser_channel="chrome",
            profile_dir=profile_dir,
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
            "store_context_contract_version": (
                scraper.run_meta.get(
                    "store_context_contract_version"
                )
            ),
            "vtex_region_mutation_skipped": (
                scraper.run_meta.get(
                    "vtex_region_mutation_skipped"
                )
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
        _write_output(all_rows, summaries, output_path)

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
            "store_context_contract_version",
            "vtex_region_mutation_skipped",
            "api_price_pages",
            "structured_price_missing_pages",
            "pagination_gaps",
            "price_order_errors",
        ):
            print(f"{key:32}: {summary.get(key)}")
        if status == "STORE_CONTEXT_ERROR":
            print("DIAGNOSTICO DE SELECCION DE TIENDA")
            for key in (
                "location_button_opened",
                "directory_clicked",
                "state_selected",
                "selected_state_label",
                "city_selected",
                "selected_city_label",
                "store_clicked",
                "directory_store_match_method",
                "directory_store_expected_pickup_value",
                "directory_store_radio_matches",
                "directory_store_radio_verified",
                "pickup_confirmed",
                "directory_header_matched_after_confirmation",
                "visible_store_control_text",
                "directory_selection_failure",
                "directory_ui_steps",
            ):
                print(f"{key:42}: {scraper.run_meta.get(key)}")
            header_checks = scraper.run_meta.get(
                "directory_header_observations"
            ) or []
            if header_checks:
                print("ULTIMAS VERIFICACIONES DE ENCABEZADO")
                for item in header_checks[-4:]:
                    print(f"  {item}")
            print("Metadatos: diagnostics\\chedraui_run_meta.json")
        print(f"Output: {output_path}")
        print("")

        if status == "STORE_CONTEXT_ERROR":
            remaining = len(categories) - index
            if remaining > 0:
                print(
                    f"Abortando {remaining} categoría(s) restante(s): "
                    "la tienda aún no está verificada."
                )
                print("")
            break

        if (
            index < len(categories)
            and args.category_delay_seconds > 0
        ):
            print(
                f"Pausa de {args.category_delay_seconds} segundos "
                "antes de la siguiente categoría..."
            )
            print("")
            time.sleep(args.category_delay_seconds)

    print("=" * 72)
    print("RESUMEN CHEDRAUI")
    print("=" * 72)
    for item in summaries:
        print(
            f"{item['category_id']}: {item['status']} "
            f"({item['products']}/{item['target_products']})"
        )

    print("")
    print(f"Archivo: {output_path}")
    print("Consolidado: sin cambios")

    return 0 if all(
        item["status"] == "COMPLETE"
        for item in summaries
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
