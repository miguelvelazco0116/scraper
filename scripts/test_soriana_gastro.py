"""Pilot CLI for Soriana Gastrointestinales + online availability.

Isolated output: never modifies Soriana phase-2 production Excel or master.
Uses operator's Chrome/CDP session; three attempts at most in this category.
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from playwright.sync_api import sync_playwright

from scraper.availability import AVAILABLE, UNAVAILABLE, UNKNOWN
from scraper.config import load_categories, load_locations
from scraper.retailers.soriana import SorianaBlocked, SorianaScraper
from scraper.quality_rules import meets_rate
from test_soriana_manual_phase2 import (
    find_soriana_page, navigate_same_manual_session, normalize_frame,
    target_count,
)

CATEGORY_ID = "gastrointestinales"
OUTPUT = ROOT / "output" / "soriana_gastrointestinales_pilot.xlsx"
DIAG = ROOT / "diagnostics" / "soriana_gastrointestinales"
REPORT = DIAG / "last_run.json"
MAX_ATTEMPTS = 3


def load_pilot_category():
    """Pilot is in category config but intentionally disabled for phase 2."""
    categories = load_categories(
        ROOT / "config" / "soriana" / "categories.yaml",
        include_disabled=True,
    )
    matches = [category for category in categories if category.id == CATEGORY_ID]
    if len(matches) != 1:
        raise ValueError("La categoría Gastrointestinales no está configurada")
    category = matches[0]
    if not category.url.startswith(
        "https://www.soriana.com/farmacia/gastrointenstinales/"
    ):
        raise ValueError("URL de Gastrointestinales distinta de la configurada")
    return category


def summarize(frame: pd.DataFrame, *, target: int | None,
              page_meta: dict, attempt: int, error: str | None = None) -> dict:
    total = len(frame)
    observed = int(frame["availability_status"].isin([AVAILABLE, UNAVAILABLE]).sum())
    available = int(frame["availability_status"].eq(AVAILABLE).sum())
    unavailable = int(frame["availability_status"].eq(UNAVAILABLE).sum())
    unknown = int(frame["availability_status"].eq(UNKNOWN).sum())
    sku = int(frame["sku"].fillna("").astype(str).str.strip().ne("").sum())
    url = int(frame["url"].fillna("").astype(str).str.strip().ne("").sum())
    prices = int(pd.to_numeric(frame["price_current"], errors="coerce").gt(0).sum())
    notes = []
    if error:
        notes.append(error)
    if total <= 0:
        notes.append("no se detectaron productos")
    if target is None or target <= 0:
        notes.append("total objetivo no visible en el catálogo")
    elif not meets_rate(total, target):
        notes.append(f"cobertura insuficiente: {total}/{target}")
    if not page_meta.get("pagination_complete"):
        notes.append(
            f"paginación incompleta: {page_meta.get('pages_visited', 0)}/"
            f"{page_meta.get('pages_expected', 0)}"
        )
    for label, count in (("SKU", sku), ("precios", prices), ("URL", url),
                         ("disponibilidad online", observed)):
        if not meets_rate(count, total):
            notes.append(f"{label} incompleto: {count}/{total}")
    if available + unavailable + unknown != total:
        notes.append("conteos de disponibilidad inconsistentes")
    return {
        "retailer": "Soriana",
        "category_id": CATEGORY_ID,
        "source_phase": "PILOTO",
        "status": "COMPLETE" if not notes else "PARTIAL",
        "capture_date": datetime.now().astimezone().date().isoformat(),
        "products": total,
        "target_products": target,
        "coverage": total / target if target else None,
        "total_pages": page_meta.get("pages_expected"),
        "pages_visited": page_meta.get("pages_visited"),
        "pagination_complete": bool(page_meta.get("pagination_complete")),
        "sku_complete": sku,
        "url_complete": url,
        "price_complete": prices,
        "availability_complete": observed,
        "available_online": available,
        "unavailable_online": unavailable,
        "availability_unknown": unknown,
        "availability_scope": "ONLINE_STORE_SESSION_ONLY",
        "attempts": attempt,
        "quality_notes": "; ".join(notes),
    }


def save_report(frame: pd.DataFrame, summary: dict) -> None:
    DIAG.mkdir(parents=True, exist_ok=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # Preserve the prior pilot workbook; never overwrite master/phase 2 inputs.
    if OUTPUT.is_file():
        backup = DIAG / (
            "before_gastro_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            + ".xlsx"
        )
        shutil.copy2(OUTPUT, backup)
    temp = OUTPUT.with_name("." + OUTPUT.stem + "." + uuid.uuid4().hex + ".xlsx")
    try:
        with pd.ExcelWriter(temp, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name="Concentrado", index=False)
            pd.DataFrame([summary]).to_excel(
                writer, sheet_name="Resumen", index=False
            )
        for retry in range(8):
            try:
                os.replace(temp, OUTPUT)
                break
            except PermissionError:
                if retry == 7:
                    raise
                time.sleep(min(0.1 * (2**retry), 0.8))
    finally:
        temp.unlink(missing_ok=True)
    REPORT.write_text(
        json.dumps(
            {**summary, "output": str(OUTPUT), "master_modified": False},
            ensure_ascii=False, indent=2, default=str,
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CLI Soriana Gastrointestinales + disponibilidad online"
    )
    parser.add_argument("--cdp-url", required=True)
    parser.add_argument("--max-pages", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.max_pages <= 200:
        parser.error("--max-pages debe estar entre 1 y 200")

    category = load_pilot_category()
    location = next(
        loc for loc in load_locations(ROOT / "config" / "locations.yaml")
        if loc.id == "cdmx"
    )
    print("SORIANA · GASTROINTESTINALES · DISPONIBILIDAD ONLINE")
    print("URL: " + category.url)
    print("Reglas: Agregar + = AVAILABLE / Agotado en línea = UNAVAILABLE")
    print("Salida separada: " + str(OUTPUT))
    print("MASTER PRODUCTIVO: SIN CAMBIOS", flush=True)

    DIAG.mkdir(parents=True, exist_ok=True)
    last_frame = normalize_frame([])
    last_summary = None

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        except Exception as exc:
            print("No fue posible conectar a Chrome CDP: " + str(exc))
            return 10
        page = find_soriana_page(browser)
        if page is None:
            print("Abre una pestaña de Soriana en Chrome CDP.")
            return 11

        scraper = SorianaScraper(
            headless=False, diagnostics_dir=DIAG,
            max_load_more=args.max_pages, wait_ms=1200,
        )
        page.on("response", scraper._capture_grid_response)

        for attempt in range(1, MAX_ATTEMPTS + 1):
            print(f"Intento {attempt}/{MAX_ATTEMPTS} de {CATEGORY_ID}", flush=True)
            target = None
            page_meta: dict = {}
            errors = None
            rows = []
            blocked = False
            try:
                navigate_same_manual_session(page, category, scraper)
                target = target_count(page)
                rows = scraper._collect_pages(page, category, location)
                page_meta = dict(scraper.last_page_meta)
                scraper._save_diagnostics(
                    page, f"gastrointestinales_attempt_{attempt}"
                )
            except SorianaBlocked as exc:
                errors = "BLOCKED: " + str(exc)
                blocked = True
            except Exception as exc:
                errors = f"{type(exc).__name__}: {exc}"

            frame = normalize_frame(rows)
            summary = summarize(
                frame, target=target,
                page_meta=page_meta, attempt=attempt, error=errors,
            )
            last_frame, last_summary = frame, summary
            print(
                f"status={summary['status']} products={summary['products']} "
                f"target={summary['target_products']} "
                f"pages={summary['pages_visited']}/{summary['total_pages']} "
                f"AVAILABLE={summary['available_online']} "
                f"UNAVAILABLE={summary['unavailable_online']} "
                f"UNKNOWN={summary['availability_unknown']}", flush=True
            )
            if summary["quality_notes"]:
                print("Observaciones: " + summary["quality_notes"])
            if summary["status"] == "COMPLETE" or blocked:
                break
            if attempt < MAX_ATTEMPTS:
                page.wait_for_timeout(1500)

    assert last_summary is not None
    save_report(last_frame, last_summary)
    print("Reporte: " + str(REPORT))
    print("Excel: " + str(OUTPUT))
    print("MASTER: SIN CAMBIOS")
    return 0 if last_summary["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
