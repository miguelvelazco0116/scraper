"""Offline Phase 6 contract: ONLY Farmacias YZA, online without CP.

Farmatodo was retired by operator decision on 27 Oct 2026. Fase 6 must
never run, validate or integrate it as part of an 'all' execution.
"""
import json
import sys

import pandas as pd
import pytest

from scripts import run_phase6_pilot as phase6


def test_phase6_manifest_has_only_yza_and_two_categories():
    manifest = phase6.validated_manifest()
    assert manifest["phase"] == 6
    assert [x["id"] for x in manifest["retailers"]] == ["farmacias-yza"]
    assert manifest["retailers"][0]["categories"] == [
        "medicamentos-respiratorio", "higiene-bucal"
    ]
    assert manifest["retailers"][0]["postal_code"] is None
    assert manifest["retailers"][0]["postal_mode"] == "NO_VERIFICATION"
    assert phase6.RETAILERS == ("farmacias-yza",)
    assert set(phase6.EXPECTED) == {"farmacias-yza"}
    assert phase6.EXPECTED["farmacias-yza"]["workbook"].name == "yza_online_test.xlsx"


def test_phase6_probe_runs_two_yza_categories_and_never_integrates_master():
    commands = phase6.commands_for(
        "probe", "farmacias-yza", "http://127.0.0.1:9222", 20
    )
    assert len(commands) == 2
    assert all(command[0] == sys.executable for command in commands)
    assert {command[command.index("--category") + 1] for command in commands} == {
        "medicamentos-respiratorio", "higiene-bucal"
    }
    assert all("probe_yza.py" in " ".join(command) for command in commands)
    assert all("integrate" not in " ".join(command) for command in commands)
    assert all("farmatodo" not in " ".join(command).lower() for command in commands)


def test_phase6_capture_runs_only_yza():
    commands = phase6.commands_for(
        "capture", "farmacias-yza", "http://127.0.0.1:9222", 100
    )
    assert len(commands) == 1
    assert commands[0][-2:] == ["--max-pages", "100"]
    assert "--cdp-url" in commands[0]
    assert "test_yza.py" in " ".join(commands[0])
    assert all("farmatodo" not in " ".join(c).lower() for c in commands)


@pytest.mark.parametrize("mode", ["probe", "capture"])
def test_phase6_yza_requires_manual_cdp(mode):
    with pytest.raises(ValueError, match="operador"):
        phase6.commands_for(mode, "farmacias-yza", "", 20)
    commands = phase6.commands_for(
        mode, "farmacias-yza", "http://127.0.0.1:9222", 20
    )
    assert all("--cdp-url" in command for command in commands)
    assert all("--headed" not in command for command in commands)


@pytest.mark.parametrize("mode", ["probe", "capture"])
def test_phase6_rejects_retired_farmatodo_explicitly(mode):
    with pytest.raises(ValueError, match="autorizado"):
        phase6.commands_for(
            mode, "farmatodo", "http://127.0.0.1:9222", 20
        )


def test_phase6_manual_chrome_wrapper_accepts_chrome_144_inspect_ws():
    script = (phase6.ROOT / "scripts" / "run_phase6_pilot.ps1").read_text(
        encoding="utf-8"
    )
    assert 'Get-Process chrome' in script
    assert 'DevToolsActivePort' in script
    assert "ws://127.0.0.1:" in script
    assert "$BrowserPath" in script
    assert 'New-Object System.Net.Sockets.TcpClient' in script
    assert 'Socket.EndConnect' in script
    assert '$CdpUrl = $Candidate' in script
    assert 'Si Chrome solicita permiso' in script
    assert '& $PythonExe @argsList' in script
    assert 'Start-Process' not in script
    assert 'Invoke-RestMethod -Uri "$Base/json/list"' not in script
    assert '$Version.webSocketDebuggerUrl' in script
    assert '[ValidateSet("all","farmacias-yza")]' in script
    assert "farmatodo" not in script.lower()


def test_shared_playwright_dependency_is_unified():
    root = phase6.ROOT
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    runner = (root / "scripts" / "run_phase6_pilot.py").read_text(
        encoding="utf-8"
    )
    assert "playwright==1.60.0" in requirements
    assert not (root / "requirements-phase6-yza-cdp.txt").exists()
    assert "requirements-phase6-yza-cdp.txt" not in runner
    assert "requirements.txt" in runner
    assert 'major_minor < (1, 60)' in runner


def test_phase6_rejects_remote_cdp_endpoint():
    with pytest.raises(ValueError, match="local"):
        phase6.commands_for(
            "probe", "farmacias-yza", "ws://198.51.100.20:9222", 20
        )


def test_capture_validation_yza_complete_without_postcode(tmp_path, monkeypatch):
    retailer = "farmacias-yza"
    categories = ["higiene-bucal", "medicamentos-respiratorio"]
    root = tmp_path / retailer
    root.mkdir()
    workbook = root / "catalog.xlsx"
    report = root / "report.json"
    day = "2026-10-27"
    rows, summaries = [], []
    for index, category in enumerate(categories):
        rows.append({
            "retailer": "Farmacias YZA", "category_id": category,
            "scrape_timestamp": day + "T10:00:00",
            "sku": f"sku-{index}", "url": f"https://example.org/{index}",
            "price_current": 50 + index,
            "postal_code": None, "store": None, "store_id": None,
            "store_context_verified": False,
        })
        summaries.append({
            "category_id": category, "capture_date": day,
            "status": "SUCCESS", "products": 1, "postal_code": None,
            "store_context_verified": False,
            "postal_verification_required": False,
        })

    def write_book(products):
        with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
            pd.DataFrame(products).to_excel(
                writer, sheet_name="Concentrado", index=False
            )
            pd.DataFrame(summaries).to_excel(
                writer, sheet_name="Resumen", index=False
            )

    write_book(rows)
    report_obj = {
        "capture_date": day, "master_modified": False,
        "postal_code": None, "store_context_verified": False,
        "complete_pilot": True, "postal_verification_required": False,
        "scope": "ONLINE_CATALOGUE_NO_LOCATION_VERIFICATION",
    }
    report.write_text(json.dumps(report_obj), encoding="utf-8")
    metadata = dict(phase6.EXPECTED[retailer])
    metadata["workbook"], metadata["report"] = workbook, report
    monkeypatch.setitem(phase6.EXPECTED, retailer, metadata)

    validated = phase6.validate_capture(retailer, day)
    assert validated["categories"] == 2
    assert validated["products"] == 2
    assert validated["scope"] == "ONLINE_CATALOGUE_NO_LOCATION_VERIFICATION"

    for tampering in (
        {"postal_code": "11590"},
        {"store_id": "123"},
        {"store": "Sucursal inventada"},
        {"store_context_verified": True},
        {"sku": None},
        {"price_current": None},
    ):
        altered = pd.DataFrame(rows)
        for key, value in tampering.items():
            altered.loc[0, key] = value
        write_book(altered.to_dict("records"))
        with pytest.raises(ValueError):
            phase6.validate_capture(retailer, day)

    write_book(rows)
    report.write_text(
        json.dumps(dict(report_obj, postal_code="11590")),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        phase6.validate_capture(retailer, day)


def test_manifest_rejects_legacy_yza_fixed_postcode(monkeypatch, tmp_path):
    manifest = phase6.validated_manifest()
    wrong = json.loads(json.dumps(manifest))
    wrong["retailers"][0]["postal_mode"] = "FIXED"
    wrong["retailers"][0]["postal_code"] = "11590"
    copy = tmp_path / "retailers.yaml"
    import yaml
    copy.write_text(yaml.safe_dump(wrong), encoding="utf-8")
    monkeypatch.setattr(phase6, "MANIFEST", copy)
    with pytest.raises(ValueError, match="codigo postal"):
        phase6.validated_manifest()


def test_manifest_rejects_readding_farmatodo(monkeypatch, tmp_path):
    manifest = phase6.validated_manifest()
    wrong = json.loads(json.dumps(manifest))
    wrong["retailers"].append({
        "id": "farmatodo", "name": "Farmatodo",
        "categories": ["respiratorios"],
        "postal_mode": "NO_VERIFICATION", "postal_code": None,
    })
    copy = tmp_path / "retailers.yaml"
    import yaml
    copy.write_text(yaml.safe_dump(wrong), encoding="utf-8")
    monkeypatch.setattr(phase6, "MANIFEST", copy)
    with pytest.raises(ValueError, match="únicamente Farmacias YZA"):
        phase6.validated_manifest()


def test_phase6_only_writes_isolated_diagnostics():
    code = (phase6.ROOT / "scripts" / "run_phase6_pilot.py").read_text(
        encoding="utf-8"
    )
    assert "current_master.json" not in code
    assert "integrate_complete_retailers" not in code
    assert 'PHASE6_REPORT = ROOT / "diagnostics" / "phase6"' in code
    assert '"farmatodo": {' not in code
