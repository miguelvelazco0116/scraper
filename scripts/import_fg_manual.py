from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS, update_consolidated_output
from scraper.config import load_categories, load_locations
from scraper.parsers import clean_text
from scraper.retailers.farmacias_guadalajara import FarmaciasGuadalajaraScraper


DIAGNOSTICS = ROOT / "diagnostics"
CONSOLIDATED = ROOT / "output" / "concentrado_scraper.xlsx"


def _load_payload(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(
            f"No se pudo leer JSON: {type(exc).__name__}: {exc}"
        ) from exc

    if payload.get("retailer") != "Farmacias Guadalajara":
        raise RuntimeError("El archivo no corresponde a Farmacias Guadalajara")

    return payload


def _category(category_id: str):
    categories = load_categories(
        ROOT / "config" / "farmacias-guadalajara" / "categories.yaml"
    )
    category = next((x for x in categories if x.id == category_id), None)
    if category is None:
        raise RuntimeError(f"Categoría no configurada: {category_id}")
    return category


def _location():
    locations = load_locations(ROOT / "config" / "locations.yaml")
    location = next((x for x in locations if x.id == "fg-online"), None)
    if location is None:
        raise RuntimeError("No se encontró ubicación fg-online")
    return location


def _normalize(payload: dict) -> tuple[pd.DataFrame, dict]:
    category_id = str(payload.get("category_id") or "").strip()
    category = _category(category_id)
    location = _location()
    cards = payload.get("cards") or []

    now = (
        clean_text(payload.get("scraped_at"))
        or datetime.now().astimezone().isoformat(timespec="seconds")
    )

    rows: list[dict] = []
    for card in cards:
        url = clean_text(card.get("href"))
        product = clean_text(card.get("name"))
        sku = (
            clean_text(card.get("dataPid"))
            or FarmaciasGuadalajaraScraper.extract_sku(url)
        )
        if not url or not product or not sku:
            continue

        current, regular, promotion = (
            FarmaciasGuadalajaraScraper._prices_from_text(card.get("text"))
        )
        brand = (
            clean_text(card.get("brand"))
            or FarmaciasGuadalajaraScraper._infer_brand(
                product,
                card.get("text"),
            )
        )

        rows.append(
            {
                "scrape_timestamp": now,
                "retailer": "Farmacias Guadalajara",
                "city": location.city,
                "state": location.state,
                "postal_code": location.postal_code,
                "store": location.store,
                "store_id": location.store_id,
                "department": category.department,
                "category": category.name,
                "subcategory": category.subcategory,
                "sub_subcategory": category.sub_subcategory,
                "category_id": category.id,
                "sku": sku,
                "brand": brand,
                "product": product,
                "price_current": current,
                "price_regular": regular,
                "promotion": promotion,
                "pickup_available": None,
                "store_context_verified": False,
                "store_context_method": "manual_edge_online_catalog",
                "url": url,
                "price_raw": clean_text(card.get("text")),
            }
        )

    df = pd.DataFrame(rows, columns=COLUMNS)
    if not df.empty:
        df = (
            df.drop_duplicates(subset=["sku", "url"], keep="last")
            .sort_values(["brand", "product"], na_position="last")
            .reset_index(drop=True)
        )

    target = payload.get("target_products")
    product_links = int(payload.get("product_links") or 0)
    unique_skus = int(df["sku"].nunique()) if not df.empty else 0
    unique_urls = int(df["url"].nunique()) if not df.empty else 0
    priced_rows = int(df["price_current"].notna().sum()) if not df.empty else 0

    validation = {
        "category_id": category_id,
        "target_products": target,
        "product_links": product_links,
        "rows": len(df),
        "unique_skus": unique_skus,
        "unique_urls": unique_urls,
        "priced_rows": priced_rows,
        "complete": False,
    }

    if not isinstance(target, int) or target <= 0:
        raise RuntimeError("No se pudo determinar target_products")

    if product_links < target:
        raise RuntimeError(
            f"Catálogo incompleto: links={product_links}, target={target}"
        )

    if len(df) < target:
        raise RuntimeError(
            f"Filas incompletas: rows={len(df)}, target={target}"
        )

    if unique_skus < target:
        raise RuntimeError(
            f"SKUs incompletos: unique_skus={unique_skus}, target={target}"
        )

    if unique_urls < target:
        raise RuntimeError(
            f"URLs incompletas: unique_urls={unique_urls}, target={target}"
        )

    if priced_rows < target:
        raise RuntimeError(
            f"Precios incompletos: priced_rows={priced_rows}, target={target}"
        )

    validation["complete"] = True
    return df, validation


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Importa JSON manual de Farmacias Guadalajara al concentrado"
    )
    parser.add_argument("--input", required=True)
    args = parser.parse_args()

    path = Path(args.input).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"No existe archivo: {path}")

    payload = _load_payload(path)

    try:
        df, validation = _normalize(payload)
    except Exception as exc:
        print(f"VALIDATION ERROR: {exc}")
        return 2

    output = update_consolidated_output(df, CONSOLIDATED)

    DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
    diag_path = DIAGNOSTICS / (
        f"farmacias_guadalajara_{validation['category_id']}_manual.json"
    )
    diag_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        f"VALIDATED [{validation['category_id']}]: "
        f"target={validation['target_products']} "
        f"links={validation['product_links']} "
        f"rows={validation['rows']} "
        f"skus={validation['unique_skus']} "
        f"prices={validation['priced_rows']}"
    )
    print(f"Concentrado: {output}")
    print(f"Diagnóstico: {diag_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
