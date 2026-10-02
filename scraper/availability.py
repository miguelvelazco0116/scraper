from __future__ import annotations

import json
import re
from typing import Any


AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
UNKNOWN = "UNKNOWN"

_NEGATIVE_PATTERNS = (
    r"\bagotad[oa]s?\b",
    r"\bsin\s+existencia\b",
    r"\bsin\s+stock\b",
    r"\bno\s+disponible\b",
    r"\bno\s+disponibles\b",
    r"\btemporalmente\s+no\s+disponible\b",
    r"\bfuera\s+de\s+stock\b",
    r"\bout\s+of\s+stock\b",
    r"\bunavailable\b",
)

_POSITIVE_PATTERNS = (
    r"\bagregar\s+al\s+carrito\b",
    r"\bañadir\s+al\s+carrito\b",
    r"\bcomprar\s+ahora\b",
    r"\ben\s+stock\b",
    r"\bin\s+stock\b",
    r"\bdisponible\b",
)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def availability_from_text(text: Any) -> tuple[str, bool | None, str | None]:
    """Normaliza señales textuales de disponibilidad.

    Los marcadores negativos se evalúan antes que los positivos para evitar
    clasificar "no disponible" como AVAILABLE sólo por contener "disponible".
    """
    raw = _clean(text)
    if not raw:
        return UNKNOWN, None, None

    folded = raw.casefold()
    for pattern in _NEGATIVE_PATTERNS:
        match = re.search(pattern, folded, flags=re.IGNORECASE)
        if match:
            return UNAVAILABLE, False, raw

    for pattern in _POSITIVE_PATTERNS:
        match = re.search(pattern, folded, flags=re.IGNORECASE)
        if match:
            return AVAILABLE, True, raw

    return UNKNOWN, None, raw


def _iter_mapping_values(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path, child
            if isinstance(child, (dict, list, tuple)):
                yield from _iter_mapping_values(child, path)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            path = f"{prefix}[{index}]"
            yield path, child
            if isinstance(child, (dict, list, tuple)):
                yield from _iter_mapping_values(child, path)


def availability_from_mapping(payload: Any) -> tuple[str, bool | None, str | None]:
    """Busca señales comunes de stock/disponibilidad en respuestas JSON."""

    if not isinstance(payload, (dict, list, tuple)):
        return availability_from_text(payload)

    bool_keys = {
        "available",
        "isavailable",
        "instock",
        "hasstock",
        "buyable",
        "purchasable",
        "canaddtocart",
    }
    status_keys = {
        "availability",
        "availabilitystatus",
        "stockstatus",
        "stocklevelstatus",
        "inventorystatus",
        "status",
    }
    numeric_keys = {
        "stocklevel",
        "quantity",
        "availablequantity",
        "inventory",
        "inventoryquantity",
    }

    observations: list[tuple[str, Any]] = []
    for path, value in _iter_mapping_values(payload):
        key = re.sub(r"[^a-z0-9]", "", path.split(".")[-1].casefold())
        if key in bool_keys:
            if isinstance(value, bool):
                raw = f"{path}={value}"
                return (AVAILABLE, True, raw) if value else (UNAVAILABLE, False, raw)
            if isinstance(value, (int, float)) and value in (0, 1):
                raw = f"{path}={value}"
                return (AVAILABLE, True, raw) if value == 1 else (UNAVAILABLE, False, raw)
            observations.append((path, value))
        elif key in status_keys:
            observations.append((path, value))
        elif key in numeric_keys and isinstance(value, (int, float)):
            raw = f"{path}={value}"
            if value <= 0:
                return UNAVAILABLE, False, raw
            return AVAILABLE, True, raw

    for path, value in observations:
        status, available, raw_text = availability_from_text(value)
        if status != UNKNOWN:
            return status, available, f"{path}={raw_text}"

        normalized = _clean(value).casefold()
        if normalized in {"instock", "in_stock", "available", "lowstock", "low_stock"}:
            return AVAILABLE, True, f"{path}={value}"
        if normalized in {"outofstock", "out_of_stock", "unavailable", "notavailable"}:
            return UNAVAILABLE, False, f"{path}={value}"

    try:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        raw = _clean(payload)

    return UNKNOWN, None, raw[:2000] if raw else None


def availability_fields(
    *,
    text: Any = None,
    payload: Any = None,
) -> dict[str, Any]:
    """Devuelve las tres columnas canónicas de disponibilidad."""

    if payload is not None:
        status, available, raw = availability_from_mapping(payload)
        if status != UNKNOWN:
            return {
                "availability_status": status,
                "is_available": available,
                "availability_raw": raw,
            }

    status, available, raw = availability_from_text(text)
    return {
        "availability_status": status,
        "is_available": available,
        "availability_raw": raw,
    }
