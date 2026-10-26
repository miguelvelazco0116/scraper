"""Pilot YZA capture: two categories, postcode 11590, no master integration."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS
from scraper.config import load_categories, load_locations
from scraper.retailers.yza import (
    POSTAL_CODE, YzaContextError, YzaScrapeError, YzaScraper,
)

OUTPUT = ROOT / "output" / "yza_cp11590_test.xlsx"
REPORT = ROOT / "diagnostics" / "yza" / "last_run.json"
MAX_ATTEMPTS = 3


def _valid_cached(df: pd.DataFrame, entry: dict, category_id: str, day: str) -> bool:
    if str(entry.get("status")) != "SUCCESS":
        return False
    if str(entry.get("capture_date")) != day:
        return False
    if str(entry.get("category_id")) != category_id:
        return False
    if str(entry.get("postal_code")) != POSTAL_CODE:
        return False
    subset = df.loc[df["category_id"].astype(str).eq(category_id)]
    if subset.empty or len(subset) != int(entry.get("products") or 0):
        return False
    if not subset["scrape_timestamp"].astype(str).str.startswith(day).all():
        return False
    if not subset["postal_code"].astype(str).eq(POSTAL_CODE).all():
        return False
    if not subset["store_context_verified"].eq(True).all():
        return False
    if subset["sku"].fillna("").astype(str).str.strip().eq("").any():
        return False
    if not pd.to_numeric(subset["price_current"], errors="coerce").gt(0).all():
        return False
    if subset.duplicated(subset=["category_id", "sku", "url"]).any():
        return False
    return True


def load_cached(day: str) -> dict[str, tuple[pd.DataFrame, dict]]:
    if not OUTPUT.is_file():
        return {}
    try:
        rows = pd.read_excel(
            OUTPUT, sheet_name="Concentrado",
            dtype={"sku": str, "postal_code": str, "category_id": str},
        )
        summary = pd.read_excel(
            OUTPUT, sheet_name="Resumen",
            dtype={"category_id": str, "postal_code": str},
        )
    except (OSError, ValueError, KeyError):
        return {}
    if rows.empty or summary.empty or "category_id" not in rows:
        return {}
    cached = {}
    for item in summary.to_dict("records"):
        category_id = str(item.get("category_id") or "")
        if _valid_cached(rows, item, category_id, day):
            cached[category_id] = (
                rows.loc[rows["category_id"].astype(str).eq(category_id)].copy(),
                item,
            )
    return cached


def save_output(items: dict[str, tuple[pd.DataFrame, dict]]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    rows = (
        pd.concat([part for part, _ in items.values()], ignore_index=True)
        if items else pd.DataFrame(columns=COLUMNS)
    )
    summary = pd.DataFrame([details for _, details in items.values()])
    tmp = OUTPUT.with_name("." + OUTPUT.stem + "." + uuid.uuid4().hex + ".xlsx")
    try:
        with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
            rows.to_excel(writer, sheet_name="Concentrado", index=False)
            summary.to_excel(writer, sheet_name="Resumen", index=False)
        for attempt in range(8):
            try:
                os.replace(tmp, OUTPUT)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(min(0.1 * (2**attempt), 1))
    finally:
        if tmp.exists():
            tmp.unlink()


def run_category(category, location, cdp_url: str, max_pages: int) -> tuple[pd.DataFrame, dict]:
    last_error = ""
    last_status = "ERROR"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(
            f"YZA | {category.id} | intento {attempt}/{MAX_ATTEMPTS} | CP {POSTAL_CODE}",
            flush=True,
        )
        scraper = YzaScraper(cdp_url=cdp_url, max_pages=max_pages)
        try:
            rows = scraper.scrape_category(category, location)
            df = pd.DataFrame(rows, columns=COLUMNS)
            meta = scraper.run_meta
            if meta.get("status") != "SUCCESS":
                raise YzaScrapeError(
                    "Cobertura o extracción incompleta: " + json.dumps(
                        {k: v for k, v in meta.items() if k != "pages"},
                        ensure_ascii=False, default=str,
                    )
                )
            if df.empty or df.duplicated(["sku", "url"]).any():
                raise YzaScrapeError("Productos vacíos o duplicados")
            if not df["store_context_verified"].eq(True).all():
                raise YzaContextError("Ubicación no verificada en todas las filas")
            summary = {
                "retailer": "Farmacias YZA",
                "category_id": category.id,
                "category": category.name,
                "status": "SUCCESS",
                "capture_date": datetime.now().astimezone().date().isoformat(),
                "postal_code": POSTAL_CODE,
                "store_context_verified": True,
                "store_context_method": "yza_visible_selected_address_cp11590",
                "products": len(df),
                "target_products": int(meta["target_products"]),
                "pages_visited": int(meta["pages_visited"]),
                "pages_expected": int(meta["pages_expected"]),
                "sku_complete": int(df["sku"].notna().sum()),
                "price_complete": int(df["price_current"].notna().sum()),
                "url_complete": int(df["url"].notna().sum()),
                "availability_scope": "NOT_CERTIFIED",
                "error": None,
            }
            print(f"COMPLETE PILOTO {category.id}: {len(df)} productos", flush=True)
            return df, summary
        except YzaContextError as exc:
            last_status = "STORE_CONTEXT_ERROR"
            last_error = str(exc)
        except (YzaScrapeError, Exception) as exc:
            last_status = "PARTIAL"
            last_error = f"{type(exc).__name__}: {exc}"
        print(f"{last_status}: {last_error}", flush=True)
        # Retry the same category before moving to the next.
        if attempt < MAX_ATTEMPTS:
            time.sleep(2)
    return pd.DataFrame(columns=COLUMNS), {
        "retailer": "Farmacias YZA", "category_id": category.id,
        "category": category.name, "status": last_status,
        "capture_date": datetime.now().astimezone().date().isoformat(),
        "postal_code": POSTAL_CODE, "store_context_verified": False,
        "products": 0, "error": last_error,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Piloto YZA CP 11590")
    parser.add_argument("--category", default="all")
    parser.add_argument("--cdp-url", required=True)
    parser.add_argument("--max-pages", type=int, default=100)
    args = parser.parse_args()
    if args.max_pages < 1 or args.max_pages > 200:
        parser.error("--max-pages debe estar entre 1 y 200")
    categories = load_categories(ROOT / "config" / "yza" / "categories.yaml")
    if args.category != "all":
        categories = [c for c in categories if c.id == args.category]
        if not categories:
            parser.error("Categoría YZA desconocida")
    location = next(
        loc for loc in load_locations(ROOT / "config" / "locations.yaml")
        if loc.id == "yza-cp-11590"
    )
    if location.postal_code != POSTAL_CODE:
        raise ValueError("Configuración de CP YZA alterada")
    day = datetime.now().astimezone().date().isoformat()
    existing = load_cached(day)
    items = dict(existing)
    print("PILOTO FARMACIAS YZA | CP 11590 | SIN INTEGRACIÓN AL MASTER")
    print("Abre www.yza.mx en Chrome CDP y selecciona una dirección con CP 11590.")
    complete = True
    for category in categories:
        if category.id in existing:
            print(f"REUTILIZADO {category.id}: captura válida de hoy")
            continue
        frame, summary = run_category(
            category, location, args.cdp_url, args.max_pages
        )
        items[category.id] = frame, summary
        save_output(items)
        complete = complete and summary["status"] == "SUCCESS"
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "capture_date": day, "retailer": "Farmacias YZA",
        "postal_code": POSTAL_CODE,
        "complete_pilot": complete,
        "categories": [entry for _, entry in items.values()],
        "output": str(OUTPUT), "master_modified": False,
    }
    REPORT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"Resultado: {REPORT}")
    print(f"Captura: {OUTPUT}")
    print("MASTER: SIN CAMBIOS")
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
