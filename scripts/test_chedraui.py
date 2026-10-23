from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from copy import copy
from datetime import datetime
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
from chedraui_case_cache import MIN_COVERAGE, persist_case, seed_from_prior_test


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
        # MVP coverage contract: >=90% (including exactly 90%),
        # with an observed positive published total and no overcount.
        and target is not None
        and int(target) > 0
        and MIN_COVERAGE <= products / int(target) <= 1.0
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


def _preserve_retry_diagnostics(
    *, category, location, attempt: int, reason: str, error: str | None,
    meta: dict, started_at: float, diagnostics_dir: Path,
) -> Path:
    """Preserve per-attempt evidence before a later attempt overwrites it."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    folder = (diagnostics_dir / "chedraui_retries" / str(location.store_id)
              / category.id)
    folder.mkdir(parents=True, exist_ok=True)
    prefix = f"{stamp}_attempt{attempt}_{reason.lower()}"
    payload = {
        "category": category.id, "store_id": str(location.store_id),
        "attempt": attempt, "reason": reason, "error": error,
        "timestamp": datetime.now().astimezone().isoformat(),
        "run_meta": meta,
    }
    destination = folder / f"{prefix}.json"
    destination.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    names = ["chedraui_run_meta.json"]
    if reason == "STORE_CONTEXT_ERROR":
        names += [
            f"chedraui_{name}.{extension}"
            for name in ("store_context_pre_category_error", "store_context_error")
            for extension in ("png", "html")
        ]
    elif reason == "CATALOG_COUNT_OVERFLOW":
        names += [
            f"chedraui_success_{category.id}.{extension}"
            for extension in ("png", "html")
        ]
    for name in names:
        source = diagnostics_dir / name
        try:
            # Do not copy stale screenshots from previous categories.
            if source.is_file() and source.stat().st_mtime >= started_at - 2:
                shutil.copy2(source, folder / f"{prefix}_{name}")
        except OSError:
            pass
    return destination


def _scrape_category_with_retries(
    category, location, *, profile_dir: str, max_pages: int,
    store_context_retries: int = 2, retry_delay_seconds: int = 10,
    scraper_factory=None, sleep_fn=None, diagnostics_dir: Path | None = None,
    validate_quality: bool = True,
):
    """Up to three attempts per category before proceeding to the next.

    Every attempt gets a new browser/context and discarded failed rows. Retry
    store errors, pagination gaps, TargetClosedError, other transient failures,
    overcounts and non-COMPLETE data quality. Never retry a BLOCKED challenge,
    which must stop automation rather than trying to bypass protection.
    """
    if scraper_factory is None:
        scraper_factory = ChedrauiScraper
    if sleep_fn is None:
        sleep_fn = time.sleep
    if diagnostics_dir is None:
        diagnostics_dir = ROOT / "diagnostics"
    if store_context_retries < 0 or store_context_retries > 2:
        raise ValueError("store_context_retries debe estar entre 0 y 2 (máximo 3 intentos)")
    if retry_delay_seconds < 0 or retry_delay_seconds > 120:
        raise ValueError("retry_delay_seconds debe estar entre 0 y 120")

    history = []
    attempts = store_context_retries + 1
    for number in range(1, attempts + 1):
        print(f"  Intento {number}/{attempts}: {category.id} / tienda {location.store_id}", flush=True)
        scraper = scraper_factory(
            headless=False, browser_channel="chrome",
            profile_dir=profile_dir, max_pages=max_pages,
        )
        started_at = time.time()
        rows = []
        error = None
        reason = None
        try:
            rows = scraper.scrape_category(category, location)
        except ChedrauiBlocked as exc:
            error = f"BLOCKED: {exc}"
        except ChedrauiStoreContextError as exc:
            error = f"STORE_CONTEXT_ERROR: {exc}"
            reason = "STORE_CONTEXT_ERROR"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            reason = "CAPTURE_ERROR"

        if not error:
            if not rows:
                reason = "EMPTY"
            else:
                # Reject overcount and missing-quality responses before saving;
                # never combine rows from separately failing browser sessions.
                observed = pd.DataFrame(rows, columns=COLUMNS).drop_duplicates(
                    subset=["sku", "url"], keep="last"
                )
                published = (scraper.run_meta or {}).get("displayed_category_products")
                try:
                    published = int(published) if published is not None else None
                except (ValueError, TypeError):
                    published = None
                if published and len(observed) > published:
                    reason = "CATALOG_COUNT_OVERFLOW"
                    print(f"  Conteo inconsistente: {len(observed)}/{published} (>100%)", flush=True)
                elif validate_quality:
                    quality, indicators = _quality(observed, scraper.run_meta or {})
                    if quality != "COMPLETE":
                        reason = "QUALITY_PARTIAL"
                        print(
                            "  Calidad pendiente: "
                            f"filas={len(observed)} target={indicators['target_products']} "
                            f"gaps={indicators['pagination_gaps']} "
                            f"structured_missing={indicators['structured_price_missing_pages']}",
                            flush=True,
                        )

        if error:
            print(f"  {error}", flush=True)

        blocked = bool(error and error.startswith("BLOCKED:"))
        entry = {
            "attempt": number,
            "status": reason or (error or "SCRAPED"),
            "error": error,
            "rows": len(rows),
            "diagnostic": None,
        }
        if reason:
            try:
                diagnostic = _preserve_retry_diagnostics(
                    category=category, location=location,
                    attempt=number, reason=reason, error=error,
                    meta=dict(scraper.run_meta or {}),
                    started_at=started_at, diagnostics_dir=diagnostics_dir,
                )
                entry["diagnostic"] = str(diagnostic)
                print(f"  Diagnóstico conservado: {diagnostic}", flush=True)
            except OSError as exc:
                print(f"  ADVERTENCIA: no se pudo conservar el diagnóstico: {exc}", flush=True)
        history.append(entry)

        if not reason and not error:
            if number > 1:
                print(f"  Categoría recuperada en intento {number}/{attempts}", flush=True)
            return scraper, rows, None, history
        if blocked:
            print("  BLOQUEADO: no se reintentará para respetar las protecciones del sitio.", flush=True)
            return scraper, [], error, history
        if number < attempts:
            # An unsuccessful category stays in place until its bounded retry
            # budget is exhausted. The worker's Stop terminates this process.
            print(
                f"  Falló la categoría; reintento {number + 1}/{attempts} "
                f"en {retry_delay_seconds}s antes de pasar a otra categoría.",
                flush=True,
            )
            sleep_fn(retry_delay_seconds)
            continue
        failure = error or f"{reason}: no se consiguió una captura COMPLETE"
        print(f"  Se agotaron {attempts} intentos; categoría pendiente. Continúa la siguiente.", flush=True)
        return scraper, [], failure, history

    raise AssertionError("El bucle de reintentos debe regresar en el último intento")

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
    parser.add_argument(
        "--store-context-retries",
        type=int,
        choices=range(0, 3),
        default=2,
        metavar="{0..2}",
        help="Reintentos adicionales por categoría ante errores de captura/calidad (default: 2; 3 intentos totales).",
    )
    parser.add_argument(
        "--store-context-retry-delay-seconds",
        type=int,
        choices=range(0, 121),
        default=10,
        metavar="{0..120}",
        help="Segundos entre reintentos de la misma categoría (default: 10).",
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

    # Isolated test workbooks are mutable. Archive the previous capture
    # before a new test can replace it with EMPTY/STORE_CONTEXT_ERROR.
    if output_path.exists():
        checkpoint_dir = ROOT / "output" / "checkpoints_validos"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        previous_capture = checkpoint_dir / (
            f"before_chedraui_{args.store.removeprefix('chedraui-')}_"
            f"{stamp}.xlsx"
        )
        shutil.copy2(output_path, previous_capture)
        print(f"Respaldo de captura anterior: {previous_capture}")

    today = datetime.now().astimezone().date().isoformat()
    recovered = seed_from_prior_test(
        ROOT, output_path, store_id=str(location.store_id), day=today
    )
    for archived in recovered:
        print(f"Caso COMPLETE anterior recuperado: {archived}")

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
    print(f"Reintentos : {args.store_context_retries} por categoría")
    print(f"Delay retr.: {args.store_context_retry_delay_seconds} segundos")
    print("Consolidado: sin cambios")
    print("")

    for index, category in enumerate(categories, start=1):
        print("-" * 72)
        print(f"[{index}/{len(categories)}] {category.id}")
        print(category.url)
        print("-" * 72)

        scraper, rows, error, retry_history = _scrape_category_with_retries(
            category, location, profile_dir=profile_dir,
            max_pages=args.max_pages,
            store_context_retries=args.store_context_retries,
            retry_delay_seconds=args.store_context_retry_delay_seconds,
        )

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
            "attempts": len(retry_history),
            "retry_history": json.dumps(retry_history, ensure_ascii=False),
        }
        summaries.append(summary)
        if status == "COMPLETE":
            try:
                validated_checkpoint = persist_case(
                    ROOT, frame, summary,
                    store_id=str(location.store_id), day=today,
                )
                print(f"Checkpoint validado independiente: {validated_checkpoint}")
            except ValueError as exc:
                status = "PARTIAL"
                summary["status"] = "PARTIAL"
                summary["error"] = str(exc)
                print(f"ADVERTENCIA: {exc}")
        _write_output(all_rows, summaries, output_path)

        print("RESULTADO")
        for key in (
            "status",
            "products",
            "target_products",
            "coverage",
            "attempts",
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
                "location_button_matches",
                "location_button_selector",
                "location_locator_fallback_clicked",
                "location_locator_final_url",
                "pickup_clicked",
                "directory_clicked",
                "directory_open_method",
                "directory_final_selects_visible",
                "state_selected",
                "selected_state_label",
                "city_selected",
                "selected_city_label",
                "store_clicked",
                "directory_store_match_method",
                "directory_store_expected_pickup_value",
                "directory_store_radio_matches",
                "directory_store_radio_verified",
                "pickup_confirmation_expected_radio",
                "pickup_confirmation_method",
                "pickup_confirmed",
                "directory_header_matched_after_confirmation",
                "visible_store_control_text",
                "directory_selection_failure",
                "directory_ui_steps",
            ):
                print(f"{key:42}: {scraper.run_meta.get(key)}")
            locator_checks = scraper.run_meta.get(
                "location_locator_attempts"
            ) or []
            if locator_checks:
                print("ULTIMOS INTENTOS DE ABRIR UBICACION")
                for item in locator_checks[-2:]:
                    print(f"  {item}")
            directory_checks = scraper.run_meta.get(
                "directory_open_observations"
            ) or []
            if directory_checks:
                print("ULTIMOS INTENTOS DE ABRIR DIRECTORIO")
                for item in directory_checks[-4:]:
                    print(f"  {item}")
            confirmation_checks = scraper.run_meta.get(
                "pickup_confirmation_observations"
            ) or []
            if confirmation_checks:
                print("ULTIMOS INTENTOS DE CONFIRMAR RECOLECCION")
                for item in confirmation_checks[-4:]:
                    print(f"  {item}")
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

        if status == "BLOCKED":
            print("Se detiene esta tienda por protección de acceso; no se intentarán más categorías.", flush=True)
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
