"""Read-only browser probe for YZA CP 11590. Does not scrape full catalogues."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.config import load_categories
from scraper.retailers.yza import (
    HOST, apply_postal_code, context_evidence, discover_cards,
)

REPORT_DIR = ROOT / "diagnostics" / "yza"


def probe(category_id: str, cdp_url: str) -> int:
    from playwright.sync_api import sync_playwright

    categories = {c.id: c for c in load_categories(
        ROOT / "config" / "yza" / "categories.yaml"
    )}
    if category_id not in categories:
        raise ValueError(f"Categoría inexistente: {category_id}")
    target = categories[category_id]
    report = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "category_id": category_id, "url": target.url,
        "required_postal_code": "11590",
        "master_modified": False,
        "status": "ERROR", "context_verified": False,
    }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            if not browser.contexts:
                raise RuntimeError("Chrome CDP no expone contextos.")
            context = browser.contexts[0]
            pages = [
                page for page in context.pages
                if urlparse(page.url).netloc.lower() in {HOST, "yza.mx"}
            ]
            if not pages:
                raise RuntimeError(
                    "Abre www.yza.mx en Chrome CDP antes de ejecutar el probe."
                )
            page = pages[0]
            page.goto(target.url, wait_until="domcontentloaded", timeout=75000)
            page.wait_for_timeout(1200)
            report["site_title"] = page.title()
            report["landing_url"] = page.url
            evidence = apply_postal_code(page)
            report["context_verified"] = bool(evidence)
            report["context_evidence"] = evidence
            cards, last_page = discover_cards(page)
            report["visible_product_links"] = len(cards)
            report["pager_last_page"] = last_page
            report["samples"] = cards[:3]
            report["status"] = (
                "READY_FOR_FULL_CAPTURE" if evidence and cards and last_page
                else "NEEDS_REVIEW"
            )
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / (
        "probe_" + category_id + "_"
        + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json"
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print("YZA PROBE | " + category_id)
    print("CP requerido       : 11590")
    print("CP confirmado      : " + str(report["context_verified"]))
    print("Tarjetas visibles  : " + str(report.get("visible_product_links", 0)))
    print("Ultima pagina      : " + str(report.get("pager_last_page")))
    print("Estado             : " + report["status"])
    if report.get("error"):
        print("Detalle            : " + report["error"])
    print("Reporte            : " + str(report_path))
    print("MASTER: SIN CAMBIOS")
    return 0 if report["status"] == "READY_FOR_FULL_CAPTURE" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnóstico YZA sin integración")
    parser.add_argument(
        "--category", choices=("medicamentos-respiratorio", "higiene-bucal"),
        default="higiene-bucal",
    )
    parser.add_argument("--cdp-url", required=True)
    args = parser.parse_args()
    return probe(args.category, args.cdp_url)


if __name__ == "__main__":
    raise SystemExit(main())
