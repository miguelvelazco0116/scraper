"""Farmatodo Mexico CLI: catalogue-only pilot, three retries per category.

No postcode/store verification, no changes to productive master, runner,
dashboard, checkpoint pointer, or any other retailer.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from playwright.sync_api import sync_playwright

from main import COLUMNS
from scraper.config import load_categories
from scraper.retailers.farmatodo import FarmatodoScraper, FarmatodoCaptureError

OUTPUT = ROOT / "output" / "pilots" / "farmatodo" / "catalogo_online.xlsx"
REPORT = ROOT / "diagnostics" / "farmatodo" / "last_run.json"
MAX_ATTEMPTS = 3


def quality(rows: list[dict], meta: dict, *, error: str | None = None) -> dict:
    products = len(rows)
    frame = pd.DataFrame(rows)
    def count(field: str, check) -> int:
        return sum(check(value) for value in frame.get(
            field, pd.Series(dtype=object)
        )) if products else 0

    sku = count("sku", lambda x: bool(str(x).strip()) if pd.notna(x) else False)
    urls = count("url", lambda x: (
        isinstance(x, str) and urlparse(x).hostname == "www.farmatodo.com.mx"
        and urlparse(x).path.rstrip("/").endswith("/p")
    ))
    prices = count(
        "price_current", lambda x: pd.notna(x) and float(x) > 0
    )
    target = meta.get("target_products")
    notes = []
    if error:
        notes.append(error)
    if not products:
        notes.append("sin productos")
    if not isinstance(target, int) or target <= 0:
        notes.append("total publicado no verificable")
    elif not 0.90 <= products / target <= 1.0:
        notes.append(f"cobertura {products}/{target} fuera de 90%-100%")
    if meta.get("load_more_stalled") or meta.get("load_more_remaining"):
        notes.append("carga de Mostrar más incompleta")
    pdp_verified = count(
        "sku_verified", lambda x: x is True
        or (pd.notna(x) and str(x).strip().lower() == "true")
    )
    if products and sku != products:
        notes.append(f"SKU no identificado en {products - sku}/{products}")
    if products and pdp_verified != products:
        notes.append(
            f"SKU sin evidencia visible en PDP: "
            f"{products - pdp_verified}/{products}"
        )
    if products and meta.get("pdp_sku_verified") != products:
        notes.append(
            f"PDP verificados {meta.get('pdp_sku_verified', 0)}/{products}"
        )
    if products and urls != products:
        notes.append(f"URL incorrecta en {products - urls}/{products}")
    if products and prices != products:
        notes.append(f"precio incompleto en {products - prices}/{products}")
    if frame.get("url") is not None and frame["url"].duplicated().any():
        notes.append("URLs duplicadas")
    if products and any(
        row.get("postal_code") is not None or row.get("store_context_verified")
        for row in rows
    ):
        notes.append("se asignó ubicación postal sin verificar")
    return {
        "retailer": "Farmatodo", "category_id": meta["category_id"],
        "capture_date": meta["capture_date"],
        "status": "COMPLETE" if not notes else "PARTIAL",
        "products": products, "target_products": target,
        "coverage": products / target if isinstance(target,int) and target else None,
        "sku_complete": sku, "sku_pdp_verified": pdp_verified,
        "url_complete": urls, "price_complete": prices,
        "load_more_clicks": meta.get("load_more_clicks"),
        "load_more_stalled": meta.get("load_more_stalled"),
        "pdp_sku_checked": meta.get("pdp_sku_checked"),
        "sku_source_counts": json.dumps(
            meta.get("sku_source_counts", {}), ensure_ascii=False
        ),
        "postal_code": None, "store_context_verified": False,
        "integration_scope": "PUBLIC_ONLINE_CATALOGUE_NO_POSTAL_VERIFICATION",
        "quality_notes": "; ".join(notes),
    }


def valid_cached(frame: pd.DataFrame, summary: dict, today: str, sku_audit: pd.DataFrame | None = None) -> bool:
    if str(summary.get("capture_date")) != today:
        return False
    if str(summary.get("status")) != "COMPLETE":
        return False
    if int(summary.get("sku_pdp_verified") or 0) != int(summary.get("products") or 0):
        return False
    cat = str(summary.get("category_id") or "")
    rows = frame.loc[frame["category_id"].astype(str).eq(cat)]
    if rows.empty or len(rows) != int(summary.get("products") or 0):
        return False
    if not rows["scrape_timestamp"].astype(str).str.startswith(today).all():
        return False
    if rows["postal_code"].notna().any() or rows["store_id"].notna().any():
        return False
    if not rows["store_context_verified"].astype(str).str.lower().eq("false").all():
        return False
    if rows["sku"].fillna("").astype(str).str.strip().eq("").any():
        return False
    if not pd.to_numeric(rows["price_current"], errors="coerce").gt(0).all():
        return False
    if rows.duplicated(subset=["category_id", "url"]).any():
        return False
    if sku_audit is None:
        return False
    required = {"category_id", "sku", "url", "sku_verified", "sku_source"}
    if not required.issubset(sku_audit.columns):
        return False
    proof = sku_audit.loc[
        sku_audit["category_id"].astype(str).eq(cat)
    ]
    if len(proof) != len(rows):
        return False
    if (not proof["sku_source"].astype(str).eq("pdp_visible_sku").all()
            or not proof["sku_verified"].astype(str).str.lower().eq("true").all()):
        return False
    expected = rows[["sku", "url"]].astype(str).sort_values(
        ["url", "sku"]
    ).reset_index(drop=True)
    found = proof[["sku", "url"]].astype(str).sort_values(
        ["url", "sku"]
    ).reset_index(drop=True)
    return expected.equals(found)


def load_cache(today: str) -> dict[str, tuple[pd.DataFrame, dict]]:
    if not OUTPUT.is_file():
        return {}
    try:
        frame = pd.read_excel(
            OUTPUT, sheet_name="Concentrado",
            dtype={"sku": str, "postal_code": str, "category_id": str}
        )
        summaries = pd.read_excel(
            OUTPUT, sheet_name="Resumen", dtype={"category_id": str}
        )
        sku_audit = pd.read_excel(
            OUTPUT, sheet_name="SkuAudit",
            dtype={"category_id": str, "sku": str, "url": str},
        )
    except (OSError, ValueError, KeyError, ImportError):
        return {}
    if frame.empty or summaries.empty:
        return {}
    cache = {}
    for row in summaries.to_dict("records"):
        cat = str(row.get("category_id") or "")
        if valid_cached(frame, row, today, sku_audit):
            cache[cat] = (
                frame.loc[frame["category_id"].astype(str).eq(cat)].copy().merge(
                    sku_audit.loc[
                        sku_audit["category_id"].astype(str).eq(cat),
                        ["category_id", "sku", "url", "sku_source", "sku_verified"],
                    ], on=["category_id", "sku", "url"], how="left",
                    validate="one_to_one",
                ), row
            )
    return cache


def save_excel(items: dict[str, tuple[pd.DataFrame, dict]]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    if OUTPUT.is_file():
        backup = REPORT.parent / (
            "before_farmatodo_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            + ".xlsx"
        )
        shutil.copy2(OUTPUT, backup)
    frames = [x[0] for x in items.values()]
    master = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    master = master.reindex(columns=COLUMNS)
    audit_cols = ["category_id", "sku", "url", "sku_source", "sku_verified"]
    audit = pd.concat(frames, ignore_index=True).reindex(columns=audit_cols) if frames else pd.DataFrame(columns=audit_cols)
    summaries = pd.DataFrame([item[1] for item in items.values()])
    temp = OUTPUT.with_name("." + OUTPUT.stem + "." + uuid.uuid4().hex + ".xlsx")
    try:
        with pd.ExcelWriter(temp, engine="openpyxl") as writer:
            master.to_excel(writer, sheet_name="Concentrado", index=False)
            summaries.to_excel(writer, sheet_name="Resumen", index=False)
            audit.to_excel(writer, sheet_name="SkuAudit", index=False)
        for attempt in range(8):
            try:
                os.replace(temp, OUTPUT)
                break
            except PermissionError:
                if attempt >= 7:
                    raise
                time.sleep(min(0.2 * 2 ** attempt, 1))
    finally:
        temp.unlink(missing_ok=True)


def run_category(browser, category, *, max_rounds: int, probe: bool = False):
    failures = []
    last_rows: list[dict] = []
    last_summary: dict | None = None
    for attempt in range(1, 2 if probe else MAX_ATTEMPTS + 1):
        print(
            f"Farmatodo {category.id} · intento {attempt}"
            f"/{1 if probe else MAX_ATTEMPTS}", flush=True
        )
        page = browser.new_page(
            viewport={"width": 1440, "height": 1000}, locale="es-MX"
        )
        try:
            rows, meta = FarmatodoScraper(max_rounds=max_rounds).capture_category(
                page, category, probe=probe
            )
            result = quality(rows, meta)
            result["attempts"] = attempt
            last_rows, last_summary = rows, result
            if probe:
                # Preview coverage is deliberately partial; require at least
                # one identifiable priced product before declaring probe-ready.
                checked = int(meta.get("pdp_sku_checked") or 0)
                verified = int(meta.get("pdp_sku_verified") or 0)
                priced = sum(bool(row.get("price_current")) for row in rows[:checked])
                result["sku_debug_samples"] = meta.get("sku_debug_samples", [])
                result["pdp_sku_samples"] = meta.get("pdp_sku_samples", [])
                result["probe_sku_checked"] = checked
                result["probe_sku_verified"] = verified
                result["status"] = (
                    "PROBE" if (
                        checked >= min(3, len(rows)) and
                        verified == checked and
                        priced == checked and checked > 0
                    ) else "NEEDS_SKU_REVIEW"
                )
            if result["status"] == "COMPLETE" or probe:
                return rows, result
            failures.append(result["quality_notes"])
            print("PARTIAL: " + result["quality_notes"])
        except Exception as exc:
            failures.append(f"{type(exc).__name__}: {exc}")
            print("ERROR: " + failures[-1])
        finally:
            page.close()
        if attempt < MAX_ATTEMPTS:
            time.sleep(2)
    if last_summary is not None:
        # Preserve incomplete evidence for local diagnosis, but do not cache
        # it as COMPLETE or integrate it into the productive master.
        last_summary["quality_notes"] = " | ".join(failures[-3:])
        return last_rows, last_summary
    return [], {
        "retailer": "Farmatodo", "category_id": category.id,
        "capture_date": datetime.now().astimezone().date().isoformat(),
        "status": "PARTIAL", "products": 0,
        "postal_code": None, "store_context_verified": False,
        "attempts": len(failures), "quality_notes": " | ".join(failures[-3:]),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Farmatodo online sin verificación de CP")
    p.add_argument("--category", default="all")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--cdp-url", default="")
    p.add_argument("--max-rounds", type=int, default=100)
    p.add_argument("--probe", action="store_true", help="Diagnóstico sin XLSX")
    args = p.parse_args()
    if not 1 <= args.max_rounds <= 200:
        p.error("max-rounds debe ser 1..200")
    if args.probe and args.category == "all":
        p.error("--probe requiere una categoría individual")

    categories = load_categories(ROOT / "config" / "farmatodo" / "categories.yaml")
    if args.category != "all":
        categories = [c for c in categories if c.id == args.category]
    if not categories:
        p.error("Categoría Farmatodo desconocida")
    today = datetime.now().astimezone().date().isoformat()
    cache = {} if args.probe else load_cache(today)
    expected = {cat.id for cat in categories}
    results = {cat: value for cat, value in cache.items() if cat in expected}
    finished = True

    with sync_playwright() as playwright:
        if args.cdp_url:
            browser = playwright.chromium.connect_over_cdp(args.cdp_url)
            # CDP sessions are operator-owned; only close pages opened here.
            session_context = browser.contexts[0]
            class ExistingSession:
                def new_page(self, **kwargs):
                    return session_context.new_page()
            owner = ExistingSession()
        else:
            browser = playwright.chromium.launch(
                headless=not args.headed, channel="chrome"
            )
            owner = browser
        try:
            for category in categories:
                if category.id in cache:
                    print(f"REUTILIZADA hoy: {category.id}")
                    continue
                rows, summary = run_category(
                    owner, category, max_rounds=args.max_rounds,
                    probe=args.probe
                )
                print(
                    f"{category.id} | {summary['status']} | productos="
                    f"{summary['products']} | {summary.get('quality_notes', '')}"
                )
                if args.probe:
                    REPORT.parent.mkdir(parents=True, exist_ok=True)
                    path = REPORT.parent / f"probe_{category.id}_{today}.json"
                    path.write_text(
                        json.dumps(summary, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print("Diagnóstico: " + str(path))
                else:
                    results[category.id] = (
                        pd.DataFrame(rows), summary
                    )
                    save_excel(results)
                finished = finished and summary["status"] in ("COMPLETE", "PROBE")
        finally:
            if not args.cdp_url:
                browser.close()

    if not args.probe:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "capture_date": today, "retailer": "Farmatodo",
            "master_modified": False,
            "postal_verification_required": False,
            "categories": [entry[1] for entry in results.values()],
            "excel": str(OUTPUT),
            "complete": finished,
        }
        REPORT.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8"
        )
        print("Excel: " + str(OUTPUT))
    print("MASTER PRODUCTIVO: SIN CAMBIOS")
    return 0 if finished else 2


if __name__ == "__main__":
    raise SystemExit(main())
