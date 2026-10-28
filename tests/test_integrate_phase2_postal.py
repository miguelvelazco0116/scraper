"""Offline phase2 capture-only -> integration-only regression suite.

No browser, network or production master. Every test uses a temporary master
and checks the published 24-postal-case grain and the atomic rollback.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from main import COLUMNS
from scraper.soriana_postal_contract import CODES, CATEGORIES, SCOPE
from scripts.integrate_validated_phase import _write_master
from scripts import integrate_phase2_postal as integration
from scripts.audit_master_phases import inventory

DAY = datetime.now().astimezone().date().isoformat()


def sample_day(day: str):
    rows, cases = [], []
    for retailer, count in [("Farmacias del Ahorro", 6),
                            ("Ibarra Mayoreo", 4),
                            ("Chedraui", 20)]:
        for i in range(count):
            cat = f"{retailer[:2]}-{i}"
            rows.append(dict(
                retailer=retailer, scrape_timestamp=day+"T10:00:00",
                category_id=cat, sku=f"{retailer}-{i}", price_current=40,
                url=f"https://example.org/{retailer[:2]}/{i}",
            ))
            cases.append(dict(retailer=retailer, source_phase="FASE 1",
                              category_id=cat, status="COMPLETE", products=1))
    return pd.DataFrame(rows).reindex(columns=COLUMNS), pd.DataFrame(cases)


def sources(day: str):
    fg_rows, fg_cases, soriana_rows, soriana_cases = [], [], [], []
    for i in range(4):
        cat = f"fg-{i}"
        fg_rows.append(dict(
            retailer="Farmacias Guadalajara", category_id=cat,
            scrape_timestamp=day+"T11:00:00", price_current=50,
            sku=f"fg-{i}", url=f"https://example.org/fg/{i}",
        ))
        fg_cases.append(dict(retailer="Farmacias Guadalajara",
                             source_phase="FASE 2", category_id=cat,
                             status="COMPLETE", products=1,
                             integration_scope="STANDARD_VERIFIED"))
    for cp in CODES:
        for cat in CATEGORIES:
            soriana_rows.append(dict(
                retailer="Soriana", category_id=cat,
                postal_code=cp, scrape_timestamp=day+"T12:00:00",
                price_current=30, sku=f"sku-{cat}",
                url=f"https://www.soriana.com/{cat}", store_id=None,
                store_context_verified=False, availability_status="AVAILABLE",
            ))
            soriana_cases.append(dict(
                retailer="Soriana", source_phase="FASE 2", category_id=cat,
                postal_code=cp, status="COMPLETE", products=1,
                capture_date=day, integration_scope=SCOPE,
                postal_context_verified=True,
                postal_context_evidence="header_delivery_cp:"+cp,
            ))
    return ((pd.DataFrame(fg_rows).reindex(columns=COLUMNS), pd.DataFrame(fg_cases), {}),
            (pd.DataFrame(soriana_rows).reindex(columns=COLUMNS),
             pd.DataFrame(soriana_cases), {}))


def master(root: Path, day: str):
    frame, cases = sample_day(day)
    target = root/"output"/f"concentrado_productivo_total_{day}.xlsx"
    target.parent.mkdir(parents=True, exist_ok=True)
    control = pd.DataFrame([dict(
        capture_date=day, status="CHECKPOINT_VALIDATED",
        phase1_status="COMPLETE", phase2_status="PENDING",
        phase3_status="PENDING", phase4_status="PENDING", phase5_status="PENDING",
    )])
    _write_master(target, frame, cases, control)
    pointer = root/"output"/"checkpoints_validos"/"current_master.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps({
        "master": str(target), "capture_date": day,
        "completed_phases": [1], "accepted_partial_phases": [],
    }), encoding="utf-8")
    return target, pointer


def mock_sources(monkeypatch, day, *, fg=True, soriana=True):
    fg_data, postal_data = sources(day)
    def get_fg(root, capture_date):
        assert capture_date == day
        if not fg:
            raise ValueError("FG aun no COMPLETE")
        return fg_data
    def get_postal(root, capture_date):
        assert capture_date == day
        if not soriana:
            raise ValueError("Soriana aun no COMPLETE")
        return postal_data
    monkeypatch.setattr(integration, "read_fg", get_fg)
    monkeypatch.setattr(integration, "_validate_sources", get_postal)


def test_integrates_existing_excel_data_without_scraping_and_without_duplicate(tmp_path, monkeypatch):
    master_path, pointer_path = master(tmp_path, DAY)
    mock_sources(monkeypatch, DAY)
    old_sha = integration._hash(master_path)
    plan = integration.prepare_plan(tmp_path, DAY)
    assert plan["states"][2] == "COMPLETE"
    assert plan["after_rows"] == 30 + 4 + 24
    assert integration._hash(master_path) == old_sha

    committed = integration.commit(tmp_path, plan)
    assert committed["status"] == "COMMITTED"
    assert committed["integrated_sources"] == {
        "Farmacias Guadalajara": 4, "Soriana": 24
    }
    assert Path(committed["backup"]).is_file()
    ptr = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert ptr["completed_phases"] == [1, 2]
    phases, retailers, _, facts = inventory(master_path)
    phase2 = phases.loc[phases["fase"].eq(2)].iloc[0]
    assert phase2["estado_calculado"] == "COMPLETE"
    assert phase2["control_coincide"]
    assert int(phase2["casos_esperados"]) == 28
    assert int(retailers.loc[retailers["retailer"].eq("Soriana"),"casos_master"].iloc[0]) == 24
    full = pd.read_excel(master_path, sheet_name="Concentrado", dtype={"postal_code": str})
    postal = full.loc[full["retailer"].eq("Soriana")]
    assert len(postal) == 24
    assert set(postal["postal_code"]) == set(CODES)
    assert len(postal[["postal_code","category_id","sku","url"]].drop_duplicates()) == 24
    assert len(full.loc[full["retailer"].eq("Chedraui")]) == 20

    # A second integration REPLACES its partition; it cannot add 24 more rows.
    again = integration.prepare_plan(tmp_path, DAY)
    assert again["after_rows"] == 58
    integration.commit(tmp_path, again)
    assert len(pd.read_excel(master_path, sheet_name="Concentrado")) == 58


def test_soriana_only_stays_partial_until_fg_passes(tmp_path, monkeypatch):
    file, pointer = master(tmp_path, DAY)
    mock_sources(monkeypatch, DAY, fg=False)
    plan = integration.prepare_plan(tmp_path, DAY)
    assert plan["states"][2] == "PARTIAL_VALIDATED"
    assert plan["after_rows"] == 54
    result = integration.commit(tmp_path, plan)
    assert result["phase2_status"] == "PARTIAL_VALIDATED"
    assert result["integrated_sources"] == {"Soriana": 24}
    assert "Farmacias Guadalajara" in result["pending_sources"]
    assert json.loads(pointer.read_text())["completed_phases"] == [1]
    phases, _, _, _ = inventory(file)
    assert phases.loc[phases["fase"].eq(2)].iloc[0]["estado_calculado"] == "PARTIAL_VALIDATED"
    mock_sources(monkeypatch, DAY)
    result = integration.commit(tmp_path, integration.prepare_plan(tmp_path, DAY))
    assert result["phase2_status"] == "COMPLETE"
    assert json.loads(pointer.read_text())["completed_phases"] == [1, 2]


def test_rollback_after_excel_write_restores_pointer_and_master(tmp_path, monkeypatch):
    file, pointer = master(tmp_path, DAY)
    mock_sources(monkeypatch, DAY)
    plan = integration.prepare_plan(tmp_path, DAY)
    original = (integration._hash(file), integration._hash(pointer))
    real_write = integration._write_master
    def write_and_fail(*args, **kwargs):
        real_write(*args, **kwargs)
        raise RuntimeError("simulated partial disk write")
    monkeypatch.setattr(integration, "_write_master", write_and_fail)
    with pytest.raises(RuntimeError, match="simulated partial disk write"):
        integration.commit(tmp_path, plan)
    assert (integration._hash(file), integration._hash(pointer)) == original


def test_reject_old_master_and_missing_fase1(tmp_path, monkeypatch):
    yesterday = (datetime.now().astimezone().date() - timedelta(days=1)).isoformat()
    master(tmp_path, yesterday)
    with pytest.raises(ValueError, match="Master activo"):
        integration.prepare_plan(tmp_path, DAY)
    master(tmp_path, DAY)
    pointer = tmp_path/"output"/"checkpoints_validos"/"current_master.json"
    value = json.loads(pointer.read_text())
    value["completed_phases"] = []
    pointer.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Fase 1"):
        integration.prepare_plan(tmp_path, DAY)


def test_worker_selects_integration_only_runner_and_does_not_download(tmp_path, monkeypatch):
    from dashboard.execution import ALL, prepare_run, public_command
    root = tmp_path
    (root/"scripts").mkdir()
    (root/"scripts"/"integrate_phase2_postal.ps1").touch()
    file, pointer = master(root, DAY)
    # The OS gate is Windows-only. Keep real POSIX filesystem semantics
    # for the synthetic temp master while exercising the allowlisted command.
    from pathlib import PosixPath
    with patch("dashboard.execution.os.name", "nt"), \
         patch("dashboard.execution.Path", PosixPath), \
         patch("dashboard.execution.shutil.which", return_value="powershell.exe"):
        spec = prepare_run(root, 2, ALL, "solo_integracion")
    assert spec["runner"] == "integrate_phase2_postal.ps1"
    assert any("integrate_phase2_postal.ps1" in str(arg) for arg in spec["command"])
    assert "-PostalCode" not in spec["command"]
    assert "-SkipIntegration" not in spec["command"]
    assert "integrate_phase2_postal.ps1" in public_command(root, 2, ALL, "solo_integracion")


def test_read_actual_guadalajara_workbook_contract(tmp_path):
    """Exercise actual Excel reader and existing FG validation, not a mock."""
    from scripts.run_phase2_production import _enabled_category_ids
    categories = _enabled_category_ids("farmacias-guadalajara")
    assert len(categories) == 4
    path = tmp_path/"output"/"farmacias_guadalajara_phase2_test.xlsx"
    path.parent.mkdir(parents=True)
    products = []
    results = []
    for i, cat in enumerate(categories):
        products.append(dict(
            retailer="Farmacias Guadalajara", category_id=cat,
            scrape_timestamp=DAY+"T11:00:00", product=f"Producto real {i}",
            sku=f"FG-{i}", url=f"https://farmaciasguadalajara.com/{i}",
            price_current=30,
        ))
        results.append(dict(
            retailer="Farmacias Guadalajara", category_id=cat,
            status="SUCCESS", target_products=1, return_code=0,
            products=1, product_links=1,
        ))
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(products).reindex(columns=COLUMNS).to_excel(
            writer, sheet_name="Concentrado", index=False
        )
        pd.DataFrame(results).to_excel(writer, sheet_name="Resumen", index=False)
    fg, cases, hashes = integration.read_fg(tmp_path, DAY)
    assert len(fg) == 4
    assert len(cases) == 4
    assert cases["status"].eq("COMPLETE").all()
    assert cases["source_status"].eq("SUCCESS").all()
    assert str(path) in hashes
    # A capture from yesterday is never admitted into today's master.
    with pytest.raises(ValueError, match="Fecha|fecha|fechas"):
        integration.read_fg(tmp_path, (datetime.now().astimezone().date() - timedelta(days=1)).isoformat())
