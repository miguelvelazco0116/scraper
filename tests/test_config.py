from scraper.config import load_categories


def test_soriana_detergentes_hierarchy():
    categories = {x.id: x for x in load_categories("config/soriana/categories.yaml")}

    detergentes = categories["detergentes"]
    assert detergentes.name == "Cuidado del hogar"
    assert detergentes.subcategory == "Limpiadores"
    assert detergentes.sub_subcategory == "Detergentes"
    assert detergentes.url.endswith("/limpieza-del-hogar/limpiadores/detergentes/")


def test_soriana_afeitado_depilacion_dama_hierarchy():
    categories = {x.id: x for x in load_categories("config/soriana/categories.yaml")}

    dama = categories["afeitado-depilacion-dama"]
    assert dama.department == "Cuidado personal"
    assert dama.name == "Afeitado y depilación"
    assert dama.subcategory == "Afeitado y depilación para dama"
    assert dama.sub_subcategory is None
    assert dama.url.endswith("/cuidado-personal-y-belleza/afeitado-y-depilacion/afeitado-y-depilacion-para-dama/")


def test_soriana_parent_levels_remain_available():
    categories = {x.id: x for x in load_categories("config/soriana/categories.yaml")}

    assert "cuidado-bucal" in categories
    assert "limpiadores" in categories
    assert categories["limpiadores"].sub_subcategory is None
