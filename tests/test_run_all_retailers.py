import pandas as pd

import scripts.run_all_retailers as runner
from scripts.run_all_retailers import (
    classify_result,
    deferred_result,
    load_enabled_categories,
)


def test_run_all_retailers_discovers_current_categories():
    soriana = load_enabled_categories("soriana")
    walmart = load_enabled_categories("walmart")

    assert {item["id"] for item in soriana} == {
        "cuidado-bucal",
        "limpiadores",
        "detergentes",
        "afeitado-depilacion-dama",
        "desodorantes-para-caballero",
        "desodorantes-para-dama",
    }
    assert {item["id"] for item in walmart} == {
        "cuidado-bucal",
        "cuidado-de-la-ropa",
        "depilacion-y-rasurado",
    }


def test_run_all_retailers_classifies_controlled_failures():
    assert classify_result(0, "Productos únicos: 10") == "SUCCESS"
    assert classify_result(2, "BLOCKED: Walmart desafió la sesión") == "BLOCKED"
    assert classify_result(4, "STORE_CONTEXT_ERROR: SC Toreo") == "STORE_CONTEXT_ERROR"
    assert classify_result(3, "No se encontraron productos") == "EMPTY"
    assert classify_result(1, "unexpected") == "ERROR"



def test_apply_quality_marks_previous_rows_as_stale(tmp_path, monkeypatch):
    output = tmp_path / "concentrado_scraper.xlsx"
    old = pd.DataFrame(
        [
            {
                "retailer": "Soriana",
                "category_id": "limpiadores",
                "sku": "1",
                "url": "https://example.test/1",
                "price_current": 10.0,
                "price_regular": 10.0,
                "availability_status": "UNKNOWN",
                "store_context_verified": False,
            }
        ]
    )
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        old.to_excel(writer, index=False, sheet_name="Concentrado")

    monkeypatch.setattr(runner, "OUTPUT", output)
    results = [
        {
            "retailer": "Soriana",
            "category_id": "limpiadores",
            "status": "BLOCKED",
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
    ]

    concentrated, summary = runner.apply_quality(results)

    assert len(concentrated) == 1
    row = summary.iloc[0]
    assert row["data_status"] == "STALE_RETAINED"
    assert row["quality_status"] == "STALE"
    assert row["products"] == 1



def test_deferred_result_keeps_category_for_final_retry():
    category = {
        "id": "limpiadores",
        "department": "Supermercado",
        "name": "Limpieza",
        "subcategory": "Limpiadores",
        "sub_subcategory": None,
    }
    result = deferred_result(
        "soriana",
        category,
        "diferido por bloqueo previo",
    )
    assert result["retailer"] == "Soriana"
    assert result["category_id"] == "limpiadores"
    assert result["status"] == "DEFERRED"
    assert result["data_status"] == "MISSING"
    assert "bloqueo" in result["quality_notes"]
