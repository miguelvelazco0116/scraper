"""Regression tests for Soriana 24 CP/category cases in same-day master."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from main import COLUMNS
from scraper.soriana_postal_contract import (
    CODES, CATEGORIES, EXPECTED, PHASE, RETAILER, SCOPE,
    effective_soriana_cases, is_postal_rotation_summary, postal_issues,
)
from scripts.integrate_soriana_postal import (
    prepare_plan, plan_report, commit_plan, source_preview, _hash,
)
from scripts.audit_master_phases import inventory


def test_standalone_import_does_not_depend_on_pytest_order():
    # Windows test discovery does not add scripts/ to sys.path. This exact
    # isolated import used to throw ModuleNotFoundError on the operator PC.
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c",
         "from scripts.audit_master_phases import inventory; "
         "from scripts.integrate_soriana_postal import prepare_plan; "
         "print('AUDIT_IMPORT_OK')"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "AUDIT_IMPORT_OK" in result.stdout


def sample_data(day):
    rows, cases = [], []
    for cp in CODES:
        for cat in CATEGORIES:
            # The same SKU/URL must survive in EACH CP (never cross-CP dedup).
            rows.append({
                "scrape_timestamp": day + "T12:00:00",
                "retailer": RETAILER, "postal_code": cp,
                "category_id": cat, "sku": "SKU-" + cat,
                "url": "https://www.soriana.com/p/" + cat,
                "price_current": 20 + int(cp[0]),
                "store_id": None, "store_context_verified": False,
                "availability_status": "AVAILABLE", "is_available": True,
            })
            cases.append({
                "retailer": RETAILER, "source_phase": PHASE,
                "postal_code": cp, "category_id": cat,
                "products": 1, "status": "COMPLETE",
                "capture_date": day, "integration_scope": SCOPE,
                "postal_context_verified": True,
                "postal_context_evidence": "header_delivery_cp:" + cp,
                "pagination_complete": True,
            })
    return pd.DataFrame(rows).reindex(columns=COLUMNS), pd.DataFrame(cases)


def create_master_and_sources(root: Path, day: str):
    from scripts.integrate_validated_phase import _write_master
    from scraper.capture_progress import _write_json_atomic
    root.mkdir(parents=True, exist_ok=True)
    (root / "output" / "pilots" / "soriana_cp_rotation").mkdir(parents=True)
    all_rows, all_cases = sample_data(day)
    for cp in CODES:
        dest = root / "output" / "pilots" / "soriana_cp_rotation" / f"soriana_{cp}.xlsx"
        one_rows = all_rows.loc[all_rows["postal_code"].eq(cp)]
        one_cases = all_cases.loc[all_cases["postal_code"].eq(cp)]
        with pd.ExcelWriter(dest, engine="openpyxl") as writer:
            one_rows.to_excel(writer, sheet_name="Concentrado", index=False)
            one_cases.to_excel(writer, sheet_name="Resumen", index=False)
    # Legacy 6-category Soriana, 4-category Farmacias Guadalajara.
    old = all_rows.loc[all_rows["postal_code"].eq("83420")].copy()
    old["postal_code"] = None
    fg_rows = pd.DataFrame([{
        "scrape_timestamp": day + "T09:00:00", "retailer": "Farmacias Guadalajara",
        "category_id": "fg-" + str(i), "sku": "FG" + str(i),
        "url": "https://www.farmaciasguadalajara.com/fg" + str(i),
        "price_current": 40, "is_available": True,
    } for i in range(4)]).reindex(columns=COLUMNS)
    fg_cases = pd.DataFrame([{
        "retailer": "Farmacias Guadalajara", "source_phase": PHASE,
        "category_id": "fg-" + str(i), "status": "COMPLETE", "products": 1,
    } for i in range(4)])
    legacy = all_cases.loc[all_cases["postal_code"].eq("83420")].copy()
    legacy["postal_code"] = None
    legacy["integration_scope"] = "LEGACY"
    master = root / "output" / f"concentrado_productivo_total_{day}.xlsx"
    control = pd.DataFrame([{
        "capture_date": day, "phase2_status": "COMPLETE",
        "phase1_status": "PENDING", "products": 10,
    }])
    _write_master(master, pd.concat([fg_rows, old], ignore_index=True),
                  pd.concat([fg_cases, legacy], ignore_index=True), control)
    pointer = root / "output" / "checkpoints_validos" / "current_master.json"
    _write_json_atomic(pointer, {
        "master": str(master), "capture_date": day,
        "completed_phases": [1, 2], "accepted_partial_phases": [],
    })
    return master, pointer


def test_composite_grain_does_not_dedupe_across_cp():
    day = "2026-10-27"
    frame, cases = sample_data(day)
    assert not postal_issues(frame, cases, day)
    assert len(frame) == 24
    assert len(frame[["sku", "url"]].drop_duplicates()) == 6
    assert len(frame[["postal_code", "category_id", "sku", "url"]].drop_duplicates()) == 24
    assert is_postal_rotation_summary(cases)
    assert effective_soriana_cases(frame, cases) == 24


def test_bad_duplicate_or_postal_evidence_is_rejected():
    frame, cases = sample_data("2026-10-27")
    duplicate = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    assert any("duplicados" in issue for issue in postal_issues(duplicate, cases, "2026-10-27"))
    cases.loc[0, "postal_context_evidence"] = "header_delivery_cp:00000"
    assert any("Evidencia" in issue for issue in postal_issues(frame, cases, "2026-10-27"))


def test_legacy_6_case_contract_is_preserved():
    frame, cases = sample_data("2026-10-27")
    legacy = cases.loc[cases["postal_code"].eq("83420")].copy()
    legacy["integration_scope"] = "LEGACY"
    assert not is_postal_rotation_summary(legacy)
    assert effective_soriana_cases(frame, legacy) == 6


def test_preview_then_commit_does_not_modify_other_retailers(tmp_path):
    day = datetime.now().astimezone().date().isoformat()
    master, pointer = create_master_and_sources(tmp_path, day)
    before_master, before_pointer = _hash(master), _hash(pointer)
    plan = prepare_plan(tmp_path, day)
    report = plan_report(plan)
    assert report["master_modified"] is False
    assert report["old_soriana_rows"] == 6
    assert report["new_soriana_rows"] == 24
    assert report["new_master_rows"] == 28
    assert _hash(master) == before_master
    assert _hash(pointer) == before_pointer

    result = commit_plan(tmp_path, plan)
    assert result["status"] == "COMMITTED"
    assert result["other_retailer_rows_preserved"] == 4
    assert Path(result["backup"]).exists()
    assert _hash(pointer) == before_pointer
    phases, retailers, cases, facts = inventory(master)
    soriana = retailers.loc[retailers["retailer"].eq(RETAILER)].iloc[0]
    assert soriana["estado"] == "INTEGRADO_COMPLETE"
    assert int(soriana["casos_master"]) == 24
    assert int(soriana["filas_master"]) == 24
    assert phases.loc[phases["fase"].eq(2)].iloc[0]["estado_calculado"] == "COMPLETE"
    actual = pd.read_excel(master, sheet_name="Concentrado", dtype={"postal_code": str})
    assert len(actual.loc[actual["retailer"].eq("Farmacias Guadalajara")]) == 4
    assert len(actual.loc[actual["retailer"].eq("Soriana")]) == 24
    assert set(actual.loc[actual["retailer"].eq("Soriana"), "postal_code"]) == set(CODES)

    # Idempotent: another preview replaces only the 24 CP records.
    again = prepare_plan(tmp_path, day)
    assert again["old_soriana_rows"] == 24
    assert again["new_total"] == 28


def test_post_write_audit_failure_restores_originals(tmp_path, monkeypatch):
    import scripts.integrate_soriana_postal as integrator
    day = datetime.now().astimezone().date().isoformat()
    master, pointer = create_master_and_sources(tmp_path, day)
    plan = prepare_plan(tmp_path, day)
    previous = (_hash(master), _hash(pointer))
    real_write = integrator._write_master

    def corrupt_and_raise(path, frame, cases, control):
        real_write(path, frame, cases, control)
        raise RuntimeError("simulated disk error after master write")

    monkeypatch.setattr(integrator, "_write_master", corrupt_and_raise)
    with pytest.raises(RuntimeError, match="simulated disk error"):
        commit_plan(tmp_path, plan)
    assert (_hash(master), _hash(pointer)) == previous


def test_source_preview_historical_data_is_read_only(tmp_path):
    day = "2026-10-27"
    master, pointer = create_master_and_sources(tmp_path, day)
    before = (_hash(master), _hash(pointer))
    # Simulates a master pointer that advanced to another day.
    pointer.write_text(json.dumps({
        "master": str(master), "capture_date": "2026-10-28",
        "completed_phases": [1, 2]
    }), encoding="utf-8")
    pointer_before = _hash(pointer)
    preview = source_preview(tmp_path, day)
    assert preview["status"] == "SORIANA_SOURCE_VALIDATED"
    assert preview["capture_date"] == day
    assert preview["master_active_date"] == "2026-10-28"
    assert preview["postal_cases"] == 24
    assert preview["products_validated"] == 24
    assert preview["master_modified"] is False
    assert preview["master_integration_checked"] is False
    assert _hash(master) == before[0]
    assert _hash(pointer) == pointer_before
    with pytest.raises(ValueError, match="otra fecha"):
        prepare_plan(tmp_path, day)


def test_wrong_day_or_incomplete_sources_never_modify_master(tmp_path):
    day = datetime.now().astimezone().date().isoformat()
    master, pointer = create_master_and_sources(tmp_path, day)
    previous = (_hash(master), _hash(pointer))
    with pytest.raises(ValueError, match="otra fecha"):
        prepare_plan(tmp_path, "2026-10-12")
    wrong = tmp_path / "output" / "pilots" / "soriana_cp_rotation" / "soriana_97245.xlsx"
    wrong.unlink()
    with pytest.raises(ValueError, match="24/24"):
        prepare_plan(tmp_path, day)
    assert (_hash(master), _hash(pointer)) == previous
