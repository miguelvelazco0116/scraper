"""Shared, UI-independent case progress contract for CLI scrapers.

Keeps the COMPLETE / REINTENTAR semantics already used by Chedraui.
Never infers completion from a successful process exit; only validated cases.
The read-only JSON snapshot is intended for the existing Streamlit monitor
after the retailer's live pilot is certified.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = "capture-progress-v1"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_json_atomic(path: Path, payload: dict) -> None:
    """Atomic, Windows-safe updates (same bounded sharing retries as dashboard)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp." + uuid.uuid4().hex)
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        for attempt in range(9):
            try:
                os.replace(temporary, path)
                return
            except PermissionError as exc:
                if (os.name != "nt" or getattr(exc, "winerror", None) not in (5, 32)
                        or attempt == 8):
                    raise
                time.sleep(min(0.05 * 2 ** attempt, 0.8))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except PermissionError:
            pass


class CaptureProgress:
    """Persist individual cases and each attempt without touching the master."""

    def __init__(
        self, *, path: Path, retailer: str, capture_date: str,
        cases: list[tuple[str, str]],
    ) -> None:
        if len(set(cases)) != len(cases) or not cases:
            raise ValueError("Los casos deben ser únicos y no vacíos")
        self.path = path
        self.retailer = retailer
        self.capture_date = capture_date
        self.created_at = _now()
        self.cases = {
            (location, category): {
                "location": location, "postal_code": location,
                "category_id": category,
                "estado": "PENDING", "accion": "REINTENTAR",
                "productos_validados": 0, "attempts": 0,
                "retry_history": [], "quality_notes": "",
            }
            for location, category in cases
        }

    def reuse(self, location: str, category: str, *, products: int) -> None:
        item = self.cases[location, category]
        if products < 1:
            raise ValueError("No se pueden reutilizar capturas vacías")
        item.update(
            estado="COMPLETE", accion="OMITIR",
            productos_validados=products, quality_notes="Checkpoint válido del día",
        )
        self.save()

    def start(self, location: str, category: str, *, attempt: int) -> None:
        item = self.cases[location, category]
        if not 1 <= attempt <= 3:
            raise ValueError("Máximo tres intentos por categoría y CP")
        if item["estado"] == "COMPLETE":
            raise ValueError("No repetir un caso COMPLETE")
        item.update(estado="RUNNING", accion="CAPTURANDO", attempts=attempt)
        self.save()

    def finish_attempt(
        self, location: str, category: str, *, attempt: int,
        status: str, products: int, error: str | None = None,
        quality_notes: str = "", diagnostic: str | None = None,
    ) -> None:
        item = self.cases[location, category]
        if item["attempts"] != attempt or item["estado"] != "RUNNING":
            raise ValueError("Intento fuera de secuencia")
        if status not in {"COMPLETE", "PARTIAL", "BLOCKED"}:
            raise ValueError("Estado de intento desconocido")
        item["retry_history"].append({
            "attempt": attempt, "status": status,
            "rows": int(products), "error": error,
            "diagnostic": diagnostic,
        })
        # A successful extraction is NOT COMPLETE until its workbook is
        # atomically persisted and re-read through the same-day cache validator.
        provisional = "CAPTURED" if status == "COMPLETE" else status
        item.update(
            estado=provisional, accion=("VALIDAR" if status == "COMPLETE"
                    else "REVISAR" if status == "BLOCKED" else "REINTENTAR"),
            productos_validados=0,
            quality_notes=quality_notes,
        )
        self.save()

    def snapshot(self) -> dict:
        values = list(self.cases.values())
        complete = sum(v["estado"] == "COMPLETE" for v in values)
        return {
            "schema_version": SCHEMA_VERSION,
            "retailer": self.retailer,
            "capture_date": self.capture_date,
            "created_at": self.created_at,
            "updated_at": _now(),
            "status": ("COMPLETE" if complete == len(values)
                       else "RUNNING" if any(v["estado"] == "RUNNING" for v in values)
                       else "PARTIAL"),
            "total_cases": len(values), "complete_cases": complete,
            "pending_cases": len(values) - complete,
            "attempts_total": sum(v["attempts"] for v in values),
            "products_validated": sum(v["productos_validados"] for v in values),
            "master_modified": False,
            "cases": values,
        }

    def save(self) -> None:
        _write_json_atomic(self.path, self.snapshot())
