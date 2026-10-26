"""Soriana delivery postal-code selection; grounded in the visible UI.

The user demonstrated a red "Elige cómo quieres recibir tu pedido" header
control, a delivery modal with a textbox currently containing a CP (e.g.
03100), the red "Buscar" button, and the selected postcode in the top-left
header beside the yellow shopping-bag icon (e.g. 52164). Only the latter,
outside the modal, can certify the intended CP.

No postal attribution is allowed from a filled textbox or navigation alone.
"""
from __future__ import annotations

import re
from typing import Any


class SorianaPostalContextError(RuntimeError):
    pass


def check_postal_code(cp: str) -> str:
    code = str(cp).strip()
    if not re.fullmatch(r"\d{5}", code):
        raise ValueError("El CP debe contener exactamente 5 dígitos.")
    return code


# Minimal displayed-element evidence. The top strip in the reference
# screenshots shows the selected delivery CP by a yellow shopping bag icon.
# Restrict to the visible top-left header; never search the product body for
# any occurrence of the requested 5 digits.
_HEADER_CP_JS = r"""() => {
  const visible = (el) => {
    if (!el || !el.getClientRects().length) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  // Text nodes avoid expensive innerText reads over full category grids.
  // The screenshot shows one 5-digit leaf directly beside a yellow bag.
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const hits = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const value = String(node.nodeValue || '').replace(/\s+/g, ' ').trim();
    if (!/^\d{5}$/.test(value)) continue;
    const el = node.parentElement;
    if (!visible(el) || el.matches('input,textarea,select,option')) continue;
    if (el.closest('dialog,[role="dialog"],[aria-modal="true"],'
        + '.modal,.modal-dialog,[class*="modal" i],form')) continue;
    const rect = el.getBoundingClientRect();
    // Only the red top-left Soriana delivery area is eligible.
    if (rect.top < -2 || rect.top > 108 || rect.left < 0 ||
        rect.left > Math.min(430, innerWidth * .50) ||
        rect.width > 230 || rect.height > 100) continue;
    hits.push({value, selector:el.tagName.toLowerCase(),
               x:Math.round(rect.x),y:Math.round(rect.y)});
  }
  hits.sort((a,b) => a.y - b.y || a.x - b.x);
  return hits;
}"""

def header_postal_codes(page: Any) -> list[str]:
    """Return displayed CPs in the very top-left location control."""
    hits = page.evaluate(_HEADER_CP_JS)
    return list(dict.fromkeys(
        str(x["value"]) for x in hits if re.fullmatch(r"\d{5}", str(x.get("value", "")))
    ))


def visible_postal_evidence(page: Any, postal_code: str) -> str | None:
    cp = check_postal_code(postal_code)
    return f"header_delivery_cp:{cp}" if cp in header_postal_codes(page) else None


def _visible_search_and_input(page: Any):
    """Find the red Buscar control and the INPUT alongside it, not site search.

    The reference modal has a textbox containing '03100' and a red Buscar
    button within the same flex container. No placeholder is guaranteed.
    """
    searches = page.get_by_text(re.compile(r"^\s*Buscar\s*$", re.I))
    for index in range(min(searches.count(), 12)):
        button = searches.nth(index)
        try:
            if not button.is_visible():
                continue
            # Nearest ancestor containing an input, e.g. the postcode form row.
            row = button.locator("xpath=ancestor::*[.//input][1]")
            if row.count() == 0:
                continue
            inputs = row.locator("input:visible")
            for pos in range(min(inputs.count(), 5)):
                field = inputs.nth(pos)
                value = field.input_value(timeout=1500)
                placeholder = field.get_attribute("placeholder") or ""
                if (re.fullmatch(r"\d{5}", value.strip()) or
                    re.search(r"postal|c\.?\s*p\.?|c[oó]digo|^\d{5}$",
                              placeholder, re.I) or
                    field.get_attribute("inputmode") == "numeric"):
                    return button, field
            # Exactly one input beside Buscar is enough when the delivery
            # modal's postal instruction is currently visible.
            instruction = page.get_by_text(
                re.compile(r"Ingresa tu C\.?P\.? para verificar disponibilidad", re.I)
            )
            if inputs.count() == 1 and instruction.count() and instruction.first.is_visible():
                return button, inputs.first
        except Exception:
            continue
    return None, None


def _open_location_modal(page: Any) -> bool:
    """Click the visible header selector, either text or current 5-digit CP."""
    # Initial state: 'Elige cómo quieres recibir tu pedido'.
    phrases = (
        r"Elige c[oó]mo quieres\s+recibir tu pedido",
        r"Elige la mejor opci[oó]n de entrega para ti",
    )
    for pattern in phrases[:1]:
        choices = page.get_by_text(re.compile(pattern, re.I))
        for index in range(min(choices.count(), 6)):
            target = choices.nth(index)
            try:
                if target.is_visible():
                    target.click(timeout=4500)
                    page.wait_for_timeout(350)
                    return True
            except Exception:
                continue
    # Applied state: number in top-left strip beside the bag.
    for current in header_postal_codes(page):
        texts = page.get_by_text(current, exact=True)
        for index in range(min(texts.count(), 8)):
            target = texts.nth(index)
            try:
                if target.is_visible():
                    box = target.bounding_box()
                    if box and box["y"] <= 108 and box["x"] <= 430:
                        target.click(timeout=4500)
                        page.wait_for_timeout(350)
                        return True
            except Exception:
                continue
    return False


def postal_ui_diagnostics(page: Any, postal_code: str) -> dict:
    """Non-sensitive failure summary: no address or cookie/storage data."""
    cp = check_postal_code(postal_code)
    try:
        header = header_postal_codes(page)
    except Exception as exc:
        header = [f"error:{type(exc).__name__}"]
    try:
        instruction = page.get_by_text(
            re.compile(r"Ingresa tu C\.?P\.? para verificar disponibilidad", re.I)
        )
        modal_open = instruction.count() > 0 and instruction.first.is_visible()
    except Exception:
        modal_open = False
    try:
        button, field = _visible_search_and_input(page)
        search_visible = button is not None
        input_visible = field is not None
    except Exception:
        search_visible = input_visible = False
    return {
        "requested_cp": cp,
        "header_cp_candidates": header,
        "delivery_modal_visible": modal_open,
        "postal_input_found": input_visible,
        "buscar_found": search_visible,
        "url": page.url,
    }


def attempt_postal_selection(page: Any, postal_code: str) -> str | None:
    cp = check_postal_code(postal_code)
    proof = visible_postal_evidence(page, cp)
    if proof:
        return proof

    button, field = _visible_search_and_input(page)
    if button is None or field is None:
        _open_location_modal(page)
        button, field = _visible_search_and_input(page)
    if button is None or field is None:
        return None
    try:
        field.fill(cp, timeout=5000)
        # Confirm only the entry field changed: this is not store proof.
        if field.input_value(timeout=2000).strip() != cp:
            return None
        button.click(timeout=6000)
        # A successful search may update the header asynchronously.
        for _ in range(16):
            page.wait_for_timeout(450)
            proof = visible_postal_evidence(page, cp)
            if proof:
                return proof
    except Exception:
        return None
    return None


def require_postal_context(page: Any, postal_code: str) -> str:
    proof = visible_postal_evidence(page, postal_code)
    if not proof:
        seen = header_postal_codes(page)
        observed = ", ".join(seen) if seen else "sin CP visible"
        raise SorianaPostalContextError(
            f"Soriana no confirmó el CP {postal_code} junto a la bolsa "
            f"del encabezado. CP observado: {observed}."
        )
    return proof
