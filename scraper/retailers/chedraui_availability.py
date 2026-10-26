"""Chedraui storefront CTA evidence, scoped to one product card.

The purchase control "Agregar" is evidence of online availability, provided
it is enabled. Lack of a purchase control is not evidence of stock-out.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from ..availability import AVAILABLE, UNAVAILABLE, UNKNOWN


def _normalize(raw: object) -> str:
    decomposed = unicodedata.normalize("NFKD", str(raw or "").casefold())
    folded = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", folded).strip()


_ADD = re.compile(r"^(?:agregar(?:\s+al\s+carrito)?|anadir(?:\s+al\s+carrito)?)(?:\s*\+)?$")
_SOLD_OUT = re.compile(
    r"\b(?:agotad[oa]s?|sin\s+existencia(?:s)?|sin\s+stock|fuera\s+de\s+stock|out\s+of\s+stock)\b"
)


def from_chedraui_card_controls(
    controls: list[dict[str, Any]] | None, *,
    card_text: str = "",
) -> dict[str, Any]:
    """Classify explicitly observed controls inside one Chedraui product tile.

    Only a visible enabled buy control makes a product AVAILABLE.
    Explicit product-level stock-out text makes it UNAVAILABLE.
    Missing, disabled or contradictory signals remain UNKNOWN.
    """
    positives: list[str] = []
    negatives: list[str] = []
    for control in controls or []:
        if not isinstance(control, dict) or not control.get("visible", True):
            continue
        label = str(control.get("text") or "").strip()
        norm = _normalize(label)
        if not norm:
            continue
        if _SOLD_OUT.search(norm):
            negatives.append(label)
        elif _ADD.fullmatch(norm) and not control.get("disabled", False):
            positives.append(label)

    # A sold-out label may be plain visible text rather than a button.
    # Restrict fallback to explicit stock-out vocabulary; never infer
    # availability merely because the card has a price or a CTA is absent.
    stock_out = _SOLD_OUT.search(_normalize(card_text))
    if stock_out and not negatives:
        negatives.append(str(card_text)[:140])

    if positives and negatives:
        return {
            "availability_status": UNKNOWN,
            "is_available": None,
            "availability_raw": (
                "CONFLICTO_DOM: " + " | ".join((positives + negatives)[:4])
            )[:500],
        }
    if negatives:
        return {
            "availability_status": UNAVAILABLE,
            "is_available": False,
            "availability_raw": "dom_sin_stock: " + negatives[0][:450],
        }
    if positives:
        return {
            "availability_status": AVAILABLE,
            "is_available": True,
            "availability_raw": "dom_cta: " + positives[0][:450],
        }
    return {
        "availability_status": UNKNOWN,
        "is_available": None,
        "availability_raw": "dom_sin_senal_verificable",
    }
