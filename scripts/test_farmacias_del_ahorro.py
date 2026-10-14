from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS
from scraper.config import load_categories, load_locations
from scraper.retailers.farmacias_del_ahorro import (
    FarmaciasDelAhorroBlocked,
    FarmaciasDelAhorroNetworkUnavailable,
    FarmaciasDelAhorroScraper,
)

OUTPUT = ROOT / "output" / "farmacias_del_ahorro_test.xlsx"


def canonical(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame[COLUMNS]


def nonempty(series: pd.Series) -> int:
    return int(series.fillna("").astype(str).str.strip().ne("").sum())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test aislado de Farmacias del Ahorro."
    )
    parser.add_argument(
        "--category",
        default="anti-acne",
        help="ID de categoría; por defecto anti-acne.",
    )
    args = parser.parse_args()

    categories = {
        item.id: item
        for item in load_categories(
            ROOT / "config" / "farmacias-del-ahorro" / "categories.yaml"
        )
    }
    if args.category not in categories:
        print(f"ERROR: categoría desconocida: {args.category}")
        return 2

    locations = {
        item.id: item
        for item in load_locations(ROOT / "config" / "locations.yaml")
    }
    category = categories[args.category]
    location = locations["fahorro-online"]

    print("=" * 76)
    print("FARMACIAS DEL AHORRO - TEST AISLADO")
    print("=" * 76)
    print(f"Categoría : {category.id}")
    print(f"Jerarquía : {category.department} > {category.name} > "
          f"{category.subcategory} > {category.sub_subcategory}")
    print(f"URL       : {category.url}")
    print("Gate      : cobertura >= 90%; SKU/precio/URL >= 90%")
    print("")

    scraper = FarmaciasDelAhorroScraper(
        headless=True,
        max_pages=100,
        rows_per_page=50,
    )

    rows: list[dict] = []
    error: str | None = None
    try:
        rows = scraper.scrape_category(category, location)
    except (FarmaciasDelAhorroBlocked, FarmaciasDelAhorroNetworkUnavailable) as exc:
        error = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    frame = canonical(rows)
    meta = dict(getattr(scraper, "last_meta", {}) or {})
    target = meta.get("target_products")
    products = len(frame)
    sku_complete = nonempty(frame["sku"]) if products else 0
    price_complete = int(
        pd.to_numeric(frame["price_current"], errors="coerce").notna().sum()
    ) if products else 0
    url_complete = nonempty(frame["url"]) if products else 0
    coverage = (
        products / int(target)
        if target not in (None, 0)
        else 0.0
    )
    sku_rate = sku_complete / products if products else 0.0
    price_rate = price_complete / products if products else 0.0
    url_rate = url_complete / products if products else 0.0

    status = "SUCCESS"
    if error:
        status = "ERROR"
    elif (
        target in (None, 0)
        or coverage < 0.90
        or sku_rate < 0.90
        or price_rate < 0.90
        or url_rate < 0.90
    ):
        status = "PARTIAL"

    summary = pd.DataFrame(
        [
            {
                "retailer": "Farmacias del Ahorro",
                "category_id": category.id,
                "status": status,
                "target_products": target,
                "products": products,
                "coverage": coverage,
                "sku_complete": sku_complete,
                "price_complete": price_complete,
                "url_complete": url_complete,
                "api_pages": meta.get("api_pages"),
                "empathy_category_id": meta.get("empathy_category_id"),
                "error": error,
            }
        ]
    )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Concentrado")
        summary.to_excel(writer, index=False, sheet_name="Resumen")

    print("RESULTADO")
    print(f"status          : {status}")
    print(f"target_products : {target}")
    print(f"products        : {products}")
    print(f"coverage        : {coverage:.2%}")
    print(f"sku_complete    : {sku_complete}/{products}")
    print(f"price_complete  : {price_complete}/{products}")
    print(f"url_complete    : {url_complete}/{products}")
    print(f"api_pages       : {meta.get('api_pages')}")
    print(f"categoryId API  : {meta.get('empathy_category_id')}")
    if error:
        print(f"error           : {error}")
    print(f"Output          : {OUTPUT}")

    return 0 if status == "SUCCESS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
