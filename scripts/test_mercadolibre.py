"""Phase 7 standalone Mercado Libre Mexico capture. CLI only; no GUI.

Writes isolated pilot outputs, never the productive master/current pointer.
Publication IDs and catalog IDs are NOT assigned to the SKU field.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS
from scraper.retailers.mercadolibre import run_search

PILOT_ROOT = ROOT / "output" / "pilots" / "mercadolibre"
REPORT_ROOT = ROOT / "diagnostics" / "mercadolibre"


def query_slug(query: str) -> str:
    normalized = unicodedata.normalize("NFKD", query)
    ascii_query = normalized.encode("ascii", "ignore").decode("ascii").lower()
    readable = re.sub(r"[^a-z0-9]+", "-", ascii_query).strip("-")[:48]
    suffix = hashlib.sha256(query.casefold().encode()).hexdigest()[:8]
    return (readable or "busqueda") + "-" + suffix


def build_frames(query: str, listings: list[dict], metadata: dict, date: str):
    """Match main.COLUMNS but preserve marketplace-specific IDs separately."""
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    category_id = "mercadolibre-search-" + query_slug(query)
    rows = []
    audit = []
    for index, item in enumerate(listings, start=1):
        row = {col: None for col in COLUMNS}
        row.update({
            "scrape_timestamp": timestamp,
            "retailer": "Mercado Libre México",
            "city": "Catálogo online", "state": None,
            "postal_code": None, "store": None, "store_id": None,
            "department": "Marketplace",
            "category": "Resultados de búsqueda",
            "subcategory": query,
            "category_id": category_id,
            "sku": None,  # Marketplace publication ID is NOT product SKU.
            "brand": None,
            "product": item["product"],
            "price_current": item["price_current"],
            "price_regular": item["price_regular"],
            "promotion": item["promotion"],
            "availability_status": "UNKNOWN",
            "is_available": None,
            "availability_raw": None,
            "store_context_verified": False,
            "store_context_method": "PUBLIC_ONLINE_SEARCH_NO_LOCATION_VERIFICATION",
            "url": item["url"], "price_raw": None,
        })
        rows.append(row)
        audit.append({
            "capture_date": date,
            "category_id": category_id,
            "search_query": query,
            "search_rank": index,
            "publication_id": item["publication_id"],
            "catalog_product_id": item["catalog_product_id"],
            "sku_verified": False,
            "seller": item["seller"],
            "sponsored": item["sponsored"],
            "currency": item["currency"],
            "url": item["url"],
            "price_current": item["price_current"],
            "price_regular": item["price_regular"],
            "promotion": item["promotion"],
        })
    frame = pd.DataFrame(rows, columns=COLUMNS)
    audit_frame = pd.DataFrame(audit, columns=[
        "capture_date", "category_id", "search_query", "search_rank",
        "publication_id", "catalog_product_id", "sku_verified",
        "seller", "sponsored", "currency", "url", "price_current",
        "price_regular", "promotion",
    ])
    summary = pd.DataFrame([{
        "retailer": "Mercado Libre México",
        "category_id": category_id,
        "capture_date": date,
        "search_query": query,
        "status": metadata["status"],
        "reason": metadata["reason"],
        "products": len(frame),
        "reported_total": metadata["reported_total"],
        "pages_checked": metadata["pages_checked"],
        "postal_code": None,
        "store_context_verified": False,
        "master_modified": False,
    }])
    return frame, summary, audit_frame


def save_capture(query: str, listings: list[dict], metadata: dict,
                 *, pilot_root: Path = PILOT_ROOT,
                 report_root: Path = REPORT_ROOT,
                 date: str | None = None) -> tuple[Path | None, Path]:
    date = date or datetime.now().astimezone().date().isoformat()
    basename = date + "_" + query_slug(query)
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / (basename + ".json")
    excel_path = pilot_root / (basename + ".xlsx")
    report_data = {
        **metadata,
        "capture_date": date, "retailer": "Mercado Libre México",
        "master_modified": False, "publication_id_is_sku": False,
        "excel": str(excel_path) if listings else None,
    }
    # Atomic diagnostic report; even an HTTP failure must leave evidence.
    tmp_report = report_path.with_name("." + report_path.name + ".tmp")
    tmp_report.write_text(
        json.dumps(report_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_report, report_path)
    if not listings:
        return None, report_path
    pilot_root.mkdir(parents=True, exist_ok=True)
    frame, summary, audit = build_frames(query, listings, metadata, date)
    tmp_excel = excel_path.with_name(
        "." + excel_path.stem + "." + uuid.uuid4().hex + ".xlsx"
    )
    try:
        with pd.ExcelWriter(tmp_excel, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name="Concentrado", index=False)
            summary.to_excel(writer, sheet_name="Resumen", index=False)
            audit.to_excel(writer, sheet_name="Publicaciones", index=False)
        if excel_path.exists():
            backup = report_root / (
                "before_" + basename + "_" + datetime.now().strftime(
                    "%H%M%S_%f"
                ) + ".xlsx"
            )
            shutil.copy2(excel_path, backup)
        for attempt in range(6):
            try:
                os.replace(tmp_excel, excel_path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(min(0.2 * (2 ** attempt), 1.5))
    finally:
        tmp_excel.unlink(missing_ok=True)
    return excel_path, report_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fase 7: piloto CLI Mercado Libre MX sin GUI"
    )
    parser.add_argument(
        "--query", required=True,
        help="Texto de búsqueda, por ejemplo: pasta dental colgate",
    )
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--max-results", type=int, default=100)
    parser.add_argument("--delay-seconds", type=float, default=1.5)
    args = parser.parse_args()
    listings, report = run_search(
        args.query, max_pages=args.max_pages,
        max_results=args.max_results,
        delay_seconds=args.delay_seconds,
    )
    excel, diagnostic = save_capture(args.query, listings, report)
    print(
        f"MERCADO LIBRE MX | {report['status']} | "
        f"publicaciones={report['collected']}/"
        f"{report['reported_total'] or '?'} | "
        f"páginas={report['pages_checked']} | {report['reason']}"
    )
    print("Excel: " + (str(excel) if excel else "sin filas válidas"))
    print("Diagnóstico: " + str(diagnostic))
    print("MASTER PRODUCTIVO: SIN CAMBIOS")
    print("Los IDs de publicación NO se han convertido en SKU.")
    return 0 if report["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
