"""Offline tests for scoped Duero quality and Streamlit existing-capture import."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from dashboard.existing_integration import inspect_existing_capture
from dashboard.execution import (
    ALL, prepare_existing_import, start_existing_import, job_path,
)


def test_duero_public_catalogue_passes_without_store_but_strict_mode_rejects():
    from integrate_validated_phase import _validate_duero_retailer
    from integrate_complete_retailers import _quality

    categories = [
        "cuidado-bucal", "desodorantes-y-antitranspirantes",
        "cuidado-facial-y-corporal", "farmacia", "detergentes",
        "cloros-y-blanqueadores",
    ]
    frame = pd.DataFrame([
        {
            "retailer": "Mayorista Duero", "category_id": category,
            "sku": str(index), "url": "https://example.com/p/" + str(index),
            "price_current": 25.0, "is_available": 1, "package_type": "PIEZA",
            "units_per_package": 1, "store_context_verified": False,
            "store_context_method": "public_collection_api_no_postal_code",
            "postal_code": None, "scrape_timestamp": "2100-01-01T10:00:00",
        }
        for index, category in enumerate(categories)
    ])
    summary = pd.DataFrame([
        {
            "retailer": "Mayorista Duero", "category_id": category,
            "status": "SUCCESS", "products": 1,
            "pagination_complete": True, "pagination_verified": True,
            "independent_match": True,
            "terminal_page_limit50": 3, "terminal_page_limit250": 3,
            "variants_seen_api": 1, "sku_complete": 1,
            "merchant_sku_complete": 1, "price_complete": 1,
            "url_complete": 1, "availability_complete": 1,
            "pack_units_complete": 1, "unit_price_complete": 1,
            "store_context_verified": False, "blocked_detected": False,
        }
        for category in categories
    ])
    assert _validate_duero_retailer(
        frame, summary, allow_online_catalogue=True
    ) == []
    assert any(
        "contexto de tienda" in note
        for note in _validate_duero_retailer(frame, summary)
    )
    _quality(5, "Mayorista Duero", frame, summary.assign(status="COMPLETE"))


def test_existing_capture_fails_closed_when_previous_phase_is_missing():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        master = root / "master.xlsx"
        master.touch()
        with patch("dashboard.existing_integration.local_day", return_value="2100-01-01"), \
             patch("dashboard.existing_integration.active_pointer", return_value={
                 "capture_date": "2100-01-01",
                 "master": str(master),
                 "completed_phases": [1, 2, 3],
             }):
            with pytest.raises(ValueError, match="4"):
                inspect_existing_capture(root, 5, "Mayorista Duero")


def test_existing_capture_checks_today_and_runs_quality_without_scraping():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        master = root / "master.xlsx"
        master.touch()
        frame = pd.DataFrame([{"retailer": "Mayorista Duero"}])
        summary = pd.DataFrame([{"retailer": "Mayorista Duero"}])
        source = root / "output" / "duero.xlsx"
        with patch("dashboard.existing_integration.local_day", return_value="2100-01-01"), \
             patch("dashboard.existing_integration.active_pointer", return_value={
                 "capture_date": "2100-01-01", "master": str(master),
                 "completed_phases": [1, 2, 3, 4],
             }), \
             patch("integrate_complete_retailers._candidates",
                   return_value=iter([
                       ("Mayorista Duero", frame, summary, source, [])
                   ])) as candidates, \
             patch("integrate_complete_retailers._quality") as quality, \
             patch("integrate_complete_retailers._date_check") as date_check:
            result = inspect_existing_capture(root, 5, "Mayorista Duero")
        assert result["products"] == 1
        assert result["scope"].startswith("PUBLIC_ONLINE")
        candidates.assert_called_once_with(
            5, "2100-01-01", only_retailer="Mayorista Duero"
        )
        quality.assert_called_once()
        date_check.assert_called_once()


def test_existing_import_allowlist_and_exact_command():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        script = root / "scripts" / "integrate_complete_retailers.py"
        script.parent.mkdir()
        script.touch()
        with patch("dashboard.execution.os", SimpleNamespace(name="nt")), \
             patch("dashboard.existing_integration.inspect_existing_capture",
                   return_value={"products": 659}):
            spec = prepare_existing_import(root, 5, "Mayorista Duero")
        assert spec["job_type"] == "integracion_existente"
        assert spec["mode"] == "captura_integracion"
        assert spec["command"][-4:] == [
            "--phase", "5", "--retailer", "Mayorista Duero"
        ]


def test_existing_import_creates_job_but_not_scraper_job():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        spec = {
            "runner": "integrate_complete_retailers.py",
            "capture_date": "2100-01-01",
        }
        process = Mock()
        process.poll.return_value = None
        with patch("dashboard.execution.prepare_existing_import", return_value=spec), \
             patch("dashboard.execution.current_active_job", return_value=None), \
             patch("dashboard.execution.subprocess.Popen", return_value=process):
            job = start_existing_import(root, 5, "Mayorista Duero")
        saved = json.loads(job_path(root, job["run_id"]).read_text())
        assert saved["job_type"] == "integracion_existente"
        assert saved["target"] == "Mayorista Duero"
        assert saved["mode"] == "captura_integracion"
