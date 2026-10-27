"""Fase 6 integration regression tests: no network, no Chrome, no production master."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from scripts import integrate_phase6_yza as phase6


DAY = "2026-10-27"
CATS = ["medicamentos-respiratorio", "higiene-bucal"]


def _products(day=DAY):
    return pd.DataFrame([
        {
            "retailer": "Farmacias YZA", "category_id": cat,
            "scrape_timestamp": day + "T10:00:00", "sku": "YZA_" + str(idx),
            "price_current": 40 + idx, "url": "https://www.yza.mx/MXYZ_" + str(idx) + ".html",
            "postal_code": None, "store": None, "store_id": None,
            "store_context_verified": False,
        } for idx, cat in enumerate(CATS)
    ])


def _summary():
    return pd.DataFrame([
        {"retailer": "Farmacias YZA", "category_id": cat,
         "status": "SUCCESS", "products": 1, "capture_date": DAY}
        for cat in CATS
    ])


def test_phase6_validation_is_strict():
    frame = _products()
    summary = _summary()
    phase6._validate_frame(frame, summary, DAY)
    for column, value in [
        ("price_current", -1),
        ("postal_code", "11590"),
        ("store", "No verificada"),
        ("url", "https://other.example/item"),
        ("store_context_verified", True),
        ("scrape_timestamp", "2026-10-26T10:00:00"),
        ("sku", ""),
    ]:
        broken = frame.copy()
        broken.loc[0, column] = value
        with pytest.raises(ValueError):
            phase6._validate_frame(broken, summary, DAY)
    with pytest.raises(ValueError):
        phase6._validate_frame(pd.concat([frame, frame], ignore_index=True), summary, DAY)


def test_missing_previous_phases_blocks_integration(tmp_path, monkeypatch):
    pointer = tmp_path / "current_master.json"
    master = tmp_path / "master.xlsx"
    pointer.write_text(json.dumps({
        "master": str(master), "capture_date": DAY, "completed_phases": [1, 2]
    }), encoding="utf-8")
    monkeypatch.setattr(phase6, "CURRENT_MASTER_POINTER", pointer)
    monkeypatch.setattr(phase6, "_master_path_for_date", lambda day: master)
    monkeypatch.setattr(phase6.run_phase6_pilot, "validated_manifest", lambda: {})
    monkeypatch.setattr(
        phase6.run_phase6_pilot, "validate_capture",
        lambda retailer, day: {"products": 2, "categories": 2}
    )
    with pytest.raises(ValueError, match="1 a 5"):
        phase6.preview(DAY)
    assert not master.exists()


def test_preview_never_writes_and_uses_same_date(tmp_path, monkeypatch):
    master = tmp_path / "master.xlsx"
    pointer = tmp_path / "current_master.json"
    old_rows = pd.DataFrame([{
        "retailer": "Soriana", "category_id": "bucal", "sku": "001",
        "price_current": 99, "url": "https://www.soriana.com/a",
        "scrape_timestamp": DAY + "T09:00:00",
    }])
    old_summary = pd.DataFrame([{
        "retailer": "Soriana", "category_id": "bucal",
        "status": "COMPLETE", "source_phase": "FASE 2", "products": 1,
    }])
    control = pd.DataFrame([{"capture_date": DAY}])
    with pd.ExcelWriter(master) as writer:
        old_rows.to_excel(writer, sheet_name="Concentrado", index=False)
        old_summary.to_excel(writer, sheet_name="Resumen", index=False)
        control.to_excel(writer, sheet_name="Control", index=False)
    yza = tmp_path / "yza.xlsx"
    with pd.ExcelWriter(yza) as writer:
        _products().to_excel(writer, sheet_name="Concentrado", index=False)
        _summary().to_excel(writer, sheet_name="Resumen", index=False)
    pointer.write_text(json.dumps({
        "master": str(master), "capture_date": DAY,
        "completed_phases": [1, 2, 3, 4, 5]
    }), encoding="utf-8")
    monkeypatch.setattr(phase6, "CURRENT_MASTER_POINTER", pointer)
    monkeypatch.setattr(phase6, "_master_path_for_date", lambda day: master)
    monkeypatch.setattr(phase6.run_phase6_pilot, "validated_manifest", lambda: {})
    monkeypatch.setattr(
        phase6.run_phase6_pilot, "validate_capture",
        lambda retailer, day: {"products": 2, "categories": 2}
    )
    monkeypatch.setitem(phase6.run_phase6_pilot.EXPECTED["farmacias-yza"], "workbook", yza)
    before = master.read_bytes()
    plan = phase6.preview(DAY)
    assert plan["phase6_rows"] == 2
    assert master.read_bytes() == before
    assert len(plan["old_frame"]) == 1
    pointer.write_text(json.dumps({
        "master": str(master), "capture_date": "2026-10-26",
        "completed_phases": [1, 2, 3, 4, 5]
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="otra fecha"):
        phase6.preview(DAY)
