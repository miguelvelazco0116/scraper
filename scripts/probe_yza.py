"""Read-only browser probe for YZA CP 11590. Does not scrape full catalogues."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.config import load_categories
from scraper.retailers.yza_postal_diagnostics import inspect_postal_ui
from scraper.retailers.yza import (
    BLOCKED_MARKERS, apply_postal_code, discover_cards, yza_browser_page,
)

REPORT_DIR = ROOT / "diagnostics" / "yza"


def probe(category_id: str, cdp_url: str = "") -> int:
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
            with yza_browser_page(playwright, cdp_url) as page:
                page.goto(target.url, wait_until="domcontentloaded", timeout=75000)
                page.wait_for_timeout(1200)
                report["site_title"] = page.title()
                report["landing_url"] = page.url
                evidence = apply_postal_code(page)
                report["context_verified"] = bool(evidence)
                report["context_evidence"] = evidence
                if not evidence:
                    # Inspect the actual address UI. A product-specific
                    # availability dialog must NOT be taken as proof that
                    # a delivery postcode was applied site-wide.
                    try:
                        report["postal_ui"] = inspect_postal_ui(page)
                    except Exception as diagnostic_error:
                        report["postal_ui_error"] = (
                            f"{type(diagnostic_error).__name__}: {diagnostic_error}"
                        )
                    try:
                        REPORT_DIR.mkdir(parents=True, exist_ok=True)
                        image_path = REPORT_DIR / (
                            "postal_ui_" + category_id + "_" +
                            datetime.now().strftime("%Y%m%d_%H%M%S") + ".png"
                        )
                        page.screenshot(path=str(image_path), full_page=False)
                        report["postal_ui_screenshot"] = str(image_path)
                    except Exception as diagnostic_error:
                        report["screenshot_error"] = (
                            f"{type(diagnostic_error).__name__}: {diagnostic_error}"
                        )
                cards, last_page = discover_cards(page)
                report["visible_product_links"] = len(cards)
                report["pager_last_page"] = last_page
                report["samples"] = cards[:3]
                text = page.locator("body").inner_text(timeout=10000).lower()
                if any(marker in text for marker in BLOCKED_MARKERS):
                    report["status"] = "BLOCKED"
                    report["error"] = "El sitio mostró un bloqueo o desafío de verificación."
                else:
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
    if report.get("postal_ui") is not None:
        diagnostic = report["postal_ui"]
        print("Campos de dirección: " + str(len(diagnostic.get("input_fields", []))))
        print("Controles ubicación: " + str(
            [x.get("text") for x in diagnostic.get("location_controls", [])]
        ))
        print("Formulario diagnóstico: solo metadata sin direcciones escritas")
    if report.get("postal_ui_screenshot"):
        print("Captura del modal : " + report["postal_ui_screenshot"])
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
    parser.add_argument("--cdp-url", default="", help="Chrome previamente abierto; sin él se lanza Chrome automáticamente.")
    args = parser.parse_args()
    return probe(args.category, args.cdp_url)


if __name__ == "__main__":
    raise SystemExit(main())
