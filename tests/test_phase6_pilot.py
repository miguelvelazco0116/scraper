"""Offline Phase 6 tests: YZA fixed CP and Farmatodo catalogue only."""
import json
import sys

import pandas as pd
import pytest

from scripts import run_phase6_pilot as phase6


def test_phase6_manifest_has_two_retailers_five_categories():
    manifest = phase6.validated_manifest()
    assert manifest["phase"] == 6
    assert [row["id"] for row in manifest["retailers"]] == [
        "farmacias-yza", "farmatodo"
    ]
    assert sum(len(row["categories"]) for row in manifest["retailers"]) == 5
    assert manifest["retailers"][0]["postal_code"] == "11590"
    assert manifest["retailers"][1]["postal_code"] is None


def test_phase6_probe_is_five_isolated_commands_without_master():
    all_cmd = [
        cmd for retailer in phase6.RETAILERS
        for cmd in phase6.commands_for(
            "probe", retailer, "http://127.0.0.1:9222", 20
        )
    ]
    assert len(all_cmd) == 5
    assert all(cmd[0] == sys.executable for cmd in all_cmd)
    assert all("--category" in cmd for cmd in all_cmd)
    assert all("integrate" not in " ".join(cmd) for cmd in all_cmd)


def test_phase6_capture_is_two_retailer_commands():
    yza = phase6.commands_for(
        "capture", "farmacias-yza", "http://127.0.0.1:9222", 100
    )
    farma = phase6.commands_for(
        "capture", "farmatodo", "http://127.0.0.1:9222", 100
    )
    assert len(yza) == len(farma) == 1
    assert yza[0][-2:] == ["--max-pages", "100"]
    assert "--max-rounds" in farma[0]
    assert "--cdp-url" in yza[0] and "--cdp-url" in farma[0]



def test_phase6_yza_requires_manual_chrome_and_farmatodo_stays_automatic():
    for mode in ("probe", "capture"):
        with pytest.raises(ValueError, match="operador"):
            phase6.commands_for(mode, "farmacias-yza", "", 20)

        commands = phase6.commands_for(mode, "farmatodo", "", 20)
        assert commands
        assert all("--cdp-url" not in command for command in commands)
        assert all("--category" in command for command in commands)
        assert all("--headed" in command for command in commands)


def test_phase6_yza_manual_cdp_is_forwarded_without_automatic_browser():
    for mode in ("probe", "capture"):
        commands = phase6.commands_for(
            mode, "farmacias-yza", "http://127.0.0.1:9222", 20
        )
        assert commands
        assert all("--cdp-url" in command for command in commands)
        assert all(
            command[command.index("--cdp-url") + 1]
            == "http://127.0.0.1:9222" for command in commands
        )
        assert all("--headed" not in command for command in commands)



def test_phase6_manual_chrome_wrapper_accepts_chrome_144_inspect_ws():
    """New Chrome inspect CDP does NOT expose /json/version or /json/list."""
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
    # Neither endpoint is a hard requirement: Chrome M144+ can answer 404.
    assert 'Invoke-RestMethod -Uri "$Base/json/list"' not in script
    assert '$Version.webSocketDebuggerUrl' in script  # legacy fallback only


def test_phase6_yza_playwright_override_is_isolated_from_production():
    root = phase6.ROOT
    prod = (root / "requirements.txt").read_text(encoding="utf-8")
    pilot = (root / "requirements-phase6-yza-cdp.txt").read_text(
        encoding="utf-8"
    )
    phase_runner = (root / "scripts" / "run_phase6_pilot.py").read_text(
        encoding="utf-8"
    )
    assert "playwright==1.54.0" in prod
    assert "playwright==1.60.0" in pilot
    assert 'major_minor < (1, 60)' in phase_runner
    assert "requirements-phase6-yza-cdp.txt" in phase_runner


def test_phase6_rejects_remote_cdp_endpoint():
    with pytest.raises(ValueError, match="local"):
        phase6.commands_for("probe", "farmacias-yza",
                            "ws://198.51.100.20:9222", 20)


@pytest.mark.parametrize("retailer,categories,cp,valid_store", [
    ("farmacias-yza", ["higiene-bucal", "medicamentos-respiratorio"], "11590", True),
    ("farmatodo", ["cuidado-bucal", "respiratorios", "higiene-personal"], None, False),
])
def test_capture_validation_checks_record_and_postal_scope(
    tmp_path, monkeypatch, retailer, categories, cp, valid_store
):
    root = tmp_path / retailer
    root.mkdir()
    workbook = root / "catalog.xlsx"
    report = root / "report.json"
    day = "2026-10-26"
    name = phase6.EXPECTED[retailer]["name"]
    rows = []
    summary = []
    for index, category in enumerate(categories):
        rows.append({
            "retailer": name, "category_id": category,
            "scrape_timestamp": day + "T10:00:00",
            "sku": f"sku-{index}", "url": f"https://example.org/{index}",
            "price_current": 50 + index, "postal_code": cp,
            "store_id": None, "store_context_verified": valid_store,
        })
        summary.append({
            "category_id": category, "capture_date": day,
            "status": "SUCCESS" if valid_store else "COMPLETE",
            "products": 1,
        })
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="Concentrado", index=False)
        pd.DataFrame(summary).to_excel(writer, sheet_name="Resumen", index=False)
    payload = {
        "capture_date": day, "master_modified": False,
        "complete_pilot": valid_store, "postal_code": "11590" if valid_store else None,
        "complete": not valid_store, "postal_verification_required": False,
    }
    report.write_text(json.dumps(payload), encoding="utf-8")
    metadata = phase6.EXPECTED[retailer].copy()
    metadata["workbook"] = workbook
    metadata["report"] = report
    monkeypatch.setitem(phase6.EXPECTED, retailer, metadata)
    result = phase6.validate_capture(retailer, day)
    assert result["categories"] == len(categories)
    assert result["products"] == len(categories)

    # Deliberate wrong region must be rejected for both retailers.
    edited = pd.DataFrame(rows)
    edited.loc[0, "postal_code"] = "44220"
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        edited.to_excel(writer, sheet_name="Concentrado", index=False)
        pd.DataFrame(summary).to_excel(writer, sheet_name="Resumen", index=False)
    with pytest.raises(ValueError):
        phase6.validate_capture(retailer, day)


def test_phase6_only_writes_isolated_diagnostics():
    code = (phase6.ROOT / "scripts" / "run_phase6_pilot.py").read_text(
        encoding="utf-8"
    )
    assert "current_master.json" not in code
    assert "integrate_complete_retailers" not in code
    assert 'PHASE6_REPORT = ROOT / "diagnostics" / "phase6"' in code
