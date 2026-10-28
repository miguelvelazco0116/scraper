"""Soriana Streamlit integration contracts; no browser, network or master writes."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from dashboard.execution import RUNNERS, prepare_run, public_command
from dashboard.soriana_rotation import (
    CODES, EXPECTED_CASES, PROGRESS_SCHEMA, RETAILER_TARGET,
    read_progress, audit_snapshot,
)


DAY = "2026-10-27"


def _progress():
    cases = [
        {
            "postal_code": cp, "category_id": "category-" + str(i),
            "estado": "COMPLETE", "accion": "OMITIR",
            "productos_validados": 10, "attempts": 1,
            "retry_history": [{"attempt": 1, "status": "COMPLETE", "rows": 10}],
        }
        for cp in CODES for i in range(6)
    ]
    return {
        "schema_version": PROGRESS_SCHEMA, "retailer": "Soriana",
        "capture_date": DAY, "total_cases": 24, "complete_cases": 24,
        "pending_cases": 0, "attempts_total": 24,
        "products_validated": 240, "cases": cases,
        "master_modified": False,
    }


def test_progress_only_reads_matching_day_contract(tmp_path):
    directory = tmp_path / "diagnostics" / "soriana_cp_rotation"
    directory.mkdir(parents=True)
    path = directory / f"progress_{DAY}_all_all.json"
    path.write_text(json.dumps(_progress()), encoding="utf-8")
    current = read_progress(tmp_path, DAY)
    assert current is not None
    assert current["total_cases"] == EXPECTED_CASES
    assert read_progress(tmp_path, "2026-10-28") is None
    assert read_progress(tmp_path, "../bad") is None
    data = _progress()
    data["capture_date"] = "2026-10-26"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert read_progress(tmp_path, DAY) is None
    data["capture_date"] = DAY
    data["cases"][0]["postal_code"] = "99999"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert read_progress(tmp_path, DAY) is None


def test_audit_reads_exact_pilot_root_and_distinguishes_cases(tmp_path, monkeypatch):
    import scripts.audit_soriana_cp_rotation as independent

    called = {}
    def offline_audit(day, *, output_dir):
        called["day"] = day
        called["path"] = output_dir
        return [
            {"status": "COMPLETE", "products": 10, "cp": cp, "category": f"cat{i}"}
            for cp in CODES for i in range(6)
        ]
    monkeypatch.setattr(independent, "audit", offline_audit)
    result = audit_snapshot(tmp_path, DAY)
    assert called["day"] == DAY
    assert called["path"] == tmp_path / "output" / "pilots" / "soriana_cp_rotation"
    assert result["certified"] is True
    assert result["validated_products"] == 240
    assert result["master_modified"] is False

    def partial_audit(day, *, output_dir):
        result = offline_audit(day, output_dir=output_dir)
        result[-1] = {"status": "PENDING", "products": 10, "issue": "Mala fecha"}
        return result
    monkeypatch.setattr(independent, "audit", partial_audit)
    result = audit_snapshot(tmp_path, DAY)
    assert result["certified"] is False
    assert result["complete"] == 23
    assert len(result["issues"]) == 1


def test_dashboard_uses_separate_capture_only_allowlisted_runner(tmp_path):
    assert RUNNERS[2]["Soriana"] == "test_soriana_manual_phase2.ps1"
    assert RUNNERS[2][RETAILER_TARGET] == "test_soriana_cp_rotation.ps1"
    script = tmp_path / "scripts" / "test_soriana_cp_rotation.ps1"
    script.parent.mkdir()
    script.touch()
    with patch("dashboard.execution.os.name", "nt"), \
         patch("dashboard.execution.shutil.which", return_value="powershell.exe"):
        spec = prepare_run(tmp_path, 2, RETAILER_TARGET, "solo_captura")
    assert spec["command"][-4:] == ["-PostalCode", "all", "-Category", "all"]
    assert "SkipIntegration" not in " ".join(spec["command"])
    assert " -PostalCode all -Category all" in public_command(
        tmp_path, 2, RETAILER_TARGET, "solo_captura"
    )
    with pytest.raises(ValueError):
        prepare_run(tmp_path, 2, RETAILER_TARGET, "captura_integracion")
