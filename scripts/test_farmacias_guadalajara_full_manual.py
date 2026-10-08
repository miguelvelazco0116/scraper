from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "farmacias_guadalajara_full_test.xlsx"
DIAG_DIR = ROOT / "diagnostics" / "farmacias_guadalajara_full"

CATEGORY_ORDER = [
    "vias-respiratorias",
    "lavanderia",
    "cuidado-bucal",
    "preservativos",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prueba completa de Farmacias Guadalajara sobre Chrome existente."
    )
    parser.add_argument("--cdp-url", required=True)
    args = parser.parse_args()

    import pandas as pd
    from openpyxl.styles import Font
    from main import COLUMNS

    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    temp_dir = ROOT / "output" / "_fg_full_test"
    temp_dir.mkdir(parents=True, exist_ok=True)

    frames = []
    summaries = []

    print("=" * 82)
    print("FARMACIAS GUADALAJARA - PRUEBA COMPLETA")
    print("=" * 82)
    print(f"Categorías : {', '.join(CATEGORY_ORDER)}")
    print(f"CDP        : {args.cdp_url}")
    print("")

    for index, category_id in enumerate(CATEGORY_ORDER, start=1):
        print("-" * 82)
        print(f"[{index}/{len(CATEGORY_ORDER)}] {category_id}")

        category_output = temp_dir / f"{category_id}.xlsx"
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "scrape_farmacias_guadalajara_manual_session.py"),
            "--category",
            category_id,
            "--cdp-url",
            args.cdp_url,
            "--output",
            str(category_output),
        ]

        completed = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
        )

        meta_path = (
            ROOT
            / "diagnostics"
            / "farmacias_guadalajara_manual"
            / f"{category_id}_meta.json"
        )

        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}

        status = "SUCCESS" if completed.returncode == 0 else "ERROR"
        frame = pd.DataFrame(columns=COLUMNS)

        if category_output.exists():
            try:
                frame = pd.read_excel(
                    category_output,
                    sheet_name="Concentrado",
                    dtype={"sku": str, "store_id": str},
                )
            except Exception:
                frame = pd.DataFrame(columns=COLUMNS)

        if not frame.empty:
            for column in COLUMNS:
                if column not in frame.columns:
                    frame[column] = None
            frame = frame[COLUMNS]
            frames.append(frame)

        target = meta.get("target_products")
        products = len(frame)
        coverage = (
            round(products / target, 4)
            if isinstance(target, int) and target > 0
            else None
        )

        if completed.returncode == 2:
            status = "BLOCKED"
        elif completed.returncode != 0:
            status = f"ERROR_{completed.returncode}"
        elif products == 0:
            status = "EMPTY"
        elif coverage is not None and coverage < 0.95:
            status = "PARTIAL"

        sku_complete = int(
            frame["sku"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not frame.empty else 0
        price_complete = int(
            frame["price_current"].notna().sum()
        ) if not frame.empty else 0
        url_complete = int(
            frame["url"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not frame.empty else 0

        summaries.append(
            {
                "retailer": "Farmacias Guadalajara",
                "category_id": category_id,
                "status": status,
                "target_products": target,
                "product_links": meta.get("product_links"),
                "products": products,
                "coverage": coverage,
                "sku_complete": sku_complete,
                "price_complete": price_complete,
                "url_complete": url_complete,
                "expansion_rounds": len(meta.get("expansion_trace") or []),
                "return_code": completed.returncode,
            }
        )

        print(
            f"FULL_TEST       : {status} | target={target} | "
            f"products={products} | coverage={coverage}"
        )

        if status.startswith("ERROR") or status == "BLOCKED":
            print("La prueba se detiene para no forzar la sesión.")
            break

    concentrated = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=COLUMNS)
    )
    summary_df = pd.DataFrame(summaries)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        concentrated.to_excel(writer, index=False, sheet_name="Concentrado")
        summary_df.to_excel(writer, index=False, sheet_name="Resumen")
        for sheet_name in ("Concentrado", "Resumen"):
            ws = writer.book[sheet_name]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = Font(bold=True)

    print("")
    print("=" * 82)
    print("RESUMEN FINAL - FARMACIAS GUADALAJARA")
    print("=" * 82)
    if not summary_df.empty:
        print(summary_df.to_string(index=False))
    print(f"Filas totales: {len(concentrated)}")
    print(f"Output      : {OUTPUT}")

    if summary_df.empty:
        return 2
    bad = summary_df["status"].astype(str).str.startswith("ERROR")
    bad = bad | summary_df["status"].isin(["BLOCKED", "EMPTY"])
    return 2 if bool(bad.any()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
