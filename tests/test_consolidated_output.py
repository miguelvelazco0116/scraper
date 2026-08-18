from pathlib import Path

import pandas as pd

from main import COLUMNS, update_consolidated_output


def _row(category_id: str, sku: str, product: str, price: float) -> dict:
    row = {col: None for col in COLUMNS}
    row.update(
        {
            "scrape_timestamp": "2026-08-18T12:00:00-06:00",
            "retailer": "Soriana",
            "city": "Ciudad de México",
            "state": "CDMX",
            "category": "Cuidado bucal" if category_id == "cuidado-bucal" else "Cuidado del hogar",
            "subcategory": None if category_id == "cuidado-bucal" else "Limpiadores",
            "category_id": category_id,
            "sku": sku,
            "brand": "Marca",
            "product": product,
            "price_current": price,
            "price_regular": price,
            "url": f"https://example.com/{sku}",
        }
    )
    return row


def test_consolidated_output_accumulates_categories(tmp_path: Path):
    output = tmp_path / "concentrado_scraper.xlsx"

    first = pd.DataFrame([_row("cuidado-bucal", "100", "Producto 1", 10.0)], columns=COLUMNS)
    second = pd.DataFrame([_row("detergentes", "200", "Producto 2", 20.0)], columns=COLUMNS)

    update_consolidated_output(first, output)
    update_consolidated_output(second, output)

    assert output.exists()
    wb = pd.read_excel(output, sheet_name=None, dtype={"sku": str})
    assert set(wb) == {"Concentrado", "Resumen"}
    assert len(wb["Concentrado"]) == 2
    assert set(wb["Concentrado"]["sku"]) == {"100", "200"}
    assert len(wb["Resumen"]) == 2


def test_consolidated_output_replaces_same_category_slice(tmp_path: Path):
    output = tmp_path / "concentrado_scraper.xlsx"

    original = pd.DataFrame([_row("cuidado-bucal", "100", "Producto viejo", 10.0)], columns=COLUMNS)
    replacement = pd.DataFrame([_row("cuidado-bucal", "101", "Producto nuevo", 15.0)], columns=COLUMNS)

    update_consolidated_output(original, output)
    update_consolidated_output(replacement, output)

    df = pd.read_excel(output, sheet_name="Concentrado", dtype={"sku": str})
    assert len(df) == 1
    assert df.iloc[0]["sku"] == "101"
    assert df.iloc[0]["product"] == "Producto nuevo"
