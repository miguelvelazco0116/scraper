from __future__ import annotations

import argparse
import re
import subprocess
import sys
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
    if code == 3:
        return "EMPTY"
    return "ERROR"


def run_case(
    retailer: str,
    category: dict,
    *,
    walmart_profile_dir: Path | None = None,
    walmart_storage_state: Path | None = None,
) -> dict:
    category_id = category["id"]
    if retailer == "walmart":
        cmd = [
            sys.executable,
            "main.py",
            "--retailer",
            "walmart",
            "--category",
            category_id,
            "--store",
            "sc-toreo",
        ]
        if walmart_storage_state is not None:
            cmd.extend(["--storage-state", str(walmart_storage_state)])
        elif walmart_profile_dir is not None:
            cmd.extend(["--profile-dir", str(walmart_profile_dir), "--headed"])
        location = "sc-toreo"
        store = "SC Toreo"
    elif retailer == "chedraui":
        cmd = [
            sys.executable,
            "main.py",
            "--retailer",
            "chedraui",
            "--category",
            category_id,
            "--store",
            "chedraui-polanco",
        ]
        location = "chedraui-polanco"
        store = "Chedraui Selecto México Polanco"
    elif retailer == "farmacias-guadalajara":
        cmd = [
            sys.executable,
            "main.py",
            "--retailer",
            "farmacias-guadalajara",
            "--category",
            category_id,
            "--location",
            "fg-online",
        ]
        location = "fg-online"
        store = None
    else:
        cmd = [
            sys.executable,
            "main.py",
            "--retailer",
            "soriana",
            "--category",
            category_id,
            "--location",
            "cdmx",
        ]
        location = "cdmx"
        store = None

    proc = subprocess.run(cmd, text=True, capture_output=True)
    text = f"{proc.stdout}\n{proc.stderr}"
    status = classify_result(proc.returncode, text)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{retailer}_{category_id}.log"
    log_path.write_text(
        f"$ {' '.join(cmd)}\n\nSTDOUT\n{proc.stdout}\n\nSTDERR\n{proc.stderr}\n",
        encoding="utf-8",
    )

    match = re.search(r"Productos únicos:\s*(\d+)", text)
    reported_products = int(match.group(1)) if match else 0
    display_names = {
        "soriana": "Soriana",
        "chedraui": "Chedraui",
        "walmart": "Walmart",
        "farmacias-guadalajara": "Farmacias Guadalajara",
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
    }


def count_nonempty(series: pd.Series) -> int:
    return int(series.fillna("").astype(str).str.strip().ne("").sum())


def apply_quality(results: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    if OUTPUT.exists():
        concentrated = pd.read_excel(
            OUTPUT,
            sheet_name="Concentrado",
            dtype={"sku": str, "store_id": str},
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
        result["duplicates_sku_url"] = int(subset.duplicated(subset=["sku", "url"]).sum())
        if "store_context_verified" in subset.columns:
            values = subset["store_context_verified"].fillna(False).astype(bool)
            result["store_context_verified"] = int(values.sum())

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
    parser = argparse.ArgumentParser(description="Ejecutar todos los retailers y categorías configuradas")
    parser.add_argument("--walmart-profile-dir")
    parser.add_argument("--walmart-storage-state")
    args = parser.parse_args()

    walmart_profile = Path(args.walmart_profile_dir).expanduser().resolve() if args.walmart_profile_dir else None
    walmart_state = Path(args.walmart_storage_state).expanduser().resolve() if args.walmart_storage_state else None
    if walmart_state is not None and not walmart_state.exists():
        raise SystemExit(f"Storage state Walmart no encontrado: {walmart_state}")
    if walmart_profile is not None and not walmart_profile.exists():
        raise SystemExit(f"Perfil Walmart no encontrado: {walmart_profile}")
    if walmart_state is None and walmart_profile is None:
        raise SystemExit("Debes proporcionar --walmart-storage-state o --walmart-profile-dir")

    OUTPUT.unlink(missing_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    cases: list[tuple[str, dict]] = []
    for retailer in ("soriana", "chedraui", "farmacias-guadalajara", "walmart"):
        cases.extend((retailer, category) for category in load_enabled_categories(retailer))

    results: list[dict] = []
    print(f"Casos configurados: {len(cases)}")
    for index, (retailer, category) in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {retailer} / {category['id']}")
        result = run_case(
            retailer,
            category,
            walmart_profile_dir=walmart_profile,
            walmart_storage_state=walmart_state,
        )
        results.append(result)
        print(f"  -> {result['status']} (exit={result['exit_code']}, reported={result['reported_products']})")

    concentrated, summary = apply_quality(results)
    write_final_workbook(concentrated, summary)
    summary.to_csv(LOG_DIR / "summary.csv", index=False, encoding="utf-8-sig")

    print("\nRESUMEN FINAL")
    columns = [
        "retailer", "category_id", "status", "products", "sku_complete",
        "price_current_complete", "url_complete", "duplicates_sku_url",
        "store_context_verified",
    ]
    print(summary[columns].to_string(index=False))
    print(f"\nFilas concentradas: {len(concentrated)}")
    print(f"Archivo final: {OUTPUT.resolve()}")

    failures = summary[summary["status"] != "SUCCESS"]
    return 0 if failures.empty else 2


if __name__ == "__main__":
    raise SystemExit(main())
