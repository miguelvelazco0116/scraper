"""Verified same-day Phase 1 inventory, including isolated Chedraui checkpoints.

A COMPLETE label alone never makes a case reusable. Chedraui cases are
rechecked against the immutable store/date/category/quality contract.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

STORES = (
    "chedraui-polanco",
    "chedraui-tepeyac",
    "chedraui-cancun-kabah",
    "chedraui-puebla-centro-sur",
)

def today():
    return datetime.now().astimezone().date().isoformat()

def _normal_id(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip().removesuffix(".0")

def phase1_inventory(root: Path, day: str | None = None):
    """Return (case dataframe, verified mapping) for this exact capture date."""
    day = day or today()
    scripts = str(root / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from run_phase1_production import (
        PROGRESS_PATH, OUTPUT_PATH, _read_phase1_snapshot, _case_key,
    )
    from chedraui_case_cache import load_valid_snapshots, validate_case
    from scraper.config import load_categories, load_locations

    verified = _read_phase1_snapshot(PROGRESS_PATH)
    if not verified:
        verified = _read_phase1_snapshot(OUTPUT_PATH)

    # Recheck the exact store/category and quality contract for every Chedraui
    # case from the mutable progress file. Trust neither store labels nor a
    # summary flag when its row-level evidence does not match.
    for key, (frame, summary) in tuple(verified.items()):
        if key[0] != "Chedraui":
            continue
        if validate_case(frame, summary, category_id=key[1], store_id=key[2], day=day):
            del verified[key]

    for key, item in load_valid_snapshots(root, day=day).items():
        verified.setdefault(key, item)

    errors = {}
    progress = root / "output" / "fase1_progress.xlsx"
    if progress.is_file():
        try:
            control = pd.read_excel(progress, sheet_name="Control")
            if not control.empty and str(control.iloc[0].get("capture_date", ""))[:10] == day:
                summary = pd.read_excel(progress, sheet_name="Resumen",
                                        dtype={"category_id": str, "store_id": str})
                for _, row in summary.iterrows():
                    retailer = str(row.get("retailer") or "")
                    key = _case_key(retailer, str(row.get("category_id") or ""), row.get("store_id"))
                    errors[key] = (str(row.get("status") or "").upper(), str(row.get("quality_notes") or ""))
        except (ValueError, KeyError, OSError):
            pass

    locations = {loc.id: loc for loc in load_locations(root / "config" / "locations.yaml")}
    records = []
    for retailer, config in (
        ("Farmacias del Ahorro", "farmacias-del-ahorro"),
        ("Ibarra Mayoreo", "ibarra-mayoreo"),
    ):
        for category in load_categories(root / "config" / config / "categories.yaml"):
            key = _case_key(retailer, category.id)
            records.append(_row(1, retailer, "", "", category.id, key, verified, errors))
    chedraui_categories = load_categories(root / "config" / "chedraui" / "categories.yaml")
    for store in STORES:
        loc = locations[store]
        for category in chedraui_categories:
            key = _case_key("Chedraui", category.id, loc.store_id)
            records.append(_row(1, "Chedraui", store, str(loc.store_id),
                                category.id, key, verified, errors))
    return pd.DataFrame(records), verified

def _row(phase, retailer, store, store_id, category, key, verified, errors):
    saved = verified.get(key)
    past_status, note = errors.get(key, ("PENDIENTE", ""))
    if saved:
        frame, _ = saved
        status = "COMPLETE"
        count = len(frame)
        note = "Caso validado del día; no volver a descargar"
    else:
        status = past_status if past_status in {"ERROR", "BLOCKED", "PARTIAL", "STORE_CONTEXT_ERROR"} else "PENDIENTE"
        count = 0
        if past_status == "COMPLETE":
            status = "REVISAR"
            note = "La etiqueta COMPLETE no superó la validación de datos y contexto."
    return {
        "fase": phase, "retailer": retailer, "tienda": store,
        "store_id": store_id, "categoria": category,
        "estado": status, "productos_validados": count,
        "accion": "OMITIR" if saved else "REINTENTAR",
        "detalle": note[:280],
    }
