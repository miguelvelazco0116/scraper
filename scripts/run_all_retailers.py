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
    if code == 3:
        return "EMPTY"
    return "ERROR"


def run_case(
    retailer: str,
    category: dict,
    *,
    walmart_profile_dir: Path | None = None,
    walmart_storage_state: Path | None = None,
    soriana_profile_dir: Path | None = None,
    chedraui_profile_dir: Path | None = None,
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
            sys.executable, "main.py", "--retailer", "farmacias-del-ahorro", "--category", category_id,
            "--location", "fahorro-online",
        ]
        location = "fahorro-online"
        store = None
    elif retailer == "farmacias-san-pablo":
        cmd = [
            sys.executable, "main.py", "--retailer", "farmacias-san-pablo", "--category", category_id,
            "--location", "san-pablo-online", "--headed",
        ]
        location = "san-pablo-online"
        store = None
    elif retailer == "farmacias-similares":
        cmd = [
            sys.executable, "main.py", "--retailer", "farmacias-similares",
            "--category", category_id, "--location", "similares-online",
        ]
        if local_browser:
            cmd.extend(["--headed", "--browser-channel", "chrome"])
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
            sys.executable, "main.py", "--retailer", "ibarra-mayoreo",
            "--category", category_id, "--location", "ibarra-online",
        ]
        if local_browser:
            cmd.extend(["--headed", "--browser-channel", "chrome"])
        location = "ibarra-online"
        store = "Ibarra Mayoreo online"
    elif retailer == "bodega-aurrera":
        cmd = [
            sys.executable, "main.py", "--retailer", "bodega-aurrera",
            "--category", category_id, "--location", "bodega-aurrera-online",
        ]
        if local_browser:
            cmd.extend(["--headed", "--browser-channel", "chrome"])
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
            print(f"     {attempt_status}; reintentando una vez...")

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
    reported_products = int(match.group(1)) if match else 0
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
        "quality_status": "PENDING",
        "quality_notes": "",
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
            continue

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

        result["quality_status"] = "COMPLETE" if not notes else "REVIEW"
        result["quality_notes"] = "; ".join(notes)

    return concentrated, pd.DataFrame(results)


def write_final_workbook(concentrated: pd.DataFrame, summary: pd.DataFrame) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        concentrated.to_excel(writer, index=False, sheet_name="Concentrado")
        summary.to_excel(writer, index=False, sheet_name="Resumen")
        wb = writer.book
        for sheet_name in ("Concentrado", "Resumen"):
            ws = wb[sheet_name]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for cells in ws.columns:
                sample = [str(c.value) if c.value is not None else "" for c in cells[:250]]
                width = min(max(max((len(v) for v in sample), default=0) + 2, 10), 42)
                ws.column_dimensions[cells[0].column_letter].width = width


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
        default=60,
        help="Pausa entre categorías consecutivas de Soriana.",
    )
    parser.add_argument(
        "--soriana-retry-delay-seconds",
        type=int,
        default=300,
        help="Pausa antes del reintento final de categorías Soriana bloqueadas.",
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
    chedraui_profile = Path(args.chedraui_profile_dir).expanduser().resolve()
    soriana_profile.mkdir(parents=True, exist_ok=True)
    chedraui_profile.mkdir(parents=True, exist_ok=True)
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

    cases: list[tuple[str, dict]] = []
    for retailer in retailers:
        cases.extend(
            (retailer, category)
            for category in load_enabled_categories(retailer)
        )

    results: list[dict] = []
    print(f"Casos configurados: {len(cases)}")
    for index, (retailer, category) in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {retailer} / {category['id']}")
        result = run_case(
            retailer,
            category,
            walmart_profile_dir=walmart_profile,
            walmart_storage_state=walmart_state,
            soriana_profile_dir=soriana_profile,
            chedraui_profile_dir=chedraui_profile,
            local_browser=args.local_browser,
        )
        results.append(result)
        print(f"  -> {result['status']} (exit={result['exit_code']}, reported={result['reported_products']})")
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

        # Soriana ha mostrado bloqueos intermitentes cuando se consultan
        # varias categorías consecutivas. Una pausa fija reduce presión sobre
        # el storefront sin intentar evadir controles de acceso.
        if (
            retailer == "soriana"
            and index < len(cases)
            and cases[index][0] == "soriana"
            and args.soriana_delay_seconds > 0
        ):
            print(
                f"     pausa Soriana: {args.soriana_delay_seconds}s antes de la siguiente categoría..."
            )
            time.sleep(args.soriana_delay_seconds)

    # Las categorías Soriana bloqueadas se reintentan una sola vez al final
    # de la corrida, cuando la sesión ha tenido tiempo de enfriarse mientras
    # se procesan los demás retailers.
    blocked_soriana = [
        idx
        for idx, result in enumerate(results)
        if result["retailer"] == "Soriana" and result["status"] == "BLOCKED"
    ]
    if blocked_soriana:
        delay = max(0, args.soriana_retry_delay_seconds)
        print(
            f"\nREINTENTO FINAL SORIANA: {len(blocked_soriana)} categoría(s) bloqueada(s)"
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
                local_browser=args.local_browser,
            )
            results[result_index] = retry_result
            print(
                f"  -> {retry_result['status']} "
                f"(exit={retry_result['exit_code']}, "
                f"reported={retry_result['reported_products']})"
            )

            if (
                retry_number < len(blocked_soriana)
                and args.soriana_delay_seconds > 0
            ):
                print(
                    f"     pausa Soriana: {args.soriana_delay_seconds}s antes del siguiente retry..."
                )
                time.sleep(args.soriana_delay_seconds)

    concentrated, summary = apply_quality(results)
    write_final_workbook(concentrated, summary)
    summary.to_csv(LOG_DIR / "summary.csv", index=False, encoding="utf-8-sig")

    print("\nRESUMEN FINAL")
    columns = [
        "retailer", "category_id", "status", "quality_status", "products",
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
