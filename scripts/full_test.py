from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import yaml
from openpyxl.styles import Font

from main import COLUMNS

OUTPUT = Path("output/concentrado_scraper.xlsx")
LOG_DIR = Path("diagnostics/full_test")


def load_enabled_categories(retailer: str) -> list[dict]:
    path = Path(f"config/{retailer}/categories.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [x for x in data.get("categories", []) if x.get("enabled", True)]


def status_from_code(code: int, text: str) -> str:
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


def nonempty_count(series: pd.Series) -> int:
    normalized = series.fillna("").astype(str).str.strip()
    return int(normalized.ne("").sum())


def run_case(retailer: str, category: dict) -> dict:
    category_id = category["id"]
    if retailer == "walmart":
        location_id = "sc-toreo"
        cmd = [sys.executable, "main.py", "--retailer", retailer, "--category", category_id, "--store", location_id]
    else:
        location_id = "cdmx"
        cmd = [sys.executable, "main.py", "--retailer", retailer, "--category", category_id, "--location", location_id]

    proc = subprocess.run(cmd, text=True, capture_output=True)
    combined_log = f"$ {' '.join(cmd)}\n\nSTDOUT\n{proc.stdout}\n\nSTDERR\n{proc.stderr}\n"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / f"{retailer}_{category_id}.log").write_text(combined_log, encoding="utf-8")

    text = f"{proc.stdout}\n{proc.stderr}"
    match = re.search(r"Productos únicos:\s*(\d+)", text)
    reported_products = int(match.group(1)) if match else 0

    return {
        "retailer": retailer.capitalize(),
        "department": category.get("department"),
        "category": category.get("name"),
        "subcategory": category.get("subcategory"),
        "sub_subcategory": category.get("sub_subcategory"),
        "category_id": category_id,
        "location_id": location_id,
        "store": "SC Toreo" if retailer == "walmart" else None,
        "status": status_from_code(proc.returncode, text),
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


def apply_quality_metrics(results: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    if OUTPUT.exists():
        concentrated = pd.read_excel(OUTPUT, sheet_name="Concentrado", dtype={"sku": str, "store_id": str})
    else:
        concentrated = pd.DataFrame(columns=COLUMNS)

    for result in results:
        retailer_mask = concentrated.get("retailer", pd.Series(dtype=str)).astype(str).str.casefold().eq(result["retailer"].casefold())
        category_mask = concentrated.get("category_id", pd.Series(dtype=str)).astype(str).eq(result["category_id"])
        subset = concentrated.loc[retailer_mask & category_mask].copy()
        if subset.empty:
            continue
        result["products"] = len(subset)
        result["sku_complete"] = nonempty_count(subset["sku"])
        result["price_current_complete"] = int(subset["price_current"].notna().sum())
        result["price_regular_complete"] = int(subset["price_regular"].notna().sum())
        result["url_complete"] = nonempty_count(subset["url"])
        result["duplicates_sku_url"] = int(subset.duplicated(subset=["sku", "url"]).sum())
        if "store_context_verified" in subset.columns:
            result["store_context_verified"] = int(subset["store_context_verified"].fillna(False).astype(bool).sum())

    summary = pd.DataFrame(results)
    return concentrated, summary


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
            for col_cells in ws.columns:
                values = [str(c.value) if c.value is not None else "" for c in col_cells[:250]]
                width = min(max(max((len(v) for v in values), default=0) + 2, 10), 42)
                ws.column_dimensions[col_cells[0].column_letter].width = width


def main() -> int:
    OUTPUT.unlink(missing_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    cases: list[tuple[str, dict]] = []
    for retailer in ("soriana", "walmart"):
        for category in load_enabled_categories(retailer):
            cases.append((retailer, category))

    results = []
    print(f"Casos configurados: {len(cases)}")
    for i, (retailer, category) in enumerate(cases, start=1):
        print(f"[{i}/{len(cases)}] {retailer} / {category['id']}")
        result = run_case(retailer, category)
        results.append(result)
        print(f"  -> {result['status']} (exit={result['exit_code']}, reported={result['reported_products']})")

    concentrated, summary = apply_quality_metrics(results)
    write_final_workbook(concentrated, summary)

    summary.to_csv(LOG_DIR / "summary.csv", index=False)
    print("\nRESUMEN FINAL")
    print(summary[["retailer", "category_id", "status", "products", "sku_complete", "price_current_complete", "url_complete", "duplicates_sku_url"]].to_string(index=False))
    print(f"\nFilas concentradas: {len(concentrated)}")
    if not concentrated.empty:
        print(f"SKU globales únicos: {concentrated['sku'].astype(str).nunique()}")
    print(f"Archivo final: {OUTPUT}")

    unexpected = summary[summary["status"].isin(["ERROR", "EMPTY", "STORE_CONTEXT_ERROR"])]
    return 1 if not unexpected.empty else 0


if __name__ == "__main__":
    raise SystemExit(main())
