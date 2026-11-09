"""Protect 19 same-day validated Chedraui cases when only one needs retry."""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from dashboard.phase1_state import phase1_inventory
from scripts import run_phase1_production as production
from scraper.config import load_categories, load_locations
from scripts.chedraui_case_cache import CONTRACT


ROOT = Path(__file__).resolve().parents[1]


def prepare_root(tmp_path: Path) -> Path:
    for relative in [
        "config/locations.yaml",
        "config/chedraui/categories.yaml",
        "config/farmacias-del-ahorro/categories.yaml",
        "config/ibarra-mayoreo/categories.yaml",
    ]:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    (tmp_path / "output").mkdir(exist_ok=True)
    return tmp_path


def write_19_cases(root: Path, day: str, *, bad: str | None = None,
                   old_date: str | None = None, summary_flag=True) -> Path:
    locations = load_locations(root / "config/locations.yaml")
    locs = [x for x in locations if x.id.startswith("chedraui-")]
    categories = load_categories(root / "config/chedraui/categories.yaml")
    assert len(locs) == 4 and len(categories) == 5
    product_rows = []
    cases = []
    for location in locs:
        for category in categories:
            if location.id == "chedraui-puebla-centro-sur" and category.id == "respiratorios":
                continue  # The single category that never downloaded.
            identity = location.id + "/" + category.id
            product_rows.append({
                "retailer": "Chedraui", "store_id": str(location.store_id),
                "category_id": category.id,
                "sku": "SKU-" + identity,
                "url": "https://www.chedraui.com.mx/ip/" + identity,
                "price_current": 43.0,
                "scrape_timestamp": (
                    old_date if old_date and identity.endswith("/lavanderia") else day
                ) + "T11:00:00-06:00",
                "store_context_verified": identity != bad,
                "is_available": 1, "availability_status": "AVAILABLE",
            })
            cases.append({
                "retailer": "Chedraui", "store_id": str(location.store_id),
                "category_id": category.id, "status": "COMPLETE",
                "products": 1, "target_products": 1, "coverage": 1.0,
                "store_context_verified": summary_flag,
                "store_context_contract_version": CONTRACT,
                "pagination_gaps": 0,
                "structured_price_missing_pages": 0,
                "price_order_errors": 0,
            })
    path = root / "output" / "fase1_progress.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(product_rows).to_excel(
            writer, sheet_name="Concentrado", index=False
        )
        pd.DataFrame(cases).to_excel(writer, sheet_name="Resumen", index=False)
        pd.DataFrame([{"capture_date": day, "status": "PROGRESS"}]).to_excel(
            writer, sheet_name="Control", index=False
        )
    return path


def test_reuses_19_complete_chedraui_cases_and_retries_only_one(tmp_path):
    root = prepare_root(tmp_path)
    day = datetime.now().astimezone().date().isoformat()
    path = write_19_cases(root, day)
    assert len(production._read_phase1_snapshot(path, day=day)) == 19
    inventory, verified = phase1_inventory(root, day)
    chedraui = inventory.loc[inventory["retailer"].eq("Chedraui")]
    assert len(chedraui) == 20
    assert chedraui["estado"].value_counts().to_dict() == {
        "COMPLETE": 19, "PENDIENTE": 1
    }
    assert chedraui["accion"].value_counts().to_dict() == {
        "OMITIR": 19, "REINTENTAR": 1
    }
    assert len([x for x in verified if x[0] == "Chedraui"]) == 19


def test_reuse_19_cases_when_summary_excel_boolean_is_one_point_zero(tmp_path):
    root = prepare_root(tmp_path)
    day = datetime.now().astimezone().date().isoformat()
    path = write_19_cases(root, day, summary_flag=1.0)
    # No browser: 19 valid cases should be discovered by the production
    # loader and the dashboard using the same Excel workbook.
    verified = production._read_phase1_snapshot(path, day=day)
    assert len(verified) == 19
    inventory, _ = phase1_inventory(root, day)
    chedraui = inventory.loc[inventory["retailer"].eq("Chedraui")]
    assert (chedraui["estado"] == "COMPLETE").sum() == 19
    assert (chedraui["accion"] == "REINTENTAR").sum() == 1
    assert (chedraui["estado"] == "PENDIENTE").sum() == 1


def test_invalid_store_evidence_is_explained_not_auto_approved(tmp_path):
    root = prepare_root(tmp_path)
    day = datetime.now().astimezone().date().isoformat()
    write_19_cases(root, day, bad="chedraui-polanco/higiene-bucal")
    inventory, verified = phase1_inventory(root, day)
    chedraui = inventory.loc[inventory["retailer"].eq("Chedraui")]
    assert chedraui["estado"].value_counts().to_dict() == {
        "COMPLETE": 18, "REVISAR": 1, "PENDIENTE": 1
    }
    rejected = chedraui.loc[chedraui["estado"].eq("REVISAR")].iloc[0]
    assert "filas sin tienda certificada" in rejected["detalle"]
    assert rejected["accion"] == "REINTENTAR"
    assert len([x for x in verified if x[0] == "Chedraui"]) == 18


def test_wrong_day_in_one_category_does_not_invalidate_other_18(tmp_path):
    root = prepare_root(tmp_path)
    day = datetime.now().astimezone().date().isoformat()
    prior = (datetime.now().astimezone().date() - timedelta(days=1)).isoformat()
    path = write_19_cases(root, day, old_date=prior)
    verified = production._read_phase1_snapshot(path, day=day)
    # All four laundry cases have old dates, while fifteen other COMPLETE
    # categories remain independently reusable in the same day.
    assert len(verified) == 15
    inventory, _ = phase1_inventory(root, day)
    rows = inventory.loc[inventory["retailer"].eq("Chedraui")]
    assert (rows["estado"] == "COMPLETE").sum() == 15
    assert (rows["estado"] == "REVISAR").sum() == 4
    assert (rows["estado"] == "PENDIENTE").sum() == 1


def test_progress_saved_after_a_category_even_if_runner_is_interrupted(
    tmp_path, monkeypatch,
):
    root = prepare_root(tmp_path)
    monkeypatch.setattr(production, "ROOT", root)
    monkeypatch.setattr(production, "PROGRESS_PATH", root / "output" / "fase1_progress.xlsx")
    monkeypatch.setattr(production, "OUTPUT_PATH", root / "output" / "concentrado_productivo.xlsx")
    monkeypatch.setattr(production, "_load_existing_valid_results", lambda: {})
    monkeypatch.setattr(production.sys, "argv", ["run_phase1_production.py"])

    def interrupt_after_first(categories, location, *, low_memory, on_case):
        category = categories[0]
        frame = pd.DataFrame([{
            "retailer": "Farmacias del Ahorro", "category_id": category.id,
            "sku": "SKU-1", "url": "https://example.org/1",
            "scrape_timestamp": datetime.now().astimezone().date().isoformat() + "T11:00:00",
        }])
        on_case(frame, {
            "retailer": "Farmacias del Ahorro",
            "category_id": category.id, "status": "COMPLETE", "products": 1,
        })
        raise KeyboardInterrupt("Simulated interruption")

    monkeypatch.setattr(production, "_run_ahorro", interrupt_after_first)
    with pytest.raises(KeyboardInterrupt, match="Simulated interruption"):
        production.main()
    path = root / "output" / "fase1_progress.xlsx"
    assert path.is_file()
    summary = pd.read_excel(path, sheet_name="Resumen")
    assert len(summary) == 1
    assert summary.iloc[0]["status"] == "COMPLETE"
    assert len(pd.read_excel(path, sheet_name="Concentrado")) == 1
