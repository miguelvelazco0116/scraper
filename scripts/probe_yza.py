"""Read-only YZA online-catalogue probe; location checks are disabled."""
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
from scraper.retailers.yza import (
    BLOCKED_MARKERS, click_catalogue_page, discover_cards,
    open_online_category, yza_browser_page,
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
        "postal_verification_required": False,
        "postal_code": None,
        "store_context_verified": False,
        "scope": "ONLINE_CATALOGUE_NO_LOCATION_VERIFICATION",
        "master_modified": False,
        "status": "ERROR",
    }
    try:
        with sync_playwright() as playwright:
            with yza_browser_page(playwright, cdp_url) as page:
                report["browser_mode"] = (
                    "operator_prepared_cdp" if cdp_url else "automatic_headed"
                )
                if cdp_url:
                    open_online_category(page, target.url)
                else:
                    page.goto(target.url, wait_until="domcontentloaded", timeout=75000)
                    page.wait_for_timeout(1200)
                report["site_title"] = page.title()
                report["landing_url"] = page.url
                cards, last_page = discover_cards(page)
                report["visible_product_links"] = len(cards)
                report["pager_last_page"] = last_page
                report["samples"] = cards[:3]
                # A first-page-only probe falsely passed when ?p=2 just
                # reloaded page 1. Validate the REAL page 2 button too.
                next_page_ok = last_page == 1
                if cards and last_page and last_page > 1:
                    try:
                        click_catalogue_page(page, 2, cards)
                        page_two, page_two_last = discover_cards(page)
                        previous_urls = {x["url"] for x in cards}
                        new_urls = {x["url"] for x in page_two} - previous_urls
                        report["page_two_links"] = len(page_two)
                        report["page_two_new_links"] = len(new_urls)
                        report["page_two_last_page"] = page_two_last
                        next_page_ok = bool(new_urls) and page_two_last == last_page
                    except Exception as exc:
                        report["pagination_error"] = (
                            f"{type(exc).__name__}: {exc}"
                        )
                report["pagination_next_verified"] = next_page_ok
                text = page.locator("body").inner_text(timeout=10000).lower()
                if any(marker in text for marker in BLOCKED_MARKERS):
                    report["status"] = "BLOCKED"
                    report["error"] = "El sitio mostró un bloqueo o desafío de verificación."
                else:
                    report["status"] = (
                        "READY_FOR_FULL_CAPTURE" if cards and last_page and next_page_ok
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
    print("Localidad          : SIN VERIFICACION (catalogo online)")
    print("CP obligatorio     : No")
    print("Contexto geográfico: NO CERTIFICADO")
    print("Tarjetas visibles  : " + str(report.get("visible_product_links", 0)))
    print("Ultima pagina      : " + str(report.get("pager_last_page")))
    print("Pagina 2 verificada: " + str(report.get("pagination_next_verified")))
    if report.get("page_two_new_links") is not None:
        print("Nuevos en pagina 2: " + str(report["page_two_new_links"]))
    if report.get("pagination_error"):
        print("Error de paginacion: " + report["pagination_error"])
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
    parser.add_argument("--cdp-url", required=True, help="Chrome previamente abierto por el operador; sin CP obligatorio.")
    args = parser.parse_args()
    return probe(args.category, args.cdp_url)


if __name__ == "__main__":
    raise SystemExit(main())
