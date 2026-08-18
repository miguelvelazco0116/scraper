from scraper.config import load_categories


def test_soriana_detergentes_hierarchy():
    categories = {x.id: x for x in load_categories("config/soriana/categories.yaml")}

    detergentes = categories["detergentes"]
    assert detergentes.name == "Cuidado del hogar"
    assert detergentes.subcategory == "Limpiadores"
    assert detergentes.sub_subcategory == "Detergentes"
    assert detergentes.url.endswith("/limpieza-del-hogar/limpiadores/detergentes/")


def test_soriana_parent_levels_remain_available():
    categories = {x.id: x for x in load_categories("config/soriana/categories.yaml")}

    assert "cuidado-bucal" in categories
    assert "limpiadores" in categories
    assert categories["limpiadores"].sub_subcategory is None
