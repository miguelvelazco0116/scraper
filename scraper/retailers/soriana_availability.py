"""Clasificación conservadora del botón de compra por producto en Soriana.

Nunca confundir ausencia del botón con agotado, ni disponibilidad online
con inventario de una tienda física.
"""
from __future__ import annotations

import re
import unicodedata

from ..availability import AVAILABLE, UNAVAILABLE, UNKNOWN


def _normalize(text: object) -> str:
    raw = unicodedata.normalize("NFKD", str(text or "").casefold())
    plain = "".join(char for char in raw if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", plain).strip()


def classify_soriana_availability(
    controls: list[str] | None, *, tile_text: str = ""
) -> tuple[str, bool | None, str | None]:
    """Return status, is_available and evidence for one product card only.

    - Agregar / Agregar + = available online.
    - Agotado en línea = unavailable online.
    - Missing or conflicting controls = unknown.

    Never use generic product descriptions, promotional text or controls from
    other tiles as positive availability evidence.
    """
    labels = [str(item).strip() for item in controls or [] if str(item).strip()]
    normalized = [_normalize(x) for x in labels]
    negative = any(
        re.search(r"\bagotad[oa]s?\s+en\s+linea\b", label)
        for label in normalized
    )
    positive = any(
        re.fullmatch(
            r"(?:agregar\s*(?:\+|al carrito)?|anadir\s+al carrito)",
            label,
        )
        for label in normalized
    )
    # The stock warning can be a non-button element in the same product tile.
    if not negative and re.search(
        r"\bagotad[oa]s?\s+en\s+linea\b", _normalize(tile_text)
    ):
        negative = True

    evidence = " | ".join(labels)[:500] or None
    if negative and positive:
        return UNKNOWN, None, evidence
    if negative:
        return UNAVAILABLE, False, evidence or "Agotado en línea (tarjeta)"
    if positive:
        return AVAILABLE, True, evidence
    return UNKNOWN, None, evidence
