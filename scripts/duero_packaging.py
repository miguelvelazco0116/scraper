from __future__ import annotations

"""Conservative interpretation of Duero Shopify product-variant pack labels.

"Product title 4 Pzas - 1 Pieza" means ONE saleable retail presentation,
not four cases. Count the explicit *variant option* before considering a
packaging phrase in the product title. A title's plain "4 pzas" can describe
the consumer product's contents, not the number of saleable presentations.
Unknown stays unknown; never default to 1.
"""

import re
from dataclasses import dataclass

QUANTITY = r"(?P<count>\d{1,4})"
UNIT = r"(?:piezas?|pzas?|pzs?|unidades?|uds?)"
PACK = r"(?P<kind>caja|paquete|pack|exhibidor|display|blister)"

PACKAGE_PREFIX = re.compile(
    rf"(?i)\b{PACK}\s*(?:de|con|x|por)?\s*{QUANTITY}\s*{UNIT}\b"
)
PACKAGE_SUFFIX = re.compile(
    rf"(?i)\b{QUANTITY}\s*{UNIT}\s*(?:por|x|/|en|de)\s*{PACK}\b"
)
VARIANT_PIECES = re.compile(
    rf"(?i)^\s*{QUANTITY}\s*{UNIT}\s*$"
)
VARIANT_SINGLE = re.compile(
    r"(?i)^\s*(?:1\s*)?(?:pieza|pza|unidad|ud)\s*$"
)


@dataclass(frozen=True)
class PackEvidence:
    units: int | None
    package_type: str | None
    evidence: str | None


KINDS = {
    "caja": "CAJA",
    "paquete": "PAQUETE",
    "pack": "PACK",
    "exhibidor": "EXHIBIDOR",
    "display": "DISPLAY",
    "blister": "BLISTER",
}


def _match_pack(text: str) -> PackEvidence | None:
    for pattern in (PACKAGE_PREFIX, PACKAGE_SUFFIX):
        match = pattern.search(text)
        if match:
            count = int(match.group("count"))
            if 1 <= count <= 1000:
                return PackEvidence(
                    count, KINDS[match.group("kind").casefold()],
                    match.group(0),
                )
    return None


def classify_pack(title: str | None, variant_title: str | None) -> PackEvidence:
    """Return the saleable variant's pack count only when text supports it."""
    variant = str(variant_title or "").strip()
    product = str(title or "").strip()
    if variant and variant.casefold() not in ("default title", "default"):
        explicit = _match_pack(variant)
        if explicit:
            return explicit
        match = VARIANT_PIECES.fullmatch(variant)
        if match:
            count = int(match.group("count"))
            if 1 <= count <= 1000:
                return PackEvidence(
                    count, "PIEZA" if count == 1 else "PAQUETE",
                    variant,
                )
        if VARIANT_SINGLE.fullmatch(variant):
            return PackEvidence(1, "PIEZA", variant)

        # Never infer from title if variant has an explicit but unparsed
        # unit/pack option. It may change how that title is sold.
        return PackEvidence(None, None, None)

    # An unconfigured/default option may use explicit "Caja de 12 piezas"
    # in the product title, but never an unqualified "12 pzas" name.
    explicit = _match_pack(product)
    if explicit:
        return explicit
    return PackEvidence(None, None, None)


def infer_from_exported_name(name: str | None) -> PackEvidence:
    """Audit only: exported names use '<Shopify title> - <variant title>'."""
    value = str(name or "").strip()
    if " - " in value:
        title, variant = value.rsplit(" - ", 1)
        return classify_pack(title, variant)
    return classify_pack(value, "Default Title")
