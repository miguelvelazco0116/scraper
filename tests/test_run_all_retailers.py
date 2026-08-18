from scripts.run_all_retailers import classify_result, load_enabled_categories


def test_run_all_retailers_discovers_current_categories():
    soriana = load_enabled_categories("soriana")
    walmart = load_enabled_categories("walmart")

    assert {item["id"] for item in soriana} == {
        "cuidado-bucal",
        "limpiadores",
        "detergentes",
        "afeitado-depilacion-dama",
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
