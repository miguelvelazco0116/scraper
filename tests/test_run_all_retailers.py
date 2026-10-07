import pandas as pd

import scripts.run_all_retailers as runner
from main import (
    COLUMNS,
    evaluate_legacy_output_quality,
    update_consolidated_output,
)
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
        "enjuagues-bucales",
        "cuidado-de-la-ropa",
        "depilacion-y-rasurado",
    }


def test_run_all_retailers_classifies_controlled_failures():
    assert classify_result(0, "Productos únicos: 10") == "SUCCESS"
    assert classify_result(2, "BLOCKED: Walmart desafió la sesión") == "BLOCKED"
    assert classify_result(4, "STORE_CONTEXT_ERROR: SC Toreo") == "STORE_CONTEXT_ERROR"
    assert classify_result(6, "DEFERRED: cooldown activo") == "DEFERRED"
    assert classify_result(3, "No se encontraron productos") == "EMPTY"
    assert classify_result(7, "QUALITY_GATE: FAIL") == "PARTIAL"
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



def test_apply_quality_preserves_scrapy_sample_accepted(
    tmp_path,
    monkeypatch,
):
    output = tmp_path / "concentrado_scraper.xlsx"
    current = pd.DataFrame(
        [
            {
                "retailer": "Ibarra Mayoreo",
                "category_id": "perfumeria-abarrotes",
                "sku": "123",
                "url": "https://example.test/123",
                "price_current": 100.0,
                "price_regular": 100.0,
                "availability_status": "AVAILABLE",
                "store_context_verified": False,
            }
        ]
    )
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        current.to_excel(
            writer,
            index=False,
            sheet_name="Concentrado",
        )

    monkeypatch.setattr(runner, "OUTPUT", output)
    results = [
        {
            "retailer": "Ibarra Mayoreo",
            "category_id": "perfumeria-abarrotes",
            "status": "SUCCESS",
            "reported_products": 330,
            "target_products": 331,
            "coverage": 330 / 331,
            "scrapy_quality_status": "SAMPLE_ACCEPTED",
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

    _, summary = runner.apply_quality(results)

    row = summary.iloc[0]
    assert row["data_status"] == "FRESH"
    assert row["quality_status"] == "SAMPLE_ACCEPTED"
    assert row["target_products"] == 331
    assert row["coverage"] == 330 / 331



def test_consolidated_update_replaces_multiple_blocks_atomically(
    tmp_path,
):
    output = tmp_path / "concentrado_scraper.xlsx"

    def row(retailer, category, sku, price):
        data = {column: None for column in COLUMNS}
        data.update(
            {
                "retailer": retailer,
                "category_id": category,
                "city": "Catálogo online",
                "store_id": None,
                "sku": sku,
                "product": f"Producto {sku}",
                "price_current": price,
                "price_regular": price,
                "availability_status": "AVAILABLE",
                "url": f"https://example.test/{sku}",
            }
        )
        return data

    initial = pd.DataFrame(
        [
            row("Retailer A", "cat-1", "old-a", 10.0),
            row("Retailer A", "cat-2", "old-b", 20.0),
            row("Retailer B", "cat-3", "keep-c", 30.0),
        ],
        columns=COLUMNS,
    )
    update_consolidated_output(initial, output)

    replacement = pd.DataFrame(
        [
            row("Retailer A", "cat-1", "new-a", 11.0),
            row("Retailer A", "cat-2", "new-b", 22.0),
        ],
        columns=COLUMNS,
    )
    update_consolidated_output(replacement, output)

    final = pd.read_excel(output, sheet_name="Concentrado")

    assert set(final["sku"].astype(str)) == {
        "new-a",
        "new-b",
        "keep-c",
    }
    assert len(final) == 3



class _DummyLegacyScraper:
    def __init__(self, meta):
        self.run_meta = meta


def test_legacy_quality_gate_rejects_partial_catalog():
    rows = []
    for index in range(2):
        row = {column: None for column in COLUMNS}
        row.update(
            {
                "retailer": "Farmacias Similares",
                "category_id": "condones",
                "sku": str(index + 1),
                "product": f"Producto {index + 1}",
                "price_current": 10.0,
                "price_regular": 10.0,
                "availability_status": "AVAILABLE",
                "url": f"https://example.test/{index + 1}",
            }
        )
        rows.append(row)

    frame = pd.DataFrame(rows, columns=COLUMNS)
    scraper = _DummyLegacyScraper(
        {
            "status": "PARTIAL",
            "target_products": 3,
        }
    )

    passed, notes, _ = evaluate_legacy_output_quality(
        frame,
        scraper,
        "farmacias-similares",
    )

    assert passed is False
    assert "scraper status=PARTIAL" in notes
    assert "cobertura 2/3" in notes


def test_legacy_quality_gate_accepts_explicit_unavailable_without_price():
    rows = []
    available = {column: None for column in COLUMNS}
    available.update(
        {
            "retailer": "Farmacias Similares",
            "category_id": "condones",
            "sku": "1",
            "product": "Producto disponible",
            "price_current": 10.0,
            "price_regular": 10.0,
            "availability_status": "AVAILABLE",
            "url": "https://example.test/1",
        }
    )
    unavailable = {column: None for column in COLUMNS}
    unavailable.update(
        {
            "retailer": "Farmacias Similares",
            "category_id": "condones",
            "product": "Producto agotado",
            "price_current": None,
            "price_regular": None,
            "availability_status": "UNAVAILABLE",
            "url": None,
            "sku": None,
        }
    )
    rows.extend([available, unavailable])

    frame = pd.DataFrame(rows, columns=COLUMNS)
    scraper = _DummyLegacyScraper(
        {
            "status": "SUCCESS",
            "target_products": 2,
        }
    )

    passed, notes, _ = evaluate_legacy_output_quality(
        frame,
        scraper,
        "farmacias-similares",
    )

    assert passed is True
    assert notes == []
