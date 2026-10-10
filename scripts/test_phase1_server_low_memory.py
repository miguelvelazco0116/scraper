from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.config import load_categories, load_locations
from scraper.retailers.chedraui_polanco_api import ChedrauiScraper
from scraper.retailers.ibarra_mayoreo import IbarraMayoreoScraper


def main() -> int:
    locations = {
        item.id: item
        for item in load_locations(ROOT / "config" / "locations.yaml")
    }

    ibarra_category = next(
        item
        for item in load_categories(
            ROOT / "config" / "ibarra-mayoreo" / "categories.yaml"
        )
        if item.id == "detergentes-lavatrastes-jab-marca-propia"
    )
    chedraui_category = next(
        item
        for item in load_categories(
            ROOT / "config" / "chedraui" / "categories.yaml"
        )
        if item.id == "higiene-bucal"
    )

    print("=" * 72)
    print("SMOKE TEST SERVIDOR - FASE 1 LOW MEMORY")
    print("=" * 72)
    print("Modo       : headless + low-memory")
    print("Ibarra     : detergentes-lavatrastes-jab-marca-propia")
    print("Chedraui   : higiene-bucal")
    print("")

    ibarra = IbarraMayoreoScraper(
        headless=True,
        browser_channel="chrome",
        max_pages=5,
        wait_ms=700,
        low_memory=True,
        page_recycle_interval=25,
    )
    ibarra_rows = ibarra.scrape_category(
        ibarra_category,
        locations["ibarra-online"],
    )
    ibarra_meta = dict(ibarra.last_meta or {})
    ibarra_ok = (
        bool(ibarra_rows)
        and bool(ibarra_meta.get("discovery_complete"))
        and str(ibarra_meta.get("status")) == "SUCCESS"
    )
    print(
        f"IBARRA: {'PASS' if ibarra_ok else 'FAIL'} "
        f"rows={len(ibarra_rows)} "
        f"status={ibarra_meta.get('status')}"
    )

    chedraui = ChedrauiScraper(
        headless=True,
        browser_channel="chrome",
        profile_dir=ROOT / ".chedraui_profile",
        max_pages=100,
        low_memory=True,
    )
    chedraui_rows = chedraui.scrape_category(
        chedraui_category,
        locations["chedraui-polanco"],
    )
    meta = dict(chedraui.run_meta or {})
    target = meta.get("displayed_category_products")
    structured_missing = len(meta.get("structured_price_missing_pages") or [])
    pagination_gaps = len(meta.get("internal_pagination_gaps") or [])
    chedraui_ok = (
        bool(chedraui_rows)
        and target is not None
        and len(chedraui_rows) >= int(target)
        and bool(meta.get("store_context_verified"))
        and structured_missing == 0
        and pagination_gaps == 0
    )
    print(
        f"CHEDRAUI: {'PASS' if chedraui_ok else 'FAIL'} "
        f"rows={len(chedraui_rows)} target={target} "
        f"structured_missing={structured_missing} "
        f"pagination_gaps={pagination_gaps}"
    )

    print("")
    print("=" * 72)
    print(
        "RESULTADO SERVIDOR: "
        + ("PASS" if ibarra_ok and chedraui_ok else "FAIL")
    )
    print("=" * 72)

    return 0 if ibarra_ok and chedraui_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
