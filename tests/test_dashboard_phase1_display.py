"""Read-only Phase-1 display tests: never start or modify a scraper."""
from pathlib import Path

import pandas as pd

from dashboard.data import phase1_master_view


DAY = "2100-01-01"


def inputs(tmp_path):
    inventory = pd.DataFrame([
        {
            "retailer": "Farmacias del Ahorro", "categoria": "congestion-nasal",
            "store_id": "", "estado": "PENDIENTE", "productos_validados": 0,
            "accion": "REINTENTAR", "detalle": "Anterior",
        },
        {
            "retailer": "Chedraui", "categoria": "higiene-bucal",
            "store_id": "232", "estado": "PENDIENTE", "productos_validados": 0,
            "accion": "REINTENTAR", "detalle": "Anterior",
        },
    ])
    cases = pd.DataFrame([
        {
            "source_phase": "FASE 1", "retailer": "Farmacias del Ahorro",
            "category_id": "congestion-nasal", "store_id": "",
            "status": "COMPLETE", "products": 3,
        },
        {
            "source_phase": "FASE 1", "retailer": "Chedraui",
            "category_id": "higiene-bucal", "store_id": "232",
            "status": "COMPLETE", "products": 5,
        },
    ])
    control = pd.DataFrame([{"capture_date": DAY}])
    phase = pd.Series({
        "estado_calculado": "COMPLETE",
        "control_coincide": True,
        "casos_complete": 2, "casos_esperados": 2, "filas_master": 8,
    })
    retailers = pd.DataFrame([
        {"fase": 1, "retailer": "Farmacias del Ahorro",
         "estado": "INTEGRADO_COMPLETE", "filas_master": 3, "casos_master": 1},
        {"fase": 1, "retailer": "Chedraui",
         "estado": "INTEGRADO_COMPLETE", "filas_master": 5, "casos_master": 1},
    ])
    return inventory, cases, control, phase, retailers


def workbook(tmp_path, cases, control):
    path = Path(tmp_path) / "master.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        cases.to_excel(writer, sheet_name="Resumen", index=False)
        control.to_excel(writer, sheet_name="Control", index=False)
    return path


def test_complete_master_replaces_stale_display_but_does_not_mutate_progress(tmp_path):
    inventory, cases, control, phase, retailers = inputs(tmp_path)
    master = workbook(tmp_path, cases, control)
    view = phase1_master_view(master, inventory, phase, retailers, DAY)
    assert view is not None
    assert view["estado"].tolist() == ["COMPLETE", "COMPLETE"]
    assert view["productos_validados"].tolist() == [3, 5]
    assert view["accion"].tolist() == ["INTEGRADO", "INTEGRADO"]
    assert inventory["estado"].tolist() == ["PENDIENTE", "PENDIENTE"]
    assert inventory["accion"].tolist() == ["REINTENTAR", "REINTENTAR"]


def test_master_from_another_day_never_changes_display(tmp_path):
    inventory, cases, control, phase, retailers = inputs(tmp_path)
    master = workbook(tmp_path, cases, control)
    assert phase1_master_view(
        master, inventory, phase, retailers, "2100-01-02"
    ) is None


def test_phase_requires_complete_and_control_consistency(tmp_path):
    inventory, cases, control, phase, retailers = inputs(tmp_path)
    master = workbook(tmp_path, cases, control)
    for column, invalid in (
        ("estado_calculado", "PARTIAL_VALIDATED"),
        ("control_coincide", False),
        ("casos_complete", 1),
    ):
        modified = phase.copy()
        modified[column] = invalid
        assert phase1_master_view(
            master, inventory, modified, retailers, DAY
        ) is None


def test_mismatched_or_duplicate_store_category_does_not_change_display(tmp_path):
    inventory, cases, control, phase, retailers = inputs(tmp_path)
    for invalid in ("999", "232.0"):
        changed = cases.copy()
        changed.loc[1, "store_id"] = invalid
        master = workbook(tmp_path, changed, control)
        if invalid == "999":
            assert phase1_master_view(master, inventory, phase, retailers, DAY) is None
        else:
            assert phase1_master_view(master, inventory, phase, retailers, DAY) is not None

    changed = cases.copy()
    changed.loc[1, "category_id"] = "incorrecta"
    master = workbook(tmp_path, changed, control)
    assert phase1_master_view(master, inventory, phase, retailers, DAY) is None


def test_retailer_and_product_counts_must_match_audit(tmp_path):
    inventory, cases, control, phase, retailers = inputs(tmp_path)
    master = workbook(tmp_path, cases, control)
    changed = retailers.copy()
    changed.loc[1, "filas_master"] = 6
    assert phase1_master_view(master, inventory, phase, changed, DAY) is None
    changed_phase = phase.copy()
    changed_phase["filas_master"] = 9
    assert phase1_master_view(
        master, inventory, changed_phase, retailers, DAY
    ) is None
