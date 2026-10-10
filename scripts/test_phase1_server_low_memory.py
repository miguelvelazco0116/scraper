from __future__ import annotations

import gc
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.config import load_categories, load_locations
from scraper.retailers.chedraui_polanco_api import ChedrauiScraper
from scraper.retailers.ibarra_mayoreo import IbarraMayoreoScraper


def _print_memory(label: str) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        state = MEMORYSTATUSEX()
        state.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
            total_mb = round(state.ullTotalPhys / (1024 * 1024))
            free_mb = round(state.ullAvailPhys / (1024 * 1024))
            used_mb = total_mb - free_mb
            print(
                f"MEMORIA {label}: used={used_mb}MB "
                f"free={free_mb}MB total={total_mb}MB "
                f"load={state.dwMemoryLoad}%"
            )
    except Exception as exc:
        print(f"MEMORIA {label}: no disponible ({type(exc).__name__})")


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
    _print_memory("ANTES")

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
    ibarra_unit_count_complete = sum(
        row.get("units_per_package") is not None
        for row in ibarra_rows
    )
    ibarra_unit_price_complete = sum(
        row.get("price_per_unit") is not None
        for row in ibarra_rows
    )
    ibarra_ok = (
        bool(ibarra_rows)
        and bool(ibarra_meta.get("discovery_complete"))
        and str(ibarra_meta.get("status")) == "SUCCESS"
        and ibarra_unit_count_complete == len(ibarra_rows)
        and ibarra_unit_price_complete == len(ibarra_rows)
    )
    print(
        f"IBARRA: {'PASS' if ibarra_ok else 'FAIL'} "
        f"rows={len(ibarra_rows)} "
        f"units={ibarra_unit_count_complete}/{len(ibarra_rows)} "
        f"unit_price={ibarra_unit_price_complete}/{len(ibarra_rows)} "
        f"status={ibarra_meta.get('status')}"
    )
    del ibarra
    gc.collect()
    time.sleep(2)
    _print_memory("DESPUES_IBARRA")

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
    del chedraui
    gc.collect()
    time.sleep(2)
    _print_memory("DESPUES_CHEDRAUI")

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
