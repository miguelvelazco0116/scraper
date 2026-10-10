from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from copy import copy
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS
from scraper.config import Category, Location, load_categories, load_locations
from scraper.io_utils import atomic_output_path
from scraper.retailers.chedraui import ChedrauiBlocked, ChedrauiStoreContextError
from scraper.retailers.chedraui_polanco_api import ChedrauiScraper
from scraper.retailers.farmacias_del_ahorro import (
    FarmaciasDelAhorroBlocked,
    FarmaciasDelAhorroNetworkUnavailable,
    FarmaciasDelAhorroScraper,
)
from scraper.retailers.ibarra_mayoreo import (
    IbarraMayoreoBlocked,
    IbarraMayoreoNetworkUnavailable,
    IbarraMayoreoScraper,
)


OUTPUT_PATH = ROOT / "output" / "concentrado_productivo.xlsx"
DIAGNOSTIC_PATH = ROOT / "diagnostics" / "phase1_production" / "last_run.json"

PHASE = "FASE 1"
RETAILERS = (
    "Farmacias del Ahorro",
    "Ibarra Mayoreo",
    "Chedraui",
)


def _set_low_priority() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.kernel32.SetPriorityClass(
            handle,
            BELOW_NORMAL_PRIORITY_CLASS,
        )
    except Exception:
        pass


def _release_between_cases(low_memory: bool) -> None:
    if not low_memory:
        return
    gc.collect()
    time.sleep(2.0)


def _canonical_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame = frame[COLUMNS]
    if frame.empty:
        return frame

    sku = frame["sku"].fillna("").astype(str).str.strip()
    url = frame["url"].fillna("").astype(str).str.strip()
    identified = sku.ne("") | url.ne("")

    with_id = frame.loc[identified].drop_duplicates(
        subset=["retailer", "category_id", "city", "store_id", "sku", "url"],
        keep="last",
    )
    without_id = frame.loc[~identified].drop_duplicates(
        subset=[
            "retailer",
            "category_id",
            "city",
            "store_id",
            "product",
            "price_current",
            "price_raw",
        ],
        keep="last",
    )
    return pd.concat([with_id, without_id], ignore_index=True)


def _count_nonempty(series: pd.Series) -> int:
    return int(series.fillna("").astype(str).str.strip().ne("").sum())


def _base_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    products = len(frame)
    current = pd.to_numeric(frame["price_current"], errors="coerce") if products else pd.Series(dtype=float)
    regular = pd.to_numeric(frame["price_regular"], errors="coerce") if products else pd.Series(dtype=float)
    return {
        "products": products,
        "sku_complete": _count_nonempty(frame["sku"]) if products else 0,
        "url_complete": _count_nonempty(frame["url"]) if products else 0,
        "price_complete": int(current.notna().sum()) if products else 0,
        "price_order_errors": int(
            (current.notna() & regular.notna() & regular.lt(current)).sum()
        ) if products else 0,
    }


def _summary_row(
    retailer: str,
    category: Category,
    frame: pd.DataFrame,
    *,
    status: str,
    target: int | None = None,
    notes: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    metrics = _base_metrics(frame)
    coverage = (
        metrics["products"] / int(target)
        if target not in (None, 0)
        else None
    )
    return {
        "phase": PHASE,
        "retailer": retailer,
        "department": category.department,
        "category": category.name,
        "subcategory": category.subcategory,
        "category_id": category.id,
        "status": status,
        "target_products": target,
        "products": metrics["products"],
        "coverage": coverage,
        "sku_complete": metrics["sku_complete"],
        "price_complete": metrics["price_complete"],
        "url_complete": metrics["url_complete"],
        "price_order_errors": metrics["price_order_errors"],
        "quality_notes": "; ".join(notes or []),
        **extra,
    }


def _run_ahorro(
    categories: list[Category],
    location: Location,
    *,
    low_memory: bool,
) -> tuple[list[pd.DataFrame], list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []

    for index, category in enumerate(categories, start=1):
        print("-" * 72)
        print(f"AHORRO [{index}/{len(categories)}] {category.id}")
        print(category.url)
        scraper = FarmaciasDelAhorroScraper(headless=True, max_pages=100)
        rows: list[dict[str, Any]] = []
        notes: list[str] = []
        error: str | None = None

        try:
            rows = scraper.scrape_category(category, location)
        except (FarmaciasDelAhorroBlocked, FarmaciasDelAhorroNetworkUnavailable) as exc:
            error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        frame = _canonical_frame(rows)
        meta = dict(getattr(scraper, "last_meta", {}) or {})
        target = meta.get("target_products")
        metrics = _base_metrics(frame)

        if error:
            status = "ERROR"
            notes.append(error)
        else:
            if metrics["products"] <= 0:
                notes.append("sin productos")
            if target is None:
                notes.append("target no disponible")
            elif metrics["products"] < int(target):
                notes.append(f"cobertura {metrics['products']}/{target}")
            if metrics["sku_complete"] < metrics["products"]:
                notes.append(f"sku {metrics['sku_complete']}/{metrics['products']}")
            if metrics["price_complete"] < metrics["products"]:
                notes.append(f"precio {metrics['price_complete']}/{metrics['products']}")
            if metrics["url_complete"] < metrics["products"]:
                notes.append(f"url {metrics['url_complete']}/{metrics['products']}")
            if metrics["price_order_errors"]:
                notes.append(f"orden_precio {metrics['price_order_errors']}")
            status = "COMPLETE" if not notes else "PARTIAL"

        if not frame.empty:
            frames.append(frame)
        summary = _summary_row(
            "Farmacias del Ahorro",
            category,
            frame,
            status=status,
            target=target,
            notes=notes,
            api_pages=meta.get("api_pages"),
            store_context_method=meta.get("store_context"),
        )
        summaries.append(summary)
        print(
            f"  -> {status} products={summary['products']} "
            f"target={target} price={summary['price_complete']} "
            f"sku={summary['sku_complete']} url={summary['url_complete']}"
        )
        _release_between_cases(low_memory)

    return frames, summaries


def _run_ibarra(
    categories: list[Category],
    location: Location,
    *,
    headed: bool,
    low_memory: bool,
) -> tuple[list[pd.DataFrame], list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []

    for index, category in enumerate(categories, start=1):
        print("-" * 72)
        print(f"IBARRA [{index}/{len(categories)}] {category.id}")
        print(category.url)
        scraper = IbarraMayoreoScraper(
            headless=not headed,
            browser_channel="chrome",
            max_pages=30,
            wait_ms=700,
            low_memory=low_memory,
            page_recycle_interval=50,
        )
        rows: list[dict[str, Any]] = []
        notes: list[str] = []
        error: str | None = None

        try:
            rows = scraper.scrape_category(category, location)
        except (IbarraMayoreoBlocked, IbarraMayoreoNetworkUnavailable) as exc:
            error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        frame = _canonical_frame(rows)
        meta = dict(getattr(scraper, "last_meta", {}) or {})
        target = meta.get("target_products")
        metrics = _base_metrics(frame)
        parse_errors = len(meta.get("parse_errors") or [])
        no_box = len(meta.get("products_without_box_price") or [])
        discovery_complete = bool(meta.get("discovery_complete"))
        discovery_coverage = (
            metrics["products"] / int(target)
            if target not in (None, 0)
            else None
        )
        discovery_coverage_ok = (
            discovery_complete
            or (
                discovery_coverage is not None
                and discovery_coverage >= 0.90
            )
        )
        unit_count_complete = int(
            frame["units_per_package"].notna().sum()
        ) if not frame.empty else 0
        unit_price_complete = int(
            frame["price_per_unit"].notna().sum()
        ) if not frame.empty else 0
        single_item_products = int(
            frame["is_single_item"].fillna(False).astype(bool).sum()
        ) if not frame.empty else 0
        single_item_price_complete = int(
            frame["single_item_price"].notna().sum()
        ) if not frame.empty else 0

        if error:
            status = "ERROR"
            notes.append(error)
        else:
            if metrics["products"] <= 0:
                notes.append("sin productos con precio utilizable")
            if not discovery_coverage_ok:
                coverage_text = (
                    f"{discovery_coverage:.1%}"
                    if discovery_coverage is not None
                    else "n/d"
                )
                notes.append(
                    f"cobertura_catalogo {coverage_text} < 90%"
                )
            if parse_errors:
                notes.append(f"parse_errors {parse_errors}")
            if metrics["price_complete"] < metrics["products"]:
                notes.append(f"precio {metrics['price_complete']}/{metrics['products']}")
            if unit_count_complete < metrics["products"]:
                notes.append(
                    f"unidades {unit_count_complete}/{metrics['products']}"
                )
            if unit_price_complete < metrics["products"]:
                notes.append(
                    f"precio_unitario {unit_price_complete}/{metrics['products']}"
                )
            if metrics["url_complete"] < metrics["products"]:
                notes.append(f"url {metrics['url_complete']}/{metrics['products']}")
            if metrics["price_order_errors"]:
                notes.append(f"orden_precio {metrics['price_order_errors']}")
            # SKU incompleto y ausencia de precio individual se informan.
            # Unidades por presentación y precio por pieza sí son bloqueantes.
            status = "COMPLETE" if not notes else "PARTIAL"

        if not frame.empty:
            frames.append(frame)
        summary = _summary_row(
            "Ibarra Mayoreo",
            category,
            frame,
            status=status,
            target=target,
            notes=notes,
            discovery_complete=discovery_complete,
            discovery_coverage=discovery_coverage,
            discovery_coverage_ok=discovery_coverage_ok,
            discovery_threshold=0.90,
            parse_errors=parse_errors,
            products_without_box_price=no_box,
            unit_count_complete=unit_count_complete,
            unit_price_complete=unit_price_complete,
            single_item_products=single_item_products,
            single_item_price_complete=single_item_price_complete,
            product_links=meta.get("product_links"),
        )
        summaries.append(summary)
        print(
            f"  -> {status} products={summary['products']} "
            f"links={meta.get('product_links')} "
            f"coverage={discovery_coverage if discovery_coverage is not None else 'n/d'} "
            f"price={summary['price_complete']} "
            f"units={unit_count_complete} unit_price={unit_price_complete} "
            f"single={single_item_products} sku={summary['sku_complete']} "
            f"no_box={no_box}"
        )
        _release_between_cases(low_memory)

    return frames, summaries


def _run_chedraui(
    categories: list[Category],
    location: Location,
    *,
    profile_dir: Path,
    headed: bool,
    low_memory: bool,
) -> tuple[list[pd.DataFrame], list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []

    for index, category in enumerate(categories, start=1):
        print("-" * 72)
        print(f"CHEDRAUI [{index}/{len(categories)}] {category.id}")
        print(category.url)
        scraper = ChedrauiScraper(
            headless=not headed,
            browser_channel="chrome",
            profile_dir=profile_dir,
            max_pages=100,
            low_memory=low_memory,
        )
        rows: list[dict[str, Any]] = []
        notes: list[str] = []
        error: str | None = None

        try:
            rows = scraper.scrape_category(category, location)
        except (ChedrauiBlocked, ChedrauiStoreContextError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        frame = _canonical_frame(rows)
        meta = dict(getattr(scraper, "run_meta", {}) or {})
        target = meta.get("displayed_category_products")
        metrics = _base_metrics(frame)
        structured_missing = len(meta.get("structured_price_missing_pages") or [])
        pagination_gaps = len(meta.get("internal_pagination_gaps") or [])
        store_verified = bool(meta.get("store_context_verified"))

        if frame.empty:
            availability = pd.Series(dtype=str)
        else:
            availability = (
                frame["availability_status"]
                .fillna("UNKNOWN")
                .astype(str)
                .str.upper()
            )
        price_required_mask = ~availability.eq("UNAVAILABLE") if not frame.empty else pd.Series(dtype=bool)
        current = pd.to_numeric(frame["price_current"], errors="coerce") if not frame.empty else pd.Series(dtype=float)
        price_required = int(price_required_mask.sum()) if not frame.empty else 0
        price_required_complete = int(
            (price_required_mask & current.notna() & current.gt(0)).sum()
        ) if not frame.empty else 0

        if error:
            status = "ERROR"
            notes.append(error)
        else:
            if metrics["products"] <= 0:
                notes.append("sin productos")
            if target is None:
                notes.append("target no disponible")
            elif metrics["products"] < int(target):
                notes.append(f"cobertura {metrics['products']}/{target}")
            if metrics["sku_complete"] < metrics["products"]:
                notes.append(f"sku {metrics['sku_complete']}/{metrics['products']}")
            if metrics["url_complete"] < metrics["products"]:
                notes.append(f"url {metrics['url_complete']}/{metrics['products']}")
            if price_required_complete < price_required:
                notes.append(f"precio_requerido {price_required_complete}/{price_required}")
            if metrics["price_order_errors"]:
                notes.append(f"orden_precio {metrics['price_order_errors']}")
            if not store_verified:
                notes.append("tienda no verificada")
            if structured_missing:
                notes.append(f"structured_missing {structured_missing}")
            if pagination_gaps:
                notes.append(f"pagination_gaps {pagination_gaps}")
            status = "COMPLETE" if not notes else "PARTIAL"

        if not frame.empty:
            frames.append(frame)
        summary = _summary_row(
            "Chedraui",
            category,
            frame,
            status=status,
            target=target,
            notes=notes,
            price_required=price_required,
            price_required_complete=price_required_complete,
            store_context_verified=store_verified,
            store_context_method=meta.get("store_context_method"),
            api_price_pages=len(meta.get("api_price_pages") or []),
            structured_price_missing_pages=structured_missing,
            pagination_gaps=pagination_gaps,
        )
        summaries.append(summary)
        print(
            f"  -> {status} products={summary['products']} target={target} "
            f"required_price={price_required_complete}/{price_required} "
            f"structured_missing={structured_missing} gaps={pagination_gaps}"
        )
        _release_between_cases(low_memory)

    return frames, summaries


def _write_productive(
    concentrated: pd.DataFrame,
    summary: pd.DataFrame,
    *,
    generated_at: str,
) -> None:
    control = pd.DataFrame(
        [
            {
                "phase": PHASE,
                "status": "PRODUCTIVE",
                "generated_at": generated_at,
                "retailers": " | ".join(RETAILERS),
                "categories": len(summary),
                "products": len(concentrated),
                "all_categories_complete": bool(
                    summary["status"].eq("COMPLETE").all()
                ),
                "output": str(OUTPUT_PATH),
            }
        ]
    )

    with atomic_output_path(OUTPUT_PATH) as temporary:
        with pd.ExcelWriter(temporary, engine="openpyxl") as writer:
            concentrated.to_excel(writer, index=False, sheet_name="Concentrado")
            summary.to_excel(writer, index=False, sheet_name="Resumen")
            control.to_excel(writer, index=False, sheet_name="Control")

            workbook = writer.book
            for sheet_name in ("Concentrado", "Resumen", "Control"):
                ws = workbook[sheet_name]
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions
                for cell in ws[1]:
                    font = copy(cell.font)
                    font.bold = True
                    cell.font = font
                for cells in ws.columns:
                    sample = [
                        str(cell.value) if cell.value is not None else ""
                        for cell in cells[:250]
                    ]
                    width = min(
                        max(max((len(value) for value in sample), default=0) + 2, 10),
                        42,
                    )
                    ws.column_dimensions[cells[0].column_letter].width = width

            for column in ("P", "Q", "V", "X"):
                for cell in workbook["Concentrado"][column]:
                    if cell.row > 1:
                        cell.number_format = "$#,##0.0000"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fase 1 productiva: Ahorro + Ibarra + Chedraui."
    )
    parser.add_argument(
        "--profile-dir",
        default=".chedraui_profile",
        help="Perfil persistente de Chedraui.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Abre Chrome visible para Ibarra y Chedraui.",
    )
    parser.add_argument(
        "--low-memory",
        action="store_true",
        help="Optimiza Chrome para servidores con poca RAM.",
    )
    args = parser.parse_args()

    if args.low_memory:
        _set_low_priority()

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    locations = {
        item.id: item
        for item in load_locations(ROOT / "config" / "locations.yaml")
    }

    configurations = {
        "ahorro": (
            load_categories(ROOT / "config" / "farmacias-del-ahorro" / "categories.yaml"),
            locations["fahorro-online"],
        ),
        "ibarra": (
            load_categories(ROOT / "config" / "ibarra-mayoreo" / "categories.yaml"),
            locations["ibarra-online"],
        ),
        "chedraui": (
            load_categories(ROOT / "config" / "chedraui" / "categories.yaml"),
            locations["chedraui-polanco"],
        ),
    }

    expected_cases = sum(len(value[0]) for value in configurations.values())

    print("=" * 72)
    print("FASE 1 - CORRIDA PRODUCTIVA")
    print("=" * 72)
    print("Retailers : Farmacias del Ahorro, Ibarra Mayoreo, Chedraui")
    print(f"Categorías: {expected_cases}")
    print(f"Salida    : {OUTPUT_PATH}")
    print(f"Navegador : {'visible' if args.headed else 'headless'}")
    print(f"Memoria   : {'LOW-MEMORY' if args.low_memory else 'standard'}")
    print("Publicación: sólo si TODOS los casos quedan COMPLETE")
    print("")

    all_frames: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []

    frames, rows = _run_ahorro(
        *configurations["ahorro"],
        low_memory=args.low_memory,
    )
    all_frames.extend(frames)
    summaries.extend(rows)

    frames, rows = _run_ibarra(
        *configurations["ibarra"],
        headed=args.headed,
        low_memory=args.low_memory,
    )
    all_frames.extend(frames)
    summaries.extend(rows)

    frames, rows = _run_chedraui(
        *configurations["chedraui"],
        profile_dir=(ROOT / args.profile_dir).resolve(),
        headed=args.headed,
        low_memory=args.low_memory,
    )
    all_frames.extend(frames)
    summaries.extend(rows)

    summary = pd.DataFrame(summaries)
    concentrated = (
        pd.concat(all_frames, ignore_index=True)
        if all_frames
        else pd.DataFrame(columns=COLUMNS)
    )
    concentrated = _canonical_frame(concentrated)
    if not concentrated.empty:
        concentrated = concentrated.sort_values(
            ["retailer", "category_id", "brand", "product"],
            na_position="last",
        ).reset_index(drop=True)

    DIAGNOSTIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    diagnostic_payload = {
        "phase": PHASE,
        "generated_at": generated_at,
        "output": str(OUTPUT_PATH),
        "categories_expected": expected_cases,
        "categories_executed": len(summary),
        "products_collected": len(concentrated),
        "results": summary.where(pd.notna(summary), None).to_dict("records"),
    }
    DIAGNOSTIC_PATH.write_text(
        json.dumps(diagnostic_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    print("")
    print("=" * 72)
    print("RESUMEN FASE 1")
    print("=" * 72)
    display_columns = [
        "retailer",
        "category_id",
        "status",
        "target_products",
        "products",
        "sku_complete",
        "price_complete",
        "url_complete",
        "quality_notes",
    ]
    print(summary[display_columns].to_string(index=False))

    publishable = (
        len(summary) == expected_cases
        and not summary.empty
        and summary["status"].eq("COMPLETE").all()
    )

    if not publishable:
        print("")
        print("FASE 1 NO PUBLICADA.")
        print("El master productivo anterior NO fue reemplazado.")
        print(f"Diagnóstico: {DIAGNOSTIC_PATH}")
        return 2

    _write_productive(
        concentrated,
        summary,
        generated_at=generated_at,
    )

    print("")
    print("FASE 1 PUBLICADA CORRECTAMENTE")
    print(f"Filas productivas : {len(concentrated)}")
    print(f"Archivo productivo: {OUTPUT_PATH}")
    print(f"Diagnóstico       : {DIAGNOSTIC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
