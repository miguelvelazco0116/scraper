"""Soriana CLI pilot: 4 independently verified postal-code capture contexts.

Does not alter the productive Phase-2 workbook, master, pointer or dashboard.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / "scripts"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import pandas as pd
import yaml
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

from main import COLUMNS
from scraper.availability import AVAILABLE, UNAVAILABLE, UNKNOWN
from scraper.capture_progress import CaptureProgress
from scraper.config import load_categories, load_locations
from scraper.quality_rules import meets_rate
from scraper.retailers.soriana import PRODUCT_SELECTOR, SorianaBlocked, SorianaScraper
from scraper.retailers.soriana_postal_context import (
    SorianaPostalContextError, attempt_postal_selection,
    check_postal_code, require_postal_context, postal_ui_diagnostics,
)
from test_soriana_manual_phase2 import (
    find_soriana_page, normalize_frame, target_count
)

CONFIG = ROOT / "config" / "soriana" / "postal_rotation.yaml"
OUTPUT_DIR = ROOT / "output" / "pilots" / "soriana_cp_rotation"
DIAG = ROOT / "diagnostics" / "soriana_cp_rotation"
MAX_ATTEMPTS = 3


def configured_postcodes() -> list[str]:
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    codes = [check_postal_code(cp) for cp in data["postal_codes"]]
    if len(codes) != 4 or len(set(codes)) != 4:
        raise ValueError("La rotación Soriana exige cuatro CP únicos.")
    return codes


def selected_categories(target: str):
    regular = load_categories(ROOT / "config/soriana/categories.yaml")
    if target == "all":
        return regular
    if target == "gastrointestinales":
        categories = load_categories(
            ROOT / "config/soriana/categories.yaml",
            include_disabled=True,
        )
    else:
        categories = regular
    return [cat for cat in categories if cat.id == target]


def classify(rows: list[dict], *, cp: str, category_id: str,
             target: int | None, page_meta: dict,
             proof: str | None, error: str | None, attempts: int) -> dict:
    frame = normalize_frame(rows)
    count = len(frame)
    counts = frame.get("availability_status", pd.Series(dtype=str))
    available = int(counts.eq(AVAILABLE).sum())
    unavailable = int(counts.eq(UNAVAILABLE).sum())
    unknown = int(counts.fillna(UNKNOWN).eq(UNKNOWN).sum())
    sku = int(frame["sku"].fillna("").astype(str).str.strip().ne("").sum())
    price = int(pd.to_numeric(frame["price_current"], errors="coerce").gt(0).sum())
    url = int(frame["url"].fillna("").astype(str).str.strip().ne("").sum())
    notes: list[str] = []
    if error:
        notes.append(error)
    if not proof:
        notes.append("CP sin verificación persistente visible")
    if not count:
        notes.append("sin productos")
    if target is None or target <= 0:
        notes.append("conteo objetivo de categoría no disponible")
    elif not 0.90 <= count / target <= 1.0:
        notes.append(f"cobertura {count}/{target} fuera de 90%-100%")
    if not page_meta.get("pagination_complete"):
        notes.append("paginación incompleta o no verificada")
    for name, valid in (
        ("SKU", sku), ("precio", price), ("URL", url),
        ("disponibilidad online", available + unavailable),
    ):
        if not meets_rate(valid, count):
            notes.append(f"{name} {valid}/{count} debajo de 90%")
    if available + unavailable + unknown != count:
        notes.append("clasificación de disponibilidad inconsistente")
    if count and (
        not frame["postal_code"].astype(str).eq(cp).all()
        or frame["store_id"].notna().any()
    ):
        notes.append("filas no corresponden al CP o tienda no certificada")
    return {
        "retailer": "Soriana", "category_id": category_id,
        "postal_code": cp, "capture_date":
            datetime.now().astimezone().date().isoformat(),
        "status": "COMPLETE" if not notes else "PARTIAL",
        "products": count, "target_products": target,
        "coverage": count / target if target else None,
        "total_pages": page_meta.get("pages_expected"),
        "pages_visited": page_meta.get("pages_visited"),
        "pagination_complete": bool(page_meta.get("pagination_complete")),
        "postal_context_verified": bool(proof),
        "postal_context_evidence": proof,
        "store_context_verified": False,
        "store_context_method": "soriana_postal_verified_without_store_id",
        "available_online": available,
        "unavailable_online": unavailable,
        "availability_unknown": unknown,
        "availability_complete": available + unavailable,
        "sku_complete": sku, "price_complete": price,
        "url_complete": url, "attempts": attempts,
        "quality_notes": "; ".join(notes),
    }


def _valid_cache(frame: pd.DataFrame, item: dict, cp: str, category: str, day: str) -> bool:
    if (str(item.get("category_id")) != category or
        str(item.get("postal_code")) != cp or
        str(item.get("capture_date")) != day or
        str(item.get("status")) != "COMPLETE" or
        str(item.get("postal_context_verified")).lower() not in ("true", "1") or
        not str(item.get("postal_context_evidence") or "").strip()):
        return False
    rows = frame.loc[frame["category_id"].astype(str).eq(category)]
    if rows.empty or len(rows) != int(item.get("products") or 0):
        return False
    if not rows["postal_code"].astype(str).eq(cp).all():
        return False
    if not rows["scrape_timestamp"].astype(str).str.startswith(day).all():
        return False
    return True


def load_cache(cp: str, day: str) -> dict[str, tuple[pd.DataFrame, dict]]:
    output = OUTPUT_DIR / f"soriana_{cp}.xlsx"
    if not output.is_file():
        return {}
    try:
        frame = pd.read_excel(
            output, sheet_name="Concentrado",
            dtype={"sku": str, "postal_code": str, "category_id": str}
        )
        summary = pd.read_excel(
            output, sheet_name="Resumen",
            dtype={"postal_code": str, "category_id": str}
        )
    except (OSError, ValueError, KeyError, ImportError):
        return {}
    if frame.empty or summary.empty:
        return {}
    return {
        str(row["category_id"]): (
            frame.loc[frame["category_id"].astype(str).eq(
                str(row["category_id"])
            )].copy(), row
        )
        for row in summary.to_dict("records")
        if _valid_cache(
            frame, row, cp, str(row.get("category_id") or ""), day
        )
    }


def save_cp(cp: str, items: dict[str, tuple[pd.DataFrame, dict]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DIAG.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / f"soriana_{cp}.xlsx"
    if output.is_file():
        shutil.copy2(
            output, DIAG / (
                f"before_soriana_{cp}_"
                + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".xlsx"
            )
        )
    frame = pd.concat(
        [v[0] for v in items.values()], ignore_index=True
    ) if items else pd.DataFrame(columns=COLUMNS)
    summary = pd.DataFrame([v[1] for v in items.values()])
    temp = output.with_name("." + output.stem + "." + uuid.uuid4().hex + ".xlsx")
    try:
        with pd.ExcelWriter(temp, engine="openpyxl") as writer:
            frame.reindex(columns=COLUMNS).to_excel(
                writer, sheet_name="Concentrado", index=False
            )
            summary.to_excel(writer, sheet_name="Resumen", index=False)
        for retry in range(8):
            try:
                os.replace(temp, output)
                break
            except PermissionError:
                if retry >= 7:
                    raise
                time.sleep(min(0.2 * 2 ** retry, 1.0))
    finally:
        temp.unlink(missing_ok=True)



class SorianaCatalogueNotRendered(RuntimeError):
    """The delivery CP was verified but category cards did not load."""


def prepare_postal_category(page, category, scraper, cp: str) -> str:
    """Apply CP before waiting for product tiles, then refresh category data.

    This recovery order also works if the prior CP left an empty category
    grid: never block at wait_for_selector before changing the delivery CP.
    """
    scraper._assert_not_blocked(page)
    proof = attempt_postal_selection(page, cp)
    if not proof:
        raise SorianaPostalContextError(
            f"CP {cp} no aplicado/confirmado en Soriana."
        )

    # Re-enter the category after changing delivery location. The site may
    # show a stale or unrendered grid until it fetches the new assortment.
    for reload_number in (1, 2):
        page.goto(category.url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1200)
        scraper._assert_not_blocked(page)
        proof = require_postal_context(page, cp)
        try:
            page.wait_for_selector(
                PRODUCT_SELECTOR, state="attached", timeout=15000
            )
            scraper._assert_not_blocked(page)
            if page.locator(PRODUCT_SELECTOR).count() > 0:
                return proof
        except PlaywrightTimeoutError:
            pass
        if reload_number == 1:
            print(
                f"CP {cp} confirmado pero catálogo sin tarjetas; "
                "reintentando la carga con el mismo CP.",
                flush=True,
            )
    raise SorianaCatalogueNotRendered(
        f"CP {cp} aplicado, pero Soriana no mostró tarjetas "
        f"{PRODUCT_SELECTOR} tras dos cargas de la categoría."
    )


def record_ui_diagnostics(page, scraper, cp: str, category_id: str, attempt: int) -> None:
    """Save the visible CP and product-grid state for failed attempts."""
    folder = DIAG / cp / category_id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        observed = postal_ui_diagnostics(page, cp)
        observed["product_tiles"] = page.locator(PRODUCT_SELECTOR).count()
        observed["category_id"] = category_id
        observed["attempt"] = attempt
        print("DIAGNÓSTICO CP:", json.dumps(observed, ensure_ascii=False))
        (folder / f"cp_attempt_{attempt}.json").write_text(
            json.dumps(observed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"Diagnóstico CP no disponible: {exc}", flush=True)
    try:
        scraper._save_diagnostics(
            page, f"failed_{cp}_{category_id}_{attempt}"
        )
    except Exception as exc:
        print(f"Diagnóstico visual no disponible: {exc}", flush=True)

def capture_case(page, category, location, cp: str, max_pages: int, on_attempt=None):
    """Retry up to 3 times within the same CP/category; report each attempt.

    The optional callback is used by the shared CLI progress contract.
    It never changes the product schema or touches the master.
    """
    last = ([], None)
    history = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"  Intento {attempt}/{MAX_ATTEMPTS}: {category.id} / CP {cp}", flush=True)
        if on_attempt is not None:
            on_attempt("start", attempt, None)
        rows: list[dict] = []
        target = None
        proof = None
        page_meta: dict = {}
        error = None
        blocked = False
        scraper = SorianaScraper(
            headless=False,
            diagnostics_dir=DIAG / cp / category.id,
            max_load_more=max_pages, wait_ms=1200,
        )
        try:
            # Selection and confirmation precede the first product wait.
            # The prior CP may have left this page with no product tiles.
            proof = prepare_postal_category(page, category, scraper, cp)
            target = target_count(page)
            scraper.page_validator = lambda pag: require_postal_context(pag, cp)
            rows = scraper._collect_pages(page, category, location)
            page_meta = dict(scraper.last_page_meta)
            for row in rows:
                row.update({
                    "postal_code": cp, "city": None, "state": None,
                    "store": None, "store_id": None,
                    "store_context_verified": False,
                    "store_context_method":
                        "soriana_postal_verified_without_store_id",
                })
            scraper._save_diagnostics(
                page, f"postal_{cp}_{category.id}_{attempt}"
            )
        except SorianaBlocked as exc:
            error = "BLOCKED: " + str(exc)
            blocked = True
        except SorianaPostalContextError as exc:
            error = str(exc)
            rows = []
            proof = None
        except SorianaCatalogueNotRendered as exc:
            # Preserve true postal evidence: verified CP != loaded products.
            error = str(exc)
            rows = []
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        if error:
            record_ui_diagnostics(page, scraper, cp, category.id, attempt)
        frame = normalize_frame(rows)
        summary = classify(
            frame.to_dict("records"), cp=cp, category_id=category.id,
            target=target, page_meta=page_meta, proof=proof,
            error=error, attempts=attempt,
        )
        attempt_status = "BLOCKED" if blocked else summary["status"]
        history.append({
            "attempt": attempt, "status": attempt_status,
            "rows": int(summary["products"]), "error": error,
            "quality_notes": summary["quality_notes"],
            "diagnostic": (
                str(DIAG / cp / category.id / f"cp_attempt_{attempt}.json")
                if error else None
            ),
        })
        summary["retry_history"] = json.dumps(history, ensure_ascii=False)
        if on_attempt is not None:
            on_attempt("finish", attempt, summary)
        print(
            f"  {attempt_status}: {summary['products']} productos, "
            f"{summary['available_online']} disponibles, "
            f"{summary['unavailable_online']} agotados, "
            f"{summary['availability_unknown']} UNKNOWN; "
            f"CP verificado={summary['postal_context_verified']}",
            flush=True,
        )
        if summary["quality_notes"]:
            print("  Observaciones: " + summary["quality_notes"], flush=True)
        last = (frame.to_dict("records"), summary)
        if summary["status"] == "COMPLETE":
            if attempt > 1:
                print(f"  Categoria recuperada en intento {attempt}/{MAX_ATTEMPTS}", flush=True)
            break
        if blocked:
            print("  BLOQUEADO: no se reintentara para respetar las protecciones del sitio.", flush=True)
            break
        if attempt < MAX_ATTEMPTS:
            print(
                f"  Reintento {attempt + 1}/{MAX_ATTEMPTS} sobre la MISMA categoria "
                f"y CP {cp} antes de avanzar.", flush=True,
            )
            page.wait_for_timeout(1500)
        else:
            print(
                f"  Agotados {MAX_ATTEMPTS} intentos; caso REINTENTAR. "
                "Se continua con el siguiente.", flush=True,
            )
    return last


def main() -> int:
    parser = argparse.ArgumentParser(description="Soriana CP 4 zonas, no master")
    parser.add_argument("--cdp-url", default="")
    parser.add_argument("--dry-run", action="store_true", help="Mostrar COMPLETE/REINTENTAR sin navegador")
    parser.add_argument("--category", default="cuidado-bucal")
    parser.add_argument("--cp", default="all")
    parser.add_argument("--max-pages", type=int, default=100)
    args = parser.parse_args()
    codes = configured_postcodes()
    if args.cp != "all":
        if check_postal_code(args.cp) not in codes:
            parser.error("CP fuera de la rotación configurada.")
        codes = [args.cp]
    cats = selected_categories(args.category)
    if not cats:
        parser.error("Categoría Soriana inexistente.")
    if not 1 <= args.max_pages <= 200:
        parser.error("max-pages fuera de 1..200")
    day = datetime.now().astimezone().date().isoformat()
    base = next(
        loc for loc in load_locations(ROOT / "config/locations.yaml")
        if loc.id == "cdmx"
    )
    results = {}
    # Same per-case vocabulary as Chedraui: COMPLETE vs REINTENTAR;
    # record each of the three attempts and persist read-only progress JSON.
    scope = f"{args.cp}_{args.category}"
    progress = CaptureProgress(
        path=DIAG / f"progress_{day}_{scope}.json",
        retailer="Soriana", capture_date=day,
        cases=[(cp, cat.id) for cp in codes for cat in cats],
    )
    cached_by_cp = {cp: load_cache(cp, day) for cp in codes}
    for cp in codes:
        for cat in cats:
            if cat.id in cached_by_cp[cp]:
                progress.reuse(
                    cp, cat.id,
                    products=len(cached_by_cp[cp][cat.id][0]),
                )
    progress.save()
    print(
        f"SORIANA INCREMENTAL - {day} | casos {len(progress.cases)} | "
        f"COMPLETE reutilizados {progress.snapshot()['complete_cases']} | "
        f"REINTENTAR {progress.snapshot()['pending_cases']}", flush=True,
    )
    print(f"Progreso JSON: {progress.path}", flush=True)
    print("Master productivo: SIN CAMBIOS", flush=True)
    if args.dry_run:
        print("INVENTARIO DE CASOS (sin navegar):", flush=True)
        for case in progress.snapshot()["cases"]:
            print(
                f"  CP {case['postal_code']} | {case['category_id']:<30} | "
                f"{case['estado']:<9} | {case['accion']:<10} | "
                f"{case['productos_validados']} productos", flush=True,
            )
        return 0
    if not args.cdp_url:
        parser.error("--cdp-url es obligatorio al capturar. Abre Chrome o usa --dry-run.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        page = find_soriana_page(browser)
        if page is None:
            print("Abre una pestaña de www.soriana.com en Chrome.")
            return 11
        for cp in codes:
            if datetime.now().astimezone().date().isoformat() != day:
                print("FECHA CAMBIO: se detiene para no mezclar capturas.", flush=True)
                return 3
            existing = cached_by_cp[cp]
            # Preserve every previously COMPLETE category for this CP/day,
            # including categories outside this CLI request. In particular,
            # running Gastrointestinales after the six regular categories
            # must not overwrite their rows in the per-CP workbook.
            items = dict(existing)
            for cat in cats:
                key = cat.id
                if datetime.now().astimezone().date().isoformat() != day:
                    print("FECHA CAMBIO: se detiene para no mezclar capturas.", flush=True)
                    return 3
                state = progress.snapshot()
                print(
                    f"AVANCE {state['complete_cases']}/{state['total_cases']} COMPLETE | "
                    f"pendientes {state['pending_cases']} | "
                    f"validos {state['products_validated']} | "
                    f"actual CP={cp} categoria={key}", flush=True,
                )
                if key in existing:
                    print(f"REUTILIZADO CP {cp}/{key}: checkpoint certificado del dia", flush=True)
                    continue

                def report_attempt(event, attempt, summary, _cp=cp, _key=key):
                    if event == "start":
                        progress.start(_cp, _key, attempt=attempt)
                        return
                    progress.finish_attempt(
                        _cp, _key, attempt=attempt,
                        status=(
                            "BLOCKED" if "BLOCKED:" in (summary.get("quality_notes") or "")
                            else summary["status"]
                        ),
                        products=int(summary["products"]),
                        quality_notes=summary.get("quality_notes") or "",
                        error=next(
                            (x.get("error") for x in json.loads(summary["retry_history"])[-1:]
                             if x.get("error")), None
                        ),
                        diagnostic=next(
                            (x.get("diagnostic") for x in json.loads(summary["retry_history"])[-1:]
                             if x.get("diagnostic")), None
                        ),
                    )

                rows, summary = capture_case(
                    page, cat, base, cp, args.max_pages, on_attempt=report_attempt,
                )
                items[key] = (pd.DataFrame(rows, columns=COLUMNS), summary)
                save_cp(cp, items)
                if summary["status"] == "COMPLETE":
                    persisted = load_cache(cp, day)
                    if key not in persisted:
                        raise ValueError(
                            f"El checkpoint {cp}/{key} no supero la "
                            "revalidacion despues de guardarse."
                        )
                    progress.reuse(
                        cp, key, products=len(persisted[key][0]),
                    )
                state = progress.snapshot()
                print(
                    f"RESULTADO {cp}/{key}={summary['status']} "
                    f"| avance {state['complete_cases']}/{state['total_cases']} "
                    f"| reintentar {state['pending_cases']}", flush=True,
                )
            # Only the requested categories determine this run's status:
            # preserved data from earlier category-specific runs cannot
            # mask an unfinished requested case.
            results[cp] = [
                items[cat.id][1] for cat in cats if cat.id in items
            ]

    DIAG.mkdir(parents=True, exist_ok=True)
    status = all(
        row.get("status") == "COMPLETE"
        for entries in results.values() for row in entries
    ) and all(len(entries) == len(cats) for entries in results.values())
    report = DIAG / f"rotation_{day}.json"
    report.write_text(
        json.dumps({
            "capture_date": day,
            "postcodes": codes, "categories": [x.id for x in cats],
            "status": "COMPLETE" if status else "PARTIAL",
            "cases": results, "master_modified": False,
            "progress_file": str(progress.path),
            "progress_contract": progress.snapshot()["schema_version"],
            "complete_cases": progress.snapshot()["complete_cases"],
            "total_cases": progress.snapshot()["total_cases"],
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
    )
    print("Reporte:", report)
    print("Archivos:", OUTPUT_DIR)
    print("PROGRESO FINAL:", json.dumps({
        key: progress.snapshot()[key] for key in (
            "complete_cases", "total_cases", "pending_cases",
            "attempts_total", "products_validated"
        )
    }, ensure_ascii=False), flush=True)
    print("Detalle de reintentos:", progress.path)
    print("MASTER PRODUCTIVO: SIN CAMBIOS")
    return 0 if status else 2


if __name__ == "__main__":
    raise SystemExit(main())
