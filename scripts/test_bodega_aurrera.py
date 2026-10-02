from __future__ import annotations

import json
import re
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


OUTPUT_PATH = ROOT / "output" / "bodega_aurrera_test.xlsx"

CURRENT_RE = re.compile(
    r"precio\s+actual\s*\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
BEFORE_RE = re.compile(
    r"Antes\s*\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
    re.IGNORECASE,
)


def _money(group: str | None) -> float | None:
    if not group:
        return None
    try:
        return float(group.replace(",", ""))
    except ValueError:
        return None


def audit_visible_prices(df: pd.DataFrame, category_id: str) -> pd.DataFrame:
    """Compare exported prices with the explicit prices shown in each card."""
    records = []

    for _, row in df.iterrows():
        raw = str(row.get("price_raw") or "")
        current_match = CURRENT_RE.search(raw)
        before_match = BEFORE_RE.search(raw)

        expected_current = _money(
            current_match.group(1) if current_match else None
        )
        expected_regular = _money(
            before_match.group(1) if before_match else None
        )

        actual_current = row.get("price_current")
        actual_regular = row.get("price_regular")

        current_ok = (
            expected_current is None
            or (
                pd.notna(actual_current)
                and abs(float(actual_current) - expected_current) < 0.005
            )
        )
        regular_ok = (
            expected_regular is None
            or (
                pd.notna(actual_regular)
                and abs(float(actual_regular) - expected_regular) < 0.005
            )
        )
        positive_ok = (
            pd.notna(actual_current)
            and float(actual_current) > 0
        )
        order_ok = (
            pd.isna(actual_regular)
            or float(actual_regular) >= float(actual_current)
        )

        records.append(
            {
                "category_id": category_id,
                "sku": row.get("sku"),
                "product": row.get("product"),
                "url": row.get("url"),
                "price_current": actual_current,
                "expected_current_from_card": expected_current,
                "current_price_match": current_ok,
                "price_regular": actual_regular,
                "expected_regular_from_card": expected_regular,
                "regular_price_match": regular_ok,
                "positive_price": positive_ok,
                "regular_gte_current": order_ok,
                "price_validation_ok": (
                    current_ok
                    and regular_ok
                    and positive_ok
                    and order_ok
                ),
            }
        )

    return pd.DataFrame(records)


def run_category(category, location):
    print("")
    print("-" * 72)
    print(f"BODEGA AURRERA | {category.id}")
    print("-" * 72)
    print(f"Departamento : {category.department}")
    print(f"Categoría    : {category.name}")
    print(f"Subcategoría : {category.subcategory}")
    print(f"URL          : {category.url}")
    print("")

    scraper = BodegaAurreraScraper(
        headless=False,
        browser_channel="chrome",
        max_pages=30,
        wait_ms=1200,
        manual_verification_timeout_ms=180_000,
    )

    rows = []
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
    price_audit = audit_visible_prices(df, category.id)

    sources = []
    for item in meta.get("sources") or []:
        flat = {
            "category_id": category.id,
            **{
                key: value
                for key, value in item.items()
                if key not in {"explicit_page_meta"}
            },
            "explicit_page_meta": json.dumps(
                item.get("explicit_page_meta") or [],
                ensure_ascii=False,
            ),
        }
        sources.append(flat)

    extraction = [
        {"category_id": category.id, **item}
        for item in (meta.get("card_extraction") or [])
    ]

    explicit_current_rows = (
        int(price_audit["expected_current_from_card"].notna().sum())
        if not price_audit.empty
        else 0
    )
    explicit_current_matches = (
        int(
            (
                price_audit["expected_current_from_card"].notna()
                & price_audit["current_price_match"]
            ).sum()
        )
        if not price_audit.empty
        else 0
    )
    explicit_regular_rows = (
        int(price_audit["expected_regular_from_card"].notna().sum())
        if not price_audit.empty
        else 0
    )
    explicit_regular_matches = (
        int(
            (
                price_audit["expected_regular_from_card"].notna()
                & price_audit["regular_price_match"]
            ).sum()
        )
        if not price_audit.empty
        else 0
    )
    price_validation_errors = (
        int((~price_audit["price_validation_ok"]).sum())
        if not price_audit.empty
        else 0
    )

    summary = {
        "retailer": "Bodega Aurrera",
        "department": category.department,
        "category": category.name,
        "subcategory": category.subcategory,
        "category_id": category.id,
        "status": status,
        "products": len(df),
        "sku_complete": int(
            df["sku"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not df.empty else 0,
        "price_complete": int(df["price_current"].notna().sum())
        if not df.empty else 0,
        "url_complete": int(
            df["url"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not df.empty else 0,
        "sources_discovered": int(meta.get("sources_discovered") or 0),
        "sources_visited": len(meta.get("sources") or []),
        "pagination_verified": bool(meta.get("pagination_verified")),
        "explicit_pages": sum(
            int(item.get("explicit_pages") or 0)
            for item in (meta.get("sources") or [])
        ),
        "explicit_current_rows": explicit_current_rows,
        "explicit_current_matches": explicit_current_matches,
        "explicit_regular_rows": explicit_regular_rows,
        "explicit_regular_matches": explicit_regular_matches,
        "price_validation_errors": price_validation_errors,
        "blocked_detected": bool(meta.get("blocked_detected")),
        "manual_verification_required": bool(
            meta.get("manual_verification_required")
        ),
        "manual_verification_resolved": bool(
            meta.get("manual_verification_resolved")
        ),
        "error": error,
    }

    print("RESULTADO")
    for key in (
        "status",
        "products",
        "sku_complete",
        "price_complete",
        "url_complete",
        "sources_discovered",
        "sources_visited",
        "explicit_pages",
        "pagination_verified",
        "explicit_current_rows",
        "explicit_current_matches",
        "explicit_regular_rows",
        "explicit_regular_matches",
        "price_validation_errors",
        "blocked_detected",
        "manual_verification_required",
        "manual_verification_resolved",
    ):
        print(f"{key:29}: {summary[key]}")
    if error:
        print(f"{'error':29}: {error}")

    return df, summary, sources, extraction, price_audit


def main() -> int:
    categories = [
        x
        for x in load_categories(
            ROOT / "config" / "bodega-aurrera" / "categories.yaml"
        )
    ]
    location = next(
        x
        for x in load_locations(ROOT / "config" / "locations.yaml")
        if x.id == "bodega-aurrera-online"
    )

    print("=" * 72)
    print("BODEGA AURRERA - VALIDACION DE COBERTURA Y PRECIOS")
    print("=" * 72)
    print("La prueba valida:")
    print("  1. Fuentes/secciones reales de la categoría.")
    print("  2. Carga hasta estabilizar el catálogo visible.")
    print("  3. Páginas explícitas si el sitio las publica.")
    print("  4. price_current contra 'precio actual' mostrado.")
    print("  5. price_regular contra 'Antes' mostrado.")
    print("")
    print("Si aparece 'Verifica tu identidad', completa manualmente la")
    print("verificación en Chrome. El scraper NO la evade.")

    frames = []
    summaries = []
    sources = []
    extraction = []
    price_audits = []

    for category in categories:
        (
            df,
            summary,
            category_sources,
            category_extraction,
            category_price_audit,
        ) = run_category(category, location)
        frames.append(df)
        summaries.append(summary)
        sources.extend(category_sources)
        extraction.extend(category_extraction)
        price_audits.append(category_price_audit)

    concentrated = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=COLUMNS)
    )
    summary_df = pd.DataFrame(summaries)
    price_audit_df = (
        pd.concat(price_audits, ignore_index=True)
        if price_audits
        else pd.DataFrame()
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as writer:
        concentrated.to_excel(writer, index=False, sheet_name="Concentrado")
        summary_df.to_excel(writer, index=False, sheet_name="Resumen")
        pd.DataFrame(sources).to_excel(
            writer,
            index=False,
            sheet_name="Fuentes",
        )
        pd.DataFrame(extraction).to_excel(
            writer,
            index=False,
            sheet_name="Extraccion",
        )
        price_audit_df.to_excel(
            writer,
            index=False,
            sheet_name="ValidacionPrecios",
        )

    print("")
    print("=" * 72)
    print("RESUMEN BODEGA AURRERA")
    print("=" * 72)
    print(
        summary_df[
            [
                "department",
                "category_id",
                "status",
                "products",
                "sku_complete",
                "price_complete",
                "url_complete",
                "sources_discovered",
                "sources_visited",
                "explicit_pages",
                "pagination_verified",
                "price_validation_errors",
                "blocked_detected",
            ]
        ].to_string(index=False)
    )
    print("")
    print(f"Total filas: {len(concentrated)}")
    print(f"Archivo: {OUTPUT_PATH}")
    print("Diagnósticos: diagnostics\\bodega_aurrera_*")

    acceptable = True
    for summary in summaries:
        acceptable = acceptable and (
            summary["status"] == "SUCCESS"
            and summary["products"] > 0
            and summary["sku_complete"] == summary["products"]
            and summary["price_complete"] == summary["products"]
            and summary["url_complete"] == summary["products"]
            and summary["sources_discovered"] == summary["sources_visited"]
            and summary["pagination_verified"]
            and summary["price_validation_errors"] == 0
        )

    return 0 if acceptable else 2


if __name__ == "__main__":
    raise SystemExit(main())
