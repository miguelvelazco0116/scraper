"""Read-only Soriana postal-rotation pilot reporting for existing Streamlit.

Do not infer production integration from CLI exit code or progress JSON.
Only the independent audit of four XLSX files can certify all 24 cases.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


RETAILER_TARGET = "Soriana (rotación CP)"
CODES = ("83420", "44220", "54030", "97245")
EXPECTED_CATEGORIES = 6
EXPECTED_CASES = 24
PROGRESS_SCHEMA = "capture-progress-v1"


def read_progress(root: Path, day: str) -> dict | None:
    """Load all/all progress only from the local known path for the current day."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return None
    path = (root / "diagnostics" / "soriana_cp_rotation"
            / f"progress_{day}_all_all.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (data.get("schema_version") != PROGRESS_SCHEMA
            or data.get("retailer") != "Soriana"
            or data.get("capture_date") != day
            or data.get("total_cases") != EXPECTED_CASES
            or not isinstance(data.get("cases"), list)
            or len(data["cases"]) != EXPECTED_CASES):
            return None
        keys = [(str(c.get("postal_code")), str(c.get("category_id")))
                for c in data["cases"]]
        if len(set(keys)) != EXPECTED_CASES:
            return None
        if any(cp not in CODES for cp, _ in keys):
            return None
        if any(sum(cp == code for cp, _ in keys) != EXPECTED_CATEGORIES
               for code in CODES):
            return None
        return data
    except (OSError, ValueError, TypeError, KeyError):
        return None


def audit_snapshot(root: Path, day: str) -> dict:
    """Re-audit local Excel workbooks; never modify them or the master."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("Fecha de auditoría inválida")
    from scripts.audit_soriana_cp_rotation import audit
    rows = audit(
        day, output_dir=root / "output" / "pilots" / "soriana_cp_rotation",
    )
    complete = sum(row["status"] == "COMPLETE" for row in rows)
    products = sum(int(row["products"]) for row in rows if row["status"] == "COMPLETE")
    return {
        "day": day,
        "complete": complete,
        "total": len(rows),
        "validated_products": products,
        "certified": complete == EXPECTED_CASES and len(rows) == EXPECTED_CASES,
        "cases": rows,
        "issues": [r for r in rows if r["status"] != "COMPLETE"],
        "master_modified": False,
    }
