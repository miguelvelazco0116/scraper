"""Validate an already captured retailer for same-day master integration.

This module never scrapes, writes files, or accepts arbitrary input paths.
"""
from __future__ import annotations

import sys
from pathlib import Path

from dashboard.execution import ALL, RUNNERS, active_pointer, local_day


def inspect_existing_capture(root: Path, phase: int, retailer: str) -> dict:
    """Read-only preview with the integrator's existing quality/date gates."""
    if phase not in RUNNERS or retailer not in RUNNERS[phase] or retailer == ALL:
        raise ValueError("Retailer no autorizado para esta fase.")
    day = local_day()

    if phase > 1:
        pointer = active_pointer(root)
        raw = str(pointer.get("master") or "").strip()
        master = Path(raw) if raw else None
        if master is not None and not master.is_absolute():
            master = root / master
        if (pointer.get("capture_date") != day or master is None
                or not master.is_file()):
            raise ValueError("El master activo debe existir y corresponder a hoy.")
        missing = [
            number for number in range(1, phase)
            if number not in pointer.get("completed_phases", [])
        ]
        if missing:
            raise ValueError(
                "Primero integra cronológicamente las fases: "
                + ", ".join(map(str, missing))
            )

    scripts = str(root / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from integrate_complete_retailers import _candidates, _quality, _date_check

    selected = list(_candidates(phase, day, only_retailer=retailer))
    if len(selected) != 1:
        raise ValueError("La fuente del retailer no está configurada.")
    name, frame, summary, source, errors = selected[0]
    if errors:
        raise ValueError("; ".join(errors))
    _quality(phase, name, frame, summary)
    _date_check(frame, summary.iloc[0:0], day)

    return {
        "phase": phase,
        "retailer": name,
        "capture_date": day,
        "source": str(source),
        "products": len(frame),
        "cases": len(summary),
        "scope": (
            "PUBLIC_ONLINE_CATALOGUE_NO_POSTAL_PRICE_CERTIFICATION"
            if name == "Mayorista Duero" else "STANDARD_VERIFIED"
        ),
    }
