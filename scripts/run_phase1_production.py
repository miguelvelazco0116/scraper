from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
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
from scraper.quality_rules import MIN_SUCCESS_RATE, meets_rate, valid_after_errors
from scraper.retailers.chedraui import (
    ChedrauiBlocked,
    ChedrauiStoreContextError,
    STORE_CONTEXT_CONTRACT_VERSION,
)
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
BASELINE_PATH = ROOT / "output" / "fase1_muestra_inicial_valida.xlsx"
BASELINE_META_PATH = ROOT / "output" / "fase1_muestra_inicial_valida.json"
DIAGNOSTIC_PATH = ROOT / "diagnostics" / "phase1_production" / "last_run.json"
PROGRESS_PATH = ROOT / "output" / "fase1_progress.xlsx"

PHASE = "FASE 1"
RETAILERS = (
    "Farmacias del Ahorro",
    "Ibarra Mayoreo",
    "Chedraui",
)


def _normalize_store_id(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip().removesuffix(".0")


def _case_key(
    retailer: str,
    category_id: str,
    store_id: Any = None,
) -> tuple[str, str, str]:
    store = _normalize_store_id(store_id) if retailer == "Chedraui" else ""
    return (str(retailer).strip(), str(category_id).strip(), store)


def _read_phase1_snapshot(
    path: Path,
) -> dict[tuple[str, str, str], tuple[pd.DataFrame, dict[str, Any]]]:
    if not path.exists():
        return {}

    try:
        frame = pd.read_excel(
            path,
            sheet_name="Concentrado",
            dtype={"sku": str, "store_id": str, "category_id": str},
        )
        summary = pd.read_excel(
            path,
            sheet_name="Resumen",
            dtype={"category_id": str, "store_id": str},
        )
        control = pd.read_excel(path, sheet_name="Control")
    except Exception:
        return {}

    today = datetime.now().astimezone().date().isoformat()
    capture_date = ""
    if not control.empty:
        capture_date = str(control.iloc[0].get("capture_date") or "").strip()
    if capture_date and capture_date != today:
        return {}

    if frame.empty or summary.empty:
        return {}

    dates = set(
        frame["scrape_timestamp"]
        .dropna()
        .astype(str)
        .str.slice(0, 10)
    )
    if dates and dates != {today}:
        return {}

    results: dict[
        tuple[str, str, str],
        tuple[pd.DataFrame, dict[str, Any]],
    ] = {}

    for _, summary_row in summary.iterrows():
        if str(summary_row.get("status") or "").strip().upper() != "COMPLETE":
            continue

        retailer = str(summary_row.get("retailer") or "").strip()
        category_id = str(summary_row.get("category_id") or "").strip()
        store_id = summary_row.get("store_id")

        if retailer == "Chedraui":
            contract = str(
                summary_row.get("store_context_contract_version") or ""
            ).strip()
            if contract != STORE_CONTEXT_CONTRACT_VERSION:
                continue
        key = _case_key(retailer, category_id, store_id)

        mask = (
            frame["retailer"].astype(str).eq(retailer)
            & frame["category_id"].astype(str).eq(category_id)
        )
        if retailer == "Chedraui":
            normalized = frame["store_id"].map(_normalize_store_id)
            mask = mask & normalized.eq(key[2])

        subset = frame.loc[mask].copy()
        try:
            expected_products = int(summary_row.get("products"))
        except (TypeError, ValueError):
            continue
        if len(subset) != expected_products or expected_products <= 0:
            continue

        results[key] = (subset, summary_row.to_dict())

    return results


def _load_existing_valid_results(
) -> dict[tuple[str, str, str], tuple[pd.DataFrame, dict[str, Any]]]:
    progress = _read_phase1_snapshot(PROGRESS_PATH)
    if progress:
        return progress
    return _read_phase1_snapshot(OUTPUT_PATH)


def _write_progress(
    concentrated: pd.DataFrame,
    summary: pd.DataFrame,
    *,
    generated_at: str,
) -> None:
    control = pd.DataFrame(
        [
            {
                "phase": PHASE,
                "status": "PROGRESS",
                "generated_at": generated_at,
                "capture_date": generated_at[:10],
                "categories": int(
                    summary[["retailer", "category_id"]]
                    .drop_duplicates()
                    .shape[0]
                ) if not summary.empty else 0,
                "execution_cases": len(summary),
                "products": len(concentrated),
            }
        ]
    )
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output_path(PROGRESS_PATH) as temporary:
        with pd.ExcelWriter(temporary, engine="openpyxl") as writer:
            concentrated.to_excel(writer, index=False, sheet_name="Concentrado")
            summary.to_excel(writer, index=False, sheet_name="Resumen")
            control.to_excel(writer, index=False, sheet_name="Control")


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
            else:
                coverage = (
                    metrics["products"] / int(target)
                    if int(target) > 0
                    else 0.0
                )
                if coverage < 0.90:
                    notes.append(
                        f"cobertura {metrics['products']}/{target} "
                        f"({coverage:.1%}) < 90%"
                    )
            if not meets_rate(metrics["sku_complete"], metrics["products"]):
                notes.append(f"sku {metrics['sku_complete']}/{metrics['products']} < 90%")
            if not meets_rate(metrics["price_complete"], metrics["products"]):
                notes.append(f"precio {metrics['price_complete']}/{metrics['products']} < 90%")
            if not meets_rate(metrics["url_complete"], metrics["products"]):
                notes.append(f"url {metrics['url_complete']}/{metrics['products']} < 90%")
            if not valid_after_errors(
                metrics["products"],
                metrics["price_order_errors"],
            ):
                notes.append(
                    f"orden_precio_valido < {MIN_SUCCESS_RATE:.0%}"
                )
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
        product_links = meta.get("product_links")
        discovery_coverage = (
            int(product_links) / int(target)
            if (
                target not in (None, 0)
                and product_links is not None
            )
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
            if not meets_rate(metrics["price_complete"], metrics["products"]):
                notes.append(f"precio {metrics['price_complete']}/{metrics['products']} < 90%")
            if not meets_rate(unit_count_complete, metrics["products"]):
                notes.append(
                    f"unidades {unit_count_complete}/{metrics['products']} < 90%"
                )
            if not meets_rate(unit_price_complete, metrics["products"]):
                notes.append(
                    f"precio_unitario {unit_price_complete}/{metrics['products']} < 90%"
                )
            if not meets_rate(metrics["url_complete"], metrics["products"]):
                notes.append(f"url {metrics['url_complete']}/{metrics['products']} < 90%")
            if not valid_after_errors(
                metrics["products"],
                metrics["price_order_errors"],
            ):
                notes.append(
                    f"orden_precio_valido < {MIN_SUCCESS_RATE:.0%}"
                )
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
            product_links=product_links,
            browser_session_restarts=meta.get(
                "browser_session_restarts"
            ),
        )
        summaries.append(summary)
        print(
            f"  -> {status} products={summary['products']} "
            f"links={meta.get('product_links')} "
            f"coverage={discovery_coverage if discovery_coverage is not None else 'n/d'} "
            f"price={summary['price_complete']} "
            f"units={unit_count_complete} unit_price={unit_price_complete} "
            f"single={single_item_products} sku={summary['sku_complete']} "
            f"no_box={no_box} "
            f"session_restarts={meta.get('browser_session_restarts', 0)}"
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
    category_delay_seconds: int = 0,
) -> tuple[list[pd.DataFrame], list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []

    for index, category in enumerate(categories, start=1):
        print("-" * 72)
        print(f"CHEDRAUI [{index}/{len(categories)}] {category.id}")
        print(category.url)
        scraper = ChedrauiScraper(
            # Chedraui production deliberately runs visible so the exact
            # State -> City/Zone -> Store directory path is auditable.
            headless=False,
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
            elif int(target) > 0:
                coverage = metrics["products"] / int(target)
                if coverage < 0.90:
                    notes.append(
                        f"cobertura {metrics['products']}/{target} "
                        f"({coverage:.1%}) < 90%"
                    )
            if not meets_rate(metrics["sku_complete"], metrics["products"]):
                notes.append(f"sku {metrics['sku_complete']}/{metrics['products']} < 90%")
            if not meets_rate(metrics["url_complete"], metrics["products"]):
                notes.append(f"url {metrics['url_complete']}/{metrics['products']} < 90%")
            if price_required > 0 and not meets_rate(
                price_required_complete,
                price_required,
            ):
                notes.append(
                    f"precio_requerido {price_required_complete}/{price_required} < 90%"
                )
            if not valid_after_errors(
                metrics["products"],
                metrics["price_order_errors"],
            ):
                notes.append(
                    f"orden_precio_valido < {MIN_SUCCESS_RATE:.0%}"
                )
            if not store_verified:
                notes.append("tienda no verificada")
            if meta.get("store_selection_strategy") != (
                "directory_ui_state_city_store"
            ):
                notes.append("estrategia de tienda no certificada")
            if not bool(meta.get("state_selected")):
                notes.append("estado no seleccionado")
            if not bool(meta.get("city_selected")):
                notes.append("ciudad/zona no seleccionada")
            if not bool(meta.get("store_clicked")):
                notes.append("tienda no seleccionada en directorio")
            if not bool(meta.get("pickup_confirmed")):
                notes.append("pickup no confirmado")
            if not bool(meta.get("directory_ui_store_verified")):
                notes.append("control visible de tienda no verificado")
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
            store_context_contract_version=meta.get(
                "store_context_contract_version"
            ),
            store_selection_strategy=meta.get(
                "store_selection_strategy"
            ),
            directory_state_selected=meta.get("state_selected"),
            directory_city_selected=meta.get("city_selected"),
            directory_store_clicked=meta.get("store_clicked"),
            directory_pickup_confirmed=meta.get(
                "pickup_confirmed"
            ),
            directory_ui_store_verified=meta.get(
                "directory_ui_store_verified"
            ),
            selected_state_label=meta.get(
                "selected_state_label"
            ),
            selected_city_label=meta.get(
                "selected_city_label"
            ),
            vtex_region_mutation_skipped=meta.get(
                "vtex_region_mutation_skipped"
            ),
            api_price_pages=len(meta.get("api_price_pages") or []),
            structured_price_missing_pages=structured_missing,
            pagination_gaps=pagination_gaps,
            location_id=location.id,
            store=location.store,
            store_id=location.store_id,
            city=location.city,
            state=location.state,
        )
        summaries.append(summary)
        print(
            f"  -> {status} products={summary['products']} target={target} "
            f"required_price={price_required_complete}/{price_required} "
            f"structured_missing={structured_missing} gaps={pagination_gaps}"
        )
        _release_between_cases(low_memory)
        if index < len(categories) and category_delay_seconds > 0:
            print(
                f"  pausa Chedraui: {category_delay_seconds}s "
                "antes de la siguiente categoría"
            )
            time.sleep(category_delay_seconds)

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
                "capture_date": generated_at[:10],
                "retailers": " | ".join(RETAILERS),
                "categories": int(
                    summary[["retailer", "category_id"]]
                    .drop_duplicates()
                    .shape[0]
                ),
                "execution_cases": len(summary),
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


def _create_baseline_if_missing(
    *,
    generated_at: str,
    concentrated: pd.DataFrame,
    summary: pd.DataFrame,
) -> bool:
    if BASELINE_PATH.exists():
        return False

    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OUTPUT_PATH, BASELINE_PATH)

    baseline_meta = {
        "phase": PHASE,
        "status": "VALID_BASELINE",
        "generated_at": generated_at,
        "source": str(OUTPUT_PATH),
        "baseline": str(BASELINE_PATH),
        "products": len(concentrated),
        "categories": int(
            summary[["retailer", "category_id"]]
            .drop_duplicates()
            .shape[0]
        ),
        "execution_cases": len(summary),
        "retailers": list(RETAILERS),
        "note": (
            "Primera muestra productiva valida de Fase 1. "
            "No sobrescribir en corridas futuras."
        ),
    }
    BASELINE_META_PATH.write_text(
        json.dumps(baseline_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return True


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
    parser.add_argument(
        "--chedraui-category-delay-seconds",
        type=int,
        default=15,
        help="Pausa entre categorías de una misma tienda Chedraui.",
    )
    parser.add_argument(
        "--chedraui-store-delay-seconds",
        type=int,
        default=60,
        help="Pausa entre tiendas Chedraui.",
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
    }
    chedraui_categories = load_categories(
        ROOT / "config" / "chedraui" / "categories.yaml"
    )
    chedraui_locations = [
        locations["chedraui-polanco"],
        locations["chedraui-tepeyac"],
        locations["chedraui-cancun-kabah"],
        locations["chedraui-puebla-centro-sur"],
    ]

    expected_retailer_categories = (
        len(configurations["ahorro"][0])
        + len(configurations["ibarra"][0])
        + len(chedraui_categories)
    )
    expected_cases = (
        len(configurations["ahorro"][0])
        + len(configurations["ibarra"][0])
        + len(chedraui_categories) * len(chedraui_locations)
    )

    print("=" * 72)
    print("FASE 1 - CORRIDA PRODUCTIVA")
    print("=" * 72)
    print("Retailers : Farmacias del Ahorro, Ibarra Mayoreo, Chedraui")
    print(f"Retailer-categorías únicas: {expected_retailer_categories}")
    print(f"Casos productivos         : {expected_cases}")
    print(
        "Chedraui                 : "
        f"{len(chedraui_locations)} tiendas x "
        f"{len(chedraui_categories)} categorías"
    )
    print(f"Salida    : {OUTPUT_PATH}")
    print(f"Navegador : {'visible' if args.headed else 'headless'}")
    print(f"Memoria   : {'LOW-MEMORY' if args.low_memory else 'standard'}")
    print("Publicación: sólo si TODOS los casos quedan COMPLETE")
    print("")

    existing = _load_existing_valid_results()
    all_frames: list[pd.DataFrame] = [
        item[0].copy()
        for item in existing.values()
    ]
    summaries: list[dict[str, Any]] = [
        dict(item[1])
        for item in existing.values()
    ]

    if existing:
        print(f"Casos válidos reutilizados hoy: {len(existing)}")
        print("Sólo se ejecutarán casos pendientes o fallidos.")
        print("")

    ahorro_categories, ahorro_location = configurations["ahorro"]
    pending_ahorro = [
        category
        for category in ahorro_categories
        if _case_key(
            "Farmacias del Ahorro",
            category.id,
        ) not in existing
    ]
    frames, rows = _run_ahorro(
        pending_ahorro,
        ahorro_location,
        low_memory=args.low_memory,
    )
    all_frames.extend(frames)
    summaries.extend(rows)

    ibarra_categories, ibarra_location = configurations["ibarra"]
    pending_ibarra = [
        category
        for category in ibarra_categories
        if _case_key(
            "Ibarra Mayoreo",
            category.id,
        ) not in existing
    ]
    frames, rows = _run_ibarra(
        pending_ibarra,
        ibarra_location,
        headed=args.headed,
        low_memory=args.low_memory,
    )
    all_frames.extend(frames)
    summaries.extend(rows)

    for store_index, location in enumerate(
        chedraui_locations,
        start=1,
    ):
        print("")
        print("=" * 72)
        print(
            "CHEDRAUI TIENDA "
            f"[{store_index}/{len(chedraui_locations)}] "
            f"{location.store} ({location.store_id})"
        )
        print("=" * 72)

        if location.id == "chedraui-polanco":
            profile_dir = (ROOT / args.profile_dir).resolve()
        else:
            suffix = location.id.removeprefix("chedraui-")
            profile_dir = (
                ROOT / f".chedraui_profile_{suffix}"
            ).resolve()

        pending_categories = [
            category
            for category in chedraui_categories
            if _case_key(
                "Chedraui",
                category.id,
                location.store_id,
            ) not in existing
        ]
        if not pending_categories:
            print("5/5 categorías ya válidas hoy; tienda omitida.")
            continue

        frames, rows = _run_chedraui(
            pending_categories,
            location,
            profile_dir=profile_dir,
            headed=args.headed,
            low_memory=args.low_memory,
            category_delay_seconds=(
                args.chedraui_category_delay_seconds
            ),
        )
        all_frames.extend(frames)
        summaries.extend(rows)

        if (
            store_index < len(chedraui_locations)
            and args.chedraui_store_delay_seconds > 0
        ):
            print(
                "Pausa de "
                f"{args.chedraui_store_delay_seconds}s "
                "antes de la siguiente tienda Chedraui..."
            )
            time.sleep(args.chedraui_store_delay_seconds)

    summary = pd.DataFrame(summaries)
    concentrated = (
        pd.concat(all_frames, ignore_index=True)
        if all_frames
        else pd.DataFrame(columns=COLUMNS)
    )
    concentrated = _canonical_frame(concentrated)
    if not concentrated.empty:
        concentrated = concentrated.sort_values(
            ["retailer", "store_id", "category_id", "brand", "product"],
            na_position="last",
        ).reset_index(drop=True)

    _write_progress(
        concentrated,
        summary,
        generated_at=generated_at,
    )

    DIAGNOSTIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    diagnostic_payload = {
        "phase": PHASE,
        "generated_at": generated_at,
        "output": str(OUTPUT_PATH),
        "retailer_categories_expected": expected_retailer_categories,
        "execution_cases_expected": expected_cases,
        "execution_cases_executed": len(summary),
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
        "store_id",
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

    observed_retailer_categories = (
        summary[["retailer", "category_id"]]
        .drop_duplicates()
        .shape[0]
        if not summary.empty
        else 0
    )
    publishable = (
        len(summary) == expected_cases
        and observed_retailer_categories == expected_retailer_categories
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
    PROGRESS_PATH.unlink(missing_ok=True)

    baseline_created = _create_baseline_if_missing(
        generated_at=generated_at,
        concentrated=concentrated,
        summary=summary,
    )

    print("")
    print("FASE 1 PUBLICADA CORRECTAMENTE")
    print(f"Filas productivas : {len(concentrated)}")
    print(f"Archivo productivo: {OUTPUT_PATH}")
    if baseline_created:
        print(f"Baseline Fase 1   : {BASELINE_PATH}")
        print("Baseline status   : CREADA - MUESTRA INICIAL VALIDA")
    else:
        print(f"Baseline Fase 1   : {BASELINE_PATH}")
        print("Baseline status   : CONSERVADA - NO SOBRESCRITA")
    print(f"Diagnóstico       : {DIAGNOSTIC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
