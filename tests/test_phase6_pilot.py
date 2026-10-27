"""Offline Phase 6 tests: YZA and Farmatodo online without locality claims."""
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
    assert all(r["postal_code"] is None for r in manifest["retailers"])
    assert all(r["postal_mode"] == "NO_VERIFICATION" for r in manifest["retailers"])
    assert phase6.EXPECTED["farmacias-yza"]["workbook"].name == "yza_online_test.xlsx"


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


def test_shared_playwright_dependency_is_unified():
    root = phase6.ROOT
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    runner = (root / "scripts" / "run_phase6_pilot.py").read_text(
        encoding="utf-8"
    )
    assert "playwright==1.60.0" in requirements
    assert "requirements-phase6-yza-cdp.txt" not in runner
    assert "requirements.txt" in runner
    assert 'major_minor < (1, 60)' in runner



def test_phase6_rejects_remote_cdp_endpoint():
    with pytest.raises(ValueError, match="local"):
        phase6.commands_for("probe", "farmacias-yza",
                            "ws://198.51.100.20:9222", 20)


@pytest.mark.parametrize("retailer,categories", [
    ("farmacias-yza", ["higiene-bucal", "medicamentos-respiratorio"]),
    ("farmatodo", ["cuidado-bucal", "respiratorios", "higiene-personal"]),
])
def test_capture_validation_requires_no_postcode_but_complete_products(
    tmp_path, monkeypatch, retailer, categories
):
    root = tmp_path / retailer
    root.mkdir()
    workbook = root / "catalog.xlsx"
    report = root / "report.json"
    day = "2026-10-27"
    name = phase6.EXPECTED[retailer]["name"]
    rows, summaries = [], []
    for index, category in enumerate(categories):
        rows.append({
            "retailer": name, "category_id": category,
            "scrape_timestamp": day + "T10:00:00",
            "sku": f"sku-{index}", "url": f"https://example.org/{index}",
            "price_current": 50 + index, "postal_code": None,
            "store": None, "store_id": None,
            "store_context_verified": False,
        })
        summaries.append({
            "category_id": category, "capture_date": day,
            "status": "SUCCESS" if retailer == "farmacias-yza" else "COMPLETE",
            "products": 1, "postal_code": None,
            "store_context_verified": False,
            "postal_verification_required": False,
            "sku_pdp_verified": 1 if retailer == "farmatodo" else None,
        })

    def write_book(products):
        with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
            pd.DataFrame(products).to_excel(
                writer, sheet_name="Concentrado", index=False
            )
            pd.DataFrame(summaries).to_excel(
                writer, sheet_name="Resumen", index=False
            )
            if retailer == "farmatodo":
                pd.DataFrame([
                    {
                        "category_id": p["category_id"],
                        "sku": p["sku"], "url": p["url"],
                        "sku_source": "pdp_visible_sku",
                        "sku_verified": True,
                    }
                    for p in rows
                ]).to_excel(writer, sheet_name="SkuAudit", index=False)

    write_book(rows)
    report_obj = {
        "capture_date": day, "master_modified": False,
        "postal_code": None,
        "store_context_verified": False,
        "complete_pilot": retailer == "farmacias-yza",
        "complete": retailer == "farmatodo",
        "postal_verification_required": False,
        "scope": "ONLINE_CATALOGUE_NO_LOCATION_VERIFICATION",
    }
    report.write_text(json.dumps(report_obj), encoding="utf-8")
    metadata = phase6.EXPECTED[retailer].copy()
    metadata["workbook"] = workbook
    metadata["report"] = report
    monkeypatch.setitem(phase6.EXPECTED, retailer, metadata)

    validated = phase6.validate_capture(retailer, day)
    assert validated["categories"] == len(categories)
    assert validated["products"] == len(categories)
    assert validated["scope"] == "ONLINE_CATALOGUE_NO_LOCATION_VERIFICATION"

    # No certified locality must be fabricated (even if the web UI happens
    # to show a postal code the operator previously typed).
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
    if retailer == "farmacias-yza":
        stale_report = dict(report_obj, postal_code="11590")
        report.write_text(json.dumps(stale_report), encoding="utf-8")
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


def test_phase6_only_writes_isolated_diagnostics():
    code = (phase6.ROOT / "scripts" / "run_phase6_pilot.py").read_text(
        encoding="utf-8"
    )
    assert "current_master.json" not in code
    assert "integrate_complete_retailers" not in code
    assert 'PHASE6_REPORT = ROOT / "diagnostics" / "phase6"' in code
