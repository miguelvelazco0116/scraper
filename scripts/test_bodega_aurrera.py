from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from main import COLUMNS
from scraper.config import load_categories, load_locations
from scraper.retailers.bodega_aurrera import (
    BodegaAurreraBlocked,
    BodegaAurreraNetworkUnavailable,
    BodegaAurreraScraper,
)


OUTPUT_PATH = ROOT / "output" / "bodega_aurrera_cuidado_bucal_test.xlsx"


def main() -> int:
    category = next(
        x
        for x in load_categories(
            ROOT / "config" / "bodega-aurrera" / "categories.yaml"
        )
        if x.id == "cuidado-bucal"
    )
    location = next(
        x
        for x in load_locations(ROOT / "config" / "locations.yaml")
        if x.id == "bodega-aurrera-online"
    )

    print("=" * 72)
    print("BODEGA AURRERA - TEST CUIDADO BUCAL")
    print("=" * 72)
    print(f"Departamento : {category.department}")
    print(f"Categoría    : {category.name}")
    print(f"Subcategoría : {category.subcategory}")
    print(f"URL          : {category.url}")
    print(f"Salida       : {OUTPUT_PATH}")
    print("")
    print(
        "Si Bodega Aurrera muestra 'Verifica tu identidad', completa "
        "manualmente la verificación en Chrome. El script NO la evade."
    )
    print("")

    scraper = BodegaAurreraScraper(
        headless=False,
        browser_channel="chrome",
        max_pages=30,
        wait_ms=1200,
        manual_verification_timeout_ms=180_000,
    )

    rows: list[dict] = []
    status = "SUCCESS"
    error = None

    try:
        rows = scraper.scrape_category(category, location)
        status = str(scraper.run_meta.get("status") or "SUCCESS")
    except BodegaAurreraBlocked as exc:
        status = "BLOCKED"
        error = str(exc)
    except BodegaAurreraNetworkUnavailable as exc:
        status = "NETWORK_UNAVAILABLE"
        error = str(exc)
    except Exception as exc:
        status = "ERROR"
        error = f"{type(exc).__name__}: {exc}"

    df = pd.DataFrame(rows, columns=COLUMNS)
    if not df.empty:
        df = (
            df.drop_duplicates(subset=["sku", "url"], keep="last")
            .sort_values(["brand", "product"], na_position="last")
            .reset_index(drop=True)
        )

    meta = scraper.run_meta or {}

    summary = pd.DataFrame(
        [
            {
                "retailer": "Bodega Aurrera",
                "category_id": category.id,
                "status": status,
                "products": len(df),
                "sku_complete": int(
                    df["sku"].fillna("").astype(str).str.strip().ne("").sum()
                ) if not df.empty else 0,
                "price_complete": int(
                    df["price_current"].notna().sum()
                ) if not df.empty else 0,
                "url_complete": int(
                    df["url"].fillna("").astype(str).str.strip().ne("").sum()
                ) if not df.empty else 0,
                "blocked_detected": bool(
                    meta.get("blocked_detected")
                ),
                "manual_verification_required": bool(
                    meta.get("manual_verification_required")
                ),
                "manual_verification_resolved": bool(
                    meta.get("manual_verification_resolved")
                ),
                "pages_visited": len(meta.get("pages") or []),
                "error": error,
            }
        ]
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Concentrado")
        summary.to_excel(writer, index=False, sheet_name="Resumen")
        pd.DataFrame(meta.get("pages") or []).to_excel(
            writer,
            index=False,
            sheet_name="Paginas",
        )
        pd.DataFrame(meta.get("card_extraction") or []).to_excel(
            writer,
            index=False,
            sheet_name="Extraccion",
        )

    print("")
    print("=" * 72)
    print("RESULTADO BODEGA AURRERA")
    print("=" * 72)
    print(f"status                       : {status}")
    print(f"products                     : {len(df)}")
    print(f"sku_complete                 : {summary.iloc[0]['sku_complete']}")
    print(f"price_complete               : {summary.iloc[0]['price_complete']}")
    print(f"url_complete                 : {summary.iloc[0]['url_complete']}")
    print(
        f"blocked_detected              : "
        f"{summary.iloc[0]['blocked_detected']}"
    )
    print(
        f"manual_verification_required : "
        f"{summary.iloc[0]['manual_verification_required']}"
    )
    print(
        f"manual_verification_resolved : "
        f"{summary.iloc[0]['manual_verification_resolved']}"
    )
    print(f"pages_visited                : {summary.iloc[0]['pages_visited']}")
    if error:
        print(f"error                        : {error}")

    print("")
    print(f"Archivo: {OUTPUT_PATH}")
    print("Diagnósticos: diagnostics\\bodega_aurrera_*")

    return 0 if status == "SUCCESS" and len(df) > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
