from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import yaml
from openpyxl.styles import Font

from main import update_consolidated_output
from scraper.io_utils import atomic_output_path, exclusive_file_lock
from scraper.config import load_categories, load_locations
from scraper.retailers.soriana import SorianaScraper

OUTPUT = Path("output/concentrado_scraper.xlsx")
LOG_DIR = Path("diagnostics/run_all")


def load_enabled_categories(retailer: str) -> list[dict]:
    path = Path(f"config/{retailer}/categories.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [item for item in data.get("categories", []) if item.get("enabled", True)]


def classify_result(code: int, text: str) -> str:
    lower = text.lower()
    if code == 0:
        return "SUCCESS"
    if code == 2 or "blocked:" in lower:
        return "BLOCKED"
    if code == 4 or "store_context_error" in lower:
        return "STORE_CONTEXT_ERROR"
    if code == 5 or "network_unavailable:" in lower:
        return "NETWORK_UNAVAILABLE"
    if code == 6 or "deferred:" in lower:
        return "DEFERRED"
    if code == 3:
        return "EMPTY"
    if code == 7 or "quality_gate: fail" in lower:
        return "PARTIAL"
    return "ERROR"


def run_soriana_batch(
    categories: list[dict],
    *,
    profile_dir: Path,
    local_browser: bool,
    delay_seconds: int,
) -> list[dict]:
    """Ejecuta una tanda Soriana dentro de una única sesión persistente."""
    configured = {
        item.id: item
        for item in load_categories("config/soriana/categories.yaml")
    }
    location = next(
        item
        for item in load_locations("config/locations.yaml")
        if item.id == "cdmx"
    )
    category_objects = [
        configured[item["id"]]
        for item in categories
    ]

    scraper = SorianaScraper(
        headless=not local_browser,
        browser_channel="chrome" if local_browser else None,
        diagnostics_dir=LOG_DIR / "soriana_batch",
        profile_dir=profile_dir,
        circuit_cooldown_seconds=600,
    )
    raw_results = scraper.scrape_categories(
        category_objects,
        location,
        delay_seconds=delay_seconds,
        stop_after_block=True,
    )

    output: list[dict] = []
    status_codes = {
        "SUCCESS": 0,
        "EMPTY": 3,
        "BLOCKED": 2,
        "DEFERRED": 6,
        "ERROR": 1,
    }

    for category, raw in zip(categories, raw_results):
        status = str(raw.get("status") or "ERROR")
        rows = raw.get("rows") or []
        error = raw.get("error")

        if status == "SUCCESS" and rows:
            update_consolidated_output(pd.DataFrame(rows), OUTPUT)

        log_path = LOG_DIR / f"soriana_{category['id']}.log"
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            "\n".join(
                [
                    "MODE single_persistent_batch",
                    f"STATUS {status}",
                    f"PRODUCTS {len(rows)}",
                    f"ERROR {error or ''}",
                    f"PROFILE {profile_dir}",
                ]
            ),
            encoding="utf-8",
        )

        output.append(
            {
                "retailer": "Soriana",
                "department": category.get("department"),
                "category": category.get("name"),
                "subcategory": category.get("subcategory"),
                "sub_subcategory": category.get("sub_subcategory"),
                "category_id": category["id"],
                "location_id": "cdmx",
                "store": None,
                "status": status,
                "exit_code": status_codes.get(status, 1),
                "reported_products": len(rows),
                "target_products": None,
                "coverage": None,
                "scrapy_quality_status": None,
                "products": 0,
                "sku_complete": 0,
                "price_current_complete": 0,
                "price_regular_complete": 0,
                "url_complete": 0,
                "duplicates_sku_url": 0,
                "store_context_verified": 0,
                "available_products": 0,
                "unavailable_products": 0,
                "availability_unknown": 0,
                "price_required_products": 0,
                "price_required_complete": 0,
                "data_status": "MISSING",
                "quality_status": "PENDING",
                "quality_notes": error or "",
            }
        )

    return output


def run_case(
    retailer: str,
    category: dict,
    *,
    walmart_profile_dir: Path | None = None,
    walmart_storage_state: Path | None = None,
    soriana_profile_dir: Path | None = None,
    chedraui_profile_dir: Path | None = None,
    similares_profile_dir: Path | None = None,
    san_pablo_profile_dir: Path | None = None,
    san_pablo_debugger_address: str | None = None,
    bodega_profile_dir: Path | None = None,
    local_browser: bool = False,
) -> dict:
    category_id = category["id"]
    if retailer == "walmart":
        cmd = [
            sys.executable, "main.py", "--retailer", "walmart", "--category", category_id,
            "--store", "sc-toreo",
        ]
        if walmart_storage_state is not None:
            cmd.extend(["--storage-state", str(walmart_storage_state)])
        elif walmart_profile_dir is not None:
            cmd.extend(["--profile-dir", str(walmart_profile_dir), "--headed"])
        location = "sc-toreo"
        store = "SC Toreo"
    elif retailer == "chedraui":
        cmd = [
            sys.executable, "main.py", "--retailer", "chedraui", "--category", category_id,
            "--store", "chedraui-polanco",
        ]
        if local_browser:
            cmd.extend(["--headed", "--browser-channel", "chrome"])
        if chedraui_profile_dir is not None:
            cmd.extend(["--profile-dir", str(chedraui_profile_dir)])
        location = "chedraui-polanco"
        store = "Chedraui Selecto México Polanco"
    elif retailer == "farmacias-guadalajara":
        cmd = [
            sys.executable, "main.py", "--retailer", "farmacias-guadalajara", "--category", category_id,
            "--location", "fg-online",
        ]
        location = "fg-online"
        store = None
    elif retailer == "farmacias-del-ahorro":
        cmd = [
            sys.executable,
            "scripts/run_scrapy.py",
            "--retailer", "farmacias-del-ahorro",
            "--category", category_id,
            "--location", "fahorro-online",
            "--update-consolidated",
        ]
        location = "fahorro-online"
        store = None
    elif retailer == "farmacias-san-pablo":
        cmd = [
            sys.executable,
            "main.py",
            "--retailer",
            "farmacias-san-pablo",
            "--category",
            category_id,
            "--location",
            "san-pablo-online",
        ]
        if san_pablo_debugger_address:
            cmd.extend(
                [
                    "--debugger-address",
                    san_pablo_debugger_address,
                ]
            )
        elif san_pablo_profile_dir is not None:
            cmd.extend(
                ["--profile-dir", str(san_pablo_profile_dir)]
            )
        location = "san-pablo-online"
        store = None
    elif retailer == "farmacias-similares":
        cmd = [
            sys.executable,
            "main.py",
            "--retailer",
            "farmacias-similares",
            "--category",
            category_id,
            "--location",
            "similares-online",
        ]
        if local_browser:
            cmd.extend(["--headed", "--browser-channel", "chrome"])
        if similares_profile_dir is not None:
            cmd.extend(
                ["--profile-dir", str(similares_profile_dir)]
            )
        location = "similares-online"
        store = "Farmacias Similares online"
    elif retailer == "la-comer":
        cmd = [
            sys.executable, "main.py", "--retailer", "la-comer", "--category", category_id,
            "--location", "la-comer-online-287",
        ]
        if local_browser:
            cmd.extend(["--headed", "--browser-channel", "chrome"])
        location = "la-comer-online-287"
        store = "La Comer online (succId 287)"
    elif retailer == "ibarra-mayoreo":
        cmd = [
            sys.executable,
            "scripts/run_scrapy.py",
            "--retailer", "ibarra-mayoreo",
            "--category", category_id,
            "--location", "ibarra-online",
            "--update-consolidated",
        ]
        location = "ibarra-online"
        store = "Ibarra Mayoreo online"
    elif retailer == "bodega-aurrera":
        cmd = [
            sys.executable,
            "main.py",
            "--retailer",
            "bodega-aurrera",
            "--category",
            category_id,
            "--location",
            "bodega-aurrera-online",
        ]
        if local_browser:
            cmd.extend(["--headed", "--browser-channel", "chrome"])
        if bodega_profile_dir is not None:
            cmd.extend(
                ["--profile-dir", str(bodega_profile_dir)]
            )
        location = "bodega-aurrera-online"
        store = "Bodega Aurrera online"
    else:
        cmd = [
            sys.executable, "main.py", "--retailer", "soriana", "--category", category_id,
            "--location", "cdmx",
        ]
        if local_browser:
            cmd.extend(["--headed", "--browser-channel", "chrome"])
        if soriana_profile_dir is not None:
            cmd.extend(["--profile-dir", str(soriana_profile_dir)])
        location = "cdmx"
        store = None

    attempts: list[tuple[int, str, str]] = []
    proc = None
    for attempt in range(1, 3):
        proc = subprocess.run(cmd, text=True, capture_output=True)
        attempts.append((proc.returncode, proc.stdout, proc.stderr))
        attempt_text = f"{proc.stdout}\n{proc.stderr}"
        attempt_status = classify_result(proc.returncode, attempt_text)
        retryable = attempt_status in {"NETWORK_UNAVAILABLE", "STORE_CONTEXT_ERROR"}
        if not retryable:
            break
        if attempt < 2:
            retry_delay = 45 if attempt_status == "STORE_CONTEXT_ERROR" else 15
            print(
                f"     {attempt_status}; reintentando una vez en "
                f"{retry_delay}s..."
            )
            time.sleep(retry_delay)

    assert proc is not None
    text = f"{proc.stdout}\n{proc.stderr}"
    status = classify_result(proc.returncode, text)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{retailer}_{category_id}.log"
    parts = []
    for attempt_index, (code, stdout, stderr) in enumerate(attempts, start=1):
        parts.append(
            f"ATTEMPT {attempt_index} exit={code}\n"
            f"$ {' '.join(cmd)}\n\nSTDOUT\n{stdout}\n\nSTDERR\n{stderr}\n"
        )
    log_path.write_text("\n".join(parts), encoding="utf-8")

    match = re.search(r"Productos únicos:\s*(\d+)", text)
    if match is None:
        match = re.search(
            r"SCRAPY_RESULT\s+products=(\d+)",
            text,
            flags=re.IGNORECASE,
        )
    if match is None:
        match = re.search(
            r"^Productos\s*:\s*(\d+)\s*$",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        )
    reported_products = int(match.group(1)) if match else 0

    scrapy_quality_match = re.search(
        r"^Status\s*:\s*([A-Z_]+)\s*$",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    scrapy_quality_status = (
        scrapy_quality_match.group(1).upper()
        if scrapy_quality_match
        else None
    )
    target_match = re.search(
        r"^Target\s*:\s*(\d+)\s*$",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    target_products = (
        int(target_match.group(1))
        if target_match
        else None
    )
    coverage_match = re.search(
        r"^Cobertura\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*$",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    coverage = (
        float(coverage_match.group(1))
        if coverage_match
        else None
    )

    display_names = {
        "soriana": "Soriana",
        "chedraui": "Chedraui",
        "walmart": "Walmart",
        "farmacias-guadalajara": "Farmacias Guadalajara",
        "farmacias-del-ahorro": "Farmacias del Ahorro",
        "farmacias-san-pablo": "Farmacias San Pablo",
        "farmacias-similares": "Farmacias Similares",
        "la-comer": "La Comer",
        "ibarra-mayoreo": "Ibarra Mayoreo",
        "bodega-aurrera": "Bodega Aurrera",
    }

    return {
        "retailer": display_names.get(retailer, retailer),
        "department": category.get("department"),
        "category": category.get("name"),
        "subcategory": category.get("subcategory"),
        "sub_subcategory": category.get("sub_subcategory"),
        "category_id": category_id,
        "location_id": location,
        "store": store,
        "status": status,
        "exit_code": proc.returncode,
        "reported_products": reported_products,
        "target_products": target_products,
        "coverage": coverage,
        "scrapy_quality_status": scrapy_quality_status,
        "products": 0,
        "sku_complete": 0,
        "price_current_complete": 0,
        "price_regular_complete": 0,
        "url_complete": 0,
        "duplicates_sku_url": 0,
        "store_context_verified": 0,
        "available_products": 0,
        "unavailable_products": 0,
        "availability_unknown": 0,
        "price_required_products": 0,
        "price_required_complete": 0,
        "data_status": "MISSING",
        "quality_status": "PENDING",
        "quality_notes": "",
    }


def deferred_result(retailer: str, category: dict, reason: str) -> dict:
    display_names = {
        "soriana": "Soriana",
        "chedraui": "Chedraui",
        "walmart": "Walmart",
        "farmacias-guadalajara": "Farmacias Guadalajara",
        "farmacias-del-ahorro": "Farmacias del Ahorro",
        "farmacias-san-pablo": "Farmacias San Pablo",
        "farmacias-similares": "Farmacias Similares",
        "la-comer": "La Comer",
        "ibarra-mayoreo": "Ibarra Mayoreo",
        "bodega-aurrera": "Bodega Aurrera",
    }
    return {
        "retailer": display_names.get(retailer, retailer),
        "department": category.get("department"),
        "category": category.get("name"),
        "subcategory": category.get("subcategory"),
        "sub_subcategory": category.get("sub_subcategory"),
        "category_id": category["id"],
        "location_id": "cdmx" if retailer == "soriana" else None,
        "store": None,
        "status": "DEFERRED",
        "exit_code": 0,
        "reported_products": 0,
        "target_products": None,
        "coverage": None,
        "scrapy_quality_status": None,
        "products": 0,
        "sku_complete": 0,
        "price_current_complete": 0,
        "price_regular_complete": 0,
        "url_complete": 0,
        "duplicates_sku_url": 0,
        "store_context_verified": 0,
        "available_products": 0,
        "unavailable_products": 0,
        "availability_unknown": 0,
        "price_required_products": 0,
        "price_required_complete": 0,
        "data_status": "MISSING",
        "quality_status": "PENDING",
        "quality_notes": reason,
    }


def count_nonempty(series: pd.Series) -> int:
    return int(series.fillna("").astype(str).str.strip().ne("").sum())


def apply_quality(results: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    if OUTPUT.exists():
        concentrated = pd.read_excel(
            OUTPUT, sheet_name="Concentrado", dtype={"sku": str, "store_id": str},
        )
    else:
        concentrated = pd.DataFrame()

    for result in results:
        if concentrated.empty:
            continue
        mask = (
            concentrated["retailer"].astype(str).str.casefold().eq(result["retailer"].casefold())
            & concentrated["category_id"].astype(str).eq(result["category_id"])
        )
        subset = concentrated.loc[mask].copy()
        if subset.empty:
            result["data_status"] = "MISSING"
            continue

        result["data_status"] = (
            "FRESH" if result["status"] == "SUCCESS" else "STALE_RETAINED"
        )
        result["products"] = len(subset)
        result["sku_complete"] = count_nonempty(subset["sku"])
        result["price_current_complete"] = int(subset["price_current"].notna().sum())
        result["price_regular_complete"] = int(subset["price_regular"].notna().sum())
        result["url_complete"] = count_nonempty(subset["url"])

        if "availability_status" not in subset.columns:
            subset["availability_status"] = "UNKNOWN"
        availability = (
            subset["availability_status"]
            .fillna("UNKNOWN")
            .astype(str)
            .str.strip()
            .str.upper()
            .replace("", "UNKNOWN")
        )
        result["available_products"] = int(availability.eq("AVAILABLE").sum())
        result["unavailable_products"] = int(availability.eq("UNAVAILABLE").sum())
        result["availability_unknown"] = int(availability.eq("UNKNOWN").sum())

        price_required = ~availability.eq("UNAVAILABLE")
        result["price_required_products"] = int(price_required.sum())
        result["price_required_complete"] = int(
            subset.loc[price_required, "price_current"].notna().sum()
        )
        sku_text = subset["sku"].fillna("").astype(str).str.strip()
        url_text = subset["url"].fillna("").astype(str).str.strip()
        valid_id = sku_text.ne("") | url_text.ne("")
        result["duplicates_sku_url"] = int(
            subset.loc[valid_id].duplicated(subset=["sku", "url"]).sum()
        )
        if "store_context_verified" in subset.columns:
            values = subset["store_context_verified"].map(
                lambda value: False if pd.isna(value) else bool(value)
            )
            result["store_context_verified"] = int(values.sum())

        notes: list[str] = []
        if result["products"] <= 0:
            notes.append("sin productos")
        if result["price_required_complete"] < result["price_required_products"]:
            notes.append(
                "precio disponible "
                f"{result['price_required_complete']}/{result['price_required_products']}"
            )

        # Los productos explícitamente UNAVAILABLE pueden no exponer PDP,
        # SKU o URL. Esos campos se exigen sólo para productos no agotados.
        identifier_required = ~availability.eq("UNAVAILABLE")
        identifier_required_count = int(identifier_required.sum())
        sku_required_complete = int(
            subset.loc[identifier_required, "sku"]
            .fillna("")
            .astype(str)
            .str.strip()
            .ne("")
            .sum()
        )
        url_required_complete = int(
            subset.loc[identifier_required, "url"]
            .fillna("")
            .astype(str)
            .str.strip()
            .ne("")
            .sum()
        )

        # En San Pablo SKU y URL siguen siendo métricas informativas.
        if result["retailer"] != "Farmacias San Pablo":
            if sku_required_complete < identifier_required_count:
                notes.append(
                    f"sku disponibles {sku_required_complete}/{identifier_required_count}"
                )
            if url_required_complete < identifier_required_count:
                notes.append(
                    f"url disponibles {url_required_complete}/{identifier_required_count}"
                )
            if result["duplicates_sku_url"] > 0:
                notes.append(f"duplicados {result['duplicates_sku_url']}")

        if result["data_status"] == "STALE_RETAINED":
            notes.insert(
                0,
                f"última muestra retenida; intento={result['status']}",
            )
            result["quality_status"] = "STALE"
        elif notes:
            result["quality_status"] = "REVIEW"
        elif result.get("scrapy_quality_status") in {
            "COMPLETE",
            "SAMPLE_ACCEPTED",
        }:
            result["quality_status"] = result[
                "scrapy_quality_status"
            ]
        else:
            result["quality_status"] = "COMPLETE"
        result["quality_notes"] = "; ".join(notes)

    return concentrated, pd.DataFrame(results)


def write_final_workbook(concentrated: pd.DataFrame, summary: pd.DataFrame) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output_path(OUTPUT) as temporary_output:
        with pd.ExcelWriter(temporary_output, engine="openpyxl") as writer:
            concentrated.to_excel(
                writer,
                index=False,
                sheet_name="Concentrado",
            )
            summary.to_excel(
                writer,
                index=False,
                sheet_name="Resumen",
            )
            wb = writer.book
            for sheet_name in ("Concentrado", "Resumen"):
                ws = wb[sheet_name]
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions
                for cell in ws[1]:
                    cell.font = Font(bold=True)
                for cells in ws.columns:
                    sample = [
                        str(cell.value)
                        if cell.value is not None
                        else ""
                        for cell in cells[:250]
                    ]
                    width = min(
                        max(
                            max(
                                (len(value) for value in sample),
                                default=0,
                            )
                            + 2,
                            10,
                        ),
                        42,
                    )
                    ws.column_dimensions[
                        cells[0].column_letter
                    ].width = width


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ejecutar retailers activos y categorías configuradas"
    )
    parser.add_argument("--walmart-profile-dir")
    parser.add_argument("--walmart-storage-state")
    parser.add_argument(
        "--soriana-profile-dir",
        default=".soriana_profile",
        help="Perfil persistente local de Soriana.",
    )
    parser.add_argument(
        "--chedraui-profile-dir",
        default=".chedraui_profile",
        help="Perfil persistente local de Chedraui.",
    )
    parser.add_argument(
        "--similares-profile-dir",
        default=".similares_profile",
        help="Perfil persistente local de Farmacias Similares.",
    )
    parser.add_argument(
        "--san-pablo-profile-dir",
        default=".san_pablo_profile",
        help="Fallback de perfil si no se usa Chrome manual.",
    )
    parser.add_argument(
        "--san-pablo-debugger-address",
        default="127.0.0.1:9223",
        help="Chrome de San Pablo abierto manualmente.",
    )
    parser.add_argument(
        "--bodega-profile-dir",
        default=".bodega_aurrera_profile",
        help="Perfil persistente local de Bodega Aurrera.",
    )
    parser.add_argument(
        "--local-browser",
        action="store_true",
        help="Usa Google Chrome visible para retailers que requieren navegador local.",
    )
    parser.add_argument(
        "--include-paused",
        action="store_true",
        help="Incluye Walmart y Farmacias Guadalajara, actualmente en pausa.",
    )
    parser.add_argument(
        "--soriana-delay-seconds",
        type=int,
        default=120,
        help="Pausa entre categorías consecutivas de Soriana.",
    )
    parser.add_argument(
        "--soriana-retry-delay-seconds",
        type=int,
        default=600,
        help="Pausa antes del reintento final de categorías Soriana bloqueadas/diferidas.",
    )
    args = parser.parse_args()

    walmart_profile = (
        Path(args.walmart_profile_dir).expanduser().resolve()
        if args.walmart_profile_dir else None
    )
    walmart_state = (
        Path(args.walmart_storage_state).expanduser().resolve()
        if args.walmart_storage_state else None
    )
    soriana_profile = Path(args.soriana_profile_dir).expanduser().resolve()
    chedraui_profile = Path(
        args.chedraui_profile_dir
    ).expanduser().resolve()
    similares_profile = Path(
        args.similares_profile_dir
    ).expanduser().resolve()
    san_pablo_profile = Path(
        args.san_pablo_profile_dir
    ).expanduser().resolve()
    bodega_profile = Path(
        args.bodega_profile_dir
    ).expanduser().resolve()
    soriana_profile.mkdir(parents=True, exist_ok=True)
    chedraui_profile.mkdir(parents=True, exist_ok=True)
    similares_profile.mkdir(parents=True, exist_ok=True)
    san_pablo_profile.mkdir(parents=True, exist_ok=True)
    bodega_profile.mkdir(parents=True, exist_ok=True)
    if walmart_state is not None and not walmart_state.exists():
        raise SystemExit(f"Storage state Walmart no encontrado: {walmart_state}")
    if walmart_profile is not None and not walmart_profile.exists():
        raise SystemExit(f"Perfil Walmart no encontrado: {walmart_profile}")

    if args.include_paused and walmart_state is None and walmart_profile is None:
        raise SystemExit(
            "Para --include-paused debes proporcionar "
            "--walmart-storage-state o --walmart-profile-dir"
        )

    # No borrar el consolidado al inicio. Cada categoría exitosa reemplaza
    # sólo su propia muestra mediante main.py; si un retailer queda bloqueado,
    # se conserva la última captura válida y se marca como STALE_RETAINED.
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    active_retailers = [
        "soriana",
        "chedraui",
        "farmacias-del-ahorro",
        "farmacias-san-pablo",
        "ibarra-mayoreo",
        "bodega-aurrera",
        "farmacias-similares",
    ]
    paused_retailers = [
        "farmacias-guadalajara",
        "walmart",
    ]

    retailers = active_retailers + (paused_retailers if args.include_paused else [])

    print("Retailers activos:", ", ".join(active_retailers))
    print("Modo navegador:", "Chrome local visible" if args.local_browser else "Playwright por defecto")
    if not args.include_paused:
        print("En pausa: Farmacias Guadalajara, Walmart")
    print("")

    # Soriana se distribuye en tandas de 2 para evitar seis navegaciones
    # consecutivas al mismo storefront. Entre tandas se procesan otros
    # retailers, lo que da un enfriamiento natural a la sesión persistente.
    enabled_by_retailer = {
        retailer: load_enabled_categories(retailer)
        for retailer in retailers
    }

    cases: list[tuple[str, dict]] = []
    soriana_categories = enabled_by_retailer.get("soriana", [])
    soriana_batches = [
        soriana_categories[index:index + 2]
        for index in range(0, len(soriana_categories), 2)
    ]

    def add_cases(retailer_name: str) -> None:
        cases.extend(
            (retailer_name, category)
            for category in enabled_by_retailer.get(retailer_name, [])
        )

    if soriana_batches:
        cases.extend(("soriana", category) for category in soriana_batches[0])

    add_cases("chedraui")
    add_cases("farmacias-del-ahorro")

    if len(soriana_batches) > 1:
        cases.extend(("soriana", category) for category in soriana_batches[1])

    add_cases("farmacias-san-pablo")
    add_cases("ibarra-mayoreo")

    if len(soriana_batches) > 2:
        for batch in soriana_batches[2:]:
            cases.extend(("soriana", category) for category in batch)

    add_cases("bodega-aurrera")
    add_cases("farmacias-similares")

    if args.include_paused:
        add_cases("farmacias-guadalajara")
        add_cases("walmart")

    results: list[dict] = []

    print(f"Casos configurados: {len(cases)}")
    index = 0
    while index < len(cases):
        retailer, category = cases[index]

        if retailer == "soriana":
            batch: list[dict] = []
            batch_start = index
            while (
                index < len(cases)
                and cases[index][0] == "soriana"
                and len(batch) < 2
            ):
                batch.append(cases[index][1])
                index += 1

            names = ", ".join(item["id"] for item in batch)
            print(
                f"[{batch_start + 1}-{index}/{len(cases)}] "
                f"soriana batch / {names}"
            )
            batch_results = run_soriana_batch(
                batch,
                profile_dir=soriana_profile,
                local_browser=args.local_browser,
                delay_seconds=args.soriana_delay_seconds,
            )
            for result in batch_results:
                results.append(result)
                print(
                    f"  -> soriana / {result['category_id']}: "
                    f"{result['status']} "
                    f"(reported={result['reported_products']})"
                )
            continue

        print(f"[{index + 1}/{len(cases)}] {retailer} / {category['id']}")
        result = run_case(
            retailer,
            category,
            walmart_profile_dir=walmart_profile,
            walmart_storage_state=walmart_state,
            soriana_profile_dir=soriana_profile,
            chedraui_profile_dir=chedraui_profile,
            similares_profile_dir=similares_profile,
            san_pablo_profile_dir=san_pablo_profile,
            san_pablo_debugger_address=(
                args.san_pablo_debugger_address
            ),
            bodega_profile_dir=bodega_profile,
            local_browser=args.local_browser,
        )
        results.append(result)
        print(
            f"  -> {result['status']} "
            f"(exit={result['exit_code']}, "
            f"reported={result['reported_products']})"
        )

        if result["status"] == "ERROR":
            log_path = LOG_DIR / f"{retailer}_{category['id']}.log"
            try:
                lines = log_path.read_text(encoding="utf-8").splitlines()
                useful = [
                    line for line in lines
                    if "Traceback" in line
                    or "Error" in line
                    or "Exception" in line
                    or "RuntimeError" in line
                    or "Timeout" in line
                ]
                if useful:
                    print("     " + " | ".join(useful[-3:]))
            except Exception:
                pass

        index += 1

    # Las categorías Soriana bloqueadas se reintentan una sola vez al final
    # de la corrida, cuando la sesión ha tenido tiempo de enfriarse mientras
    # se procesan los demás retailers.
    blocked_soriana = [
        idx
        for idx, result in enumerate(results)
        if result["retailer"] == "Soriana"
        and result["status"] in {"BLOCKED", "DEFERRED"}
    ]
    if blocked_soriana:
        delay = max(0, args.soriana_retry_delay_seconds)
        print(
            f"\nREINTENTO FINAL SORIANA: {len(blocked_soriana)} categoría(s) bloqueada(s)/diferida(s)"
        )
        if delay:
            print(f"Pausa previa: {delay}s")
            time.sleep(delay)

        for retry_number, result_index in enumerate(blocked_soriana, start=1):
            retailer, category = cases[result_index]
            print(
                f"[Soriana retry {retry_number}/{len(blocked_soriana)}] "
                f"{category['id']}"
            )
            retry_result = run_case(
                retailer,
                category,
                walmart_profile_dir=walmart_profile,
                walmart_storage_state=walmart_state,
                soriana_profile_dir=soriana_profile,
                chedraui_profile_dir=chedraui_profile,
                similares_profile_dir=similares_profile,
                san_pablo_profile_dir=san_pablo_profile,
                san_pablo_debugger_address=(
                    args.san_pablo_debugger_address
                ),
                bodega_profile_dir=bodega_profile,
                local_browser=args.local_browser,
            )
            results[result_index] = retry_result
            print(
                f"  -> {retry_result['status']} "
                f"(exit={retry_result['exit_code']}, "
                f"reported={retry_result['reported_products']})"
            )

            if retry_number < len(blocked_soriana):
                retry_spacing = max(
                    args.soriana_delay_seconds,
                    600 if retry_result["status"] == "BLOCKED" else 0,
                )
                if retry_spacing > 0:
                    print(
                        f"     pausa Soriana: {retry_spacing}s "
                        "antes del siguiente retry..."
                    )
                    time.sleep(retry_spacing)

    with exclusive_file_lock(OUTPUT):
        concentrated, summary = apply_quality(results)
        write_final_workbook(concentrated, summary)

    summary_path = LOG_DIR / "summary.csv"
    with atomic_output_path(summary_path) as temporary_summary:
        summary.to_csv(
            temporary_summary,
            index=False,
            encoding="utf-8-sig",
        )

    print("\nRESUMEN FINAL")
    columns = [
        "retailer", "category_id", "status", "data_status",
        "quality_status", "products", "target_products", "coverage",
        "sku_complete", "price_current_complete", "url_complete",
        "available_products", "unavailable_products", "availability_unknown",
        "duplicates_sku_url", "store_context_verified", "quality_notes",
    ]
    print(summary[columns].to_string(index=False))
    print(f"\nFilas concentradas: {len(concentrated)}")
    print(f"Archivo final: {OUTPUT.resolve()}")

    failures = summary[summary["status"] != "SUCCESS"]
    return 0 if failures.empty else 2


if __name__ == "__main__":
    raise SystemExit(main())
