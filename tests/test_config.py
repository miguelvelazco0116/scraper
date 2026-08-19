from scraper.config import load_categories, load_locations


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


def test_walmart_depilacion_y_rasurado_hierarchy():
    categories = {x.id: x for x in load_categories("config/walmart/categories.yaml")}

    depilacion = categories["depilacion-y-rasurado"]
    assert depilacion.department == "Belleza y cuidado personal"
    assert depilacion.name == "Depilación y rasurado"
    assert depilacion.subcategory is None
    assert depilacion.sub_subcategory is None
    assert depilacion.url.endswith("/browse/belleza/depilacion-y-rasurado/930017_264512")


def test_chedraui_hierarchy():
    categories = {x.id: x for x in load_categories("config/chedraui/categories.yaml")}

    oral = categories["higiene-bucal"]
    assert oral.department == "Cuidado e higiene personal"
    assert oral.name == "Higiene bucal"
    assert oral.subcategory is None
    assert oral.url.endswith("/cuidado-e-higiene-personal/higiene-bucal")

    laundry = categories["lavanderia"]
    assert laundry.department == "Supermercado"
    assert laundry.name == "Limpieza del hogar"
    assert laundry.subcategory == "Lavandería"
    assert laundry.url.endswith("/supermercado/limpieza-del-hogar/lavanderia")


def test_chedraui_polanco_store():
    locations = {x.id: x for x in load_locations()}
    store = locations["chedraui-polanco"]
    assert store.store_id == "232"
    assert store.store == "Chedraui Selecto México Polanco"
    assert store.postal_code == "11500"
    assert store.city == "Miguel Hidalgo"
