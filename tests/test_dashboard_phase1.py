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
