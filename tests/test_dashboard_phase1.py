"""Offline Phase 1 dashboard coverage (no capture is started)."""
from pathlib import Path
import sys
from unittest.mock import patch

import pandas as pd

from dashboard.phase1_state import phase1_inventory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

def test_phase1_inventory_has_all_30_expected_cases():
    with patch("run_phase1_production._read_phase1_snapshot", return_value={}), \
         patch("chedraui_case_cache.load_valid_snapshots", return_value={}):
        frame, verified = phase1_inventory(ROOT, day="2100-01-01")
    assert len(frame) == 30
    assert len(frame.loc[frame["retailer"].eq("Chedraui")]) == 20
    assert frame["estado"].eq("PENDIENTE").all()
    assert verified == {}

def test_phase1_complete_is_reused_and_not_marked_for_capture():
    key = ("Farmacias del Ahorro", "congestion-nasal", "")
    data = {key: (pd.DataFrame([{"sku": "123"}]), {"status": "COMPLETE"})}
    with patch("run_phase1_production._read_phase1_snapshot", return_value=data), \
         patch("chedraui_case_cache.load_valid_snapshots", return_value={}):
        frame, _ = phase1_inventory(ROOT, day="2100-01-01")
    row = frame.loc[(frame["retailer"] == "Farmacias del Ahorro") &
                    (frame["categoria"] == "congestion-nasal")].iloc[0]
    assert row["estado"] == "COMPLETE"
    assert row["accion"] == "OMITIR"
    assert row["productos_validados"] == 1

def test_chedraui_complete_flag_without_store_evidence_is_rejected():
    key = ("Chedraui", "higiene-bucal", "232")
    data = {key: (pd.DataFrame([{"sku": "invalid"}]), {"status": "COMPLETE"})}
    with patch("run_phase1_production._read_phase1_snapshot", return_value=data), \
         patch("chedraui_case_cache.load_valid_snapshots", return_value={}):
        frame, _ = phase1_inventory(ROOT, day="2100-01-01")
    row = frame.loc[(frame["store_id"] == "232") &
                    (frame["categoria"] == "higiene-bucal")].iloc[0]
    assert row["estado"] != "COMPLETE"
    assert row["accion"] == "REINTENTAR"


def _write_audited_master(tmp_path, day, retailer="Farmacias del Ahorro"):
    master = tmp_path / "master.xlsx"
    category = "congestion-nasal" if retailer != "Chedraui" else "higiene-bucal"
    store_id = "" if retailer != "Chedraui" else "232"
    rows = pd.DataFrame([{
        "retailer": retailer, "category_id": category, "store_id": store_id,
        "sku": "123", "scrape_timestamp": day + "T09:00:00",
    }])
    cases = pd.DataFrame([{
        "source_phase": "FASE 1", "retailer": retailer,
        "category_id": category, "store_id": store_id,
        "status": "COMPLETE", "products": 1,
    }])
    control = pd.DataFrame([{"capture_date": day}])
    with pd.ExcelWriter(master, engine="openpyxl") as writer:
        rows.to_excel(writer, index=False, sheet_name="Concentrado")
        cases.to_excel(writer, index=False, sheet_name="Resumen")
        control.to_excel(writer, index=False, sheet_name="Control")
    return master


def test_dashboard_recovers_cases_missing_from_older_progress():
    key = ("Farmacias del Ahorro", "congestion-nasal", "")
    master_case = {
        key: (pd.DataFrame([{"sku": "123"}]), {"status": "COMPLETE"})
    }
    with patch("run_phase1_production._read_phase1_snapshot", return_value={}), \
         patch("chedraui_case_cache.load_valid_snapshots", return_value={}), \
         patch("dashboard.phase1_state._verified_master_cases", return_value=master_case):
        frame, verified = phase1_inventory(ROOT, day="2100-01-01")
    row = frame.loc[
        frame["retailer"].eq("Farmacias del Ahorro") &
        frame["categoria"].eq("congestion-nasal")
    ].iloc[0]
    assert row["estado"] == "COMPLETE"
    assert row["accion"] == "OMITIR"
    assert key in verified


def test_master_reconciliation_checks_same_day_and_exact_case_rows(tmp_path):
    from dashboard.phase1_state import _verified_master_cases
    from run_phase1_production import _case_key
    day = "2100-01-01"
    master = _write_audited_master(tmp_path, day)
    certified = pd.DataFrame([{"fase": 1, "estado_calculado": "COMPLETE"}])
    with patch("dashboard.data.active_master",
               return_value=(master, {"capture_date": day})), \
         patch("dashboard.data.inventory_for_master",
               return_value=(certified, None, None, None)):
        found = _verified_master_cases(
            tmp_path, day, _case_key, lambda *args, **kwargs: []
        )
        stale = _verified_master_cases(
            tmp_path, "2100-01-02", _case_key, lambda *args, **kwargs: []
        )
    assert len(found) == 1
    assert ("Farmacias del Ahorro", "congestion-nasal", "") in found
    assert stale == {}


def test_master_reconciliation_does_not_bypass_chedraui_quality(tmp_path):
    from dashboard.phase1_state import _verified_master_cases
    from run_phase1_production import _case_key
    day = "2100-01-01"
    master = _write_audited_master(tmp_path, day, retailer="Chedraui")
    certified = pd.DataFrame([{"fase": 1, "estado_calculado": "COMPLETE"}])
    with patch("dashboard.data.active_master",
               return_value=(master, {"capture_date": day})), \
         patch("dashboard.data.inventory_for_master",
               return_value=(certified, None, None, None)):
        found = _verified_master_cases(
            tmp_path, day, _case_key, lambda *args, **kwargs: ["contrato inválido"]
        )
    assert found == {}
