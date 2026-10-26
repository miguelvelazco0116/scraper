"""Fail-closed Soriana shipping postal-code selection and verification.

A typed postcode, matching page URL, input value, or search result alone does
not prove that Soriana applied a delivery region. Verify a visible, persistent
selected-location element outside the modal/input before collecting records.
"""
from __future__ import annotations

import re
from typing import Any


class SorianaPostalContextError(RuntimeError):
    """Soriana did not visibly certify the requested delivery postal code."""


def check_postal_code(cp: str) -> str:
    normalized = str(cp).strip()
    if not re.fullmatch(r"\d{5}", normalized):
        raise ValueError("El CP debe contener exactamente 5 dígitos")
    return normalized


def visible_postal_evidence(page: Any, postal_code: str) -> str | None:
    """Return evidence for applied postcode; never use form/modal values."""
    postal_code = check_postal_code(postal_code)
    return page.evaluate(
        r"""(cp) => {
          const selectors = [
            'header [data-testid*="location" i]',
            'header [data-testid*="delivery" i]',
            'header [data-testid*="postal" i]',
            'header [class*="location" i]',
            'header [class*="delivery" i]',
            'header [class*="address" i]',
            'header [class*="zip" i]',
            'header [class*="postal" i]',
            '[data-testid*="selected-location" i]',
            '[data-testid*="selected-address" i]',
            '[class*="selected-location" i]',
            '[class*="selected-address" i]',
            '[class*="current-location" i]',
            '[class*="current-address" i]'
          ];
          const exact = new RegExp('(^|[^0-9])' + cp + '([^0-9]|$)');
          for (const selector of selectors) {
            for (const el of document.querySelectorAll(selector)) {
              if (!el.getClientRects().length ||
                  el.closest('dialog,[role="dialog"],.modal,.modal-dialog,[class*="modal" i],form') ||
                  el.matches('input,textarea,select')) continue;
              const text = (el.innerText || el.textContent || '')
                .replace(/\s+/g,' ').trim();
              if (text.length && text.length <= 180 && exact.test(text))
                return selector + ' :: ' + text;
            }
          }
          return null;
        }""",
        postal_code,
    )


def attempt_postal_selection(page: Any, postal_code: str) -> str | None:
    """Use ordinary Soriana UI; do not change cookies/requests directly.

    Select "Envío a domicilio", fill Código Postal, click Buscar and verify
    the persistent selected-location display. Differences in the site UI fail
    closed; the user can complete selection manually in Chrome.
    """
    cp = check_postal_code(postal_code)
    proof = visible_postal_evidence(page, cp)
    if proof:
        return proof

    for selector in (
        'header button[class*="location" i]',
        'header button[class*="delivery" i]',
        'header button[class*="postal" i]',
        '[data-testid*="change-location" i]',
        '[data-testid*="delivery-method" i]',
    ):
        try:
            button = page.locator(selector).first
            if button.count() and button.is_visible():
                button.click(timeout=3000)
                break
        except Exception:
            continue

    # The modal might already be open.
    try:
        delivery = page.get_by_text("Envío a domicilio", exact=True)
        if delivery.count() and delivery.first.is_visible():
            delivery.first.click(timeout=3000)
    except Exception:
        pass

    field = None
    for selector in (
        'input[placeholder*="Código Postal" i]',
        'input[placeholder*="Codigo Postal" i]',
        'input[placeholder*="C.P." i]',
        'input[aria-label*="postal" i]',
        'input[name*="postal" i]',
        'input[name*="zip" i]',
    ):
        candidate = page.locator(selector).first
        try:
            if candidate.count() and candidate.is_visible():
                field = candidate
                break
        except Exception:
            continue
    if field is None:
        return None

    try:
        field.fill(cp, timeout=4000)
        # The visible 'Buscar' control is part of Soriana's delivery modal.
        modal = page.locator(
            '[role="dialog"], dialog, .modal.show, [class*="delivery-modal" i]'
        )
        scope = modal.last if modal.count() and modal.last.is_visible() else page
        search = scope.get_by_role(
            "button", name=re.compile(r"^\s*buscar\s*$", re.I)
        )
        if search.count() and search.first.is_visible():
            search.first.click(timeout=5000)
        else:
            # Do not press Enter against an unverified field (search may
            # navigate away without applying a delivery method).
            return None
        page.wait_for_timeout(1600)
    except Exception:
        return None

    return visible_postal_evidence(page, cp)


def require_postal_context(page: Any, postal_code: str) -> str:
    """Raise if Soriana does not visibly display this CP as applied."""
    proof = visible_postal_evidence(page, postal_code)
    if not proof:
        raise SorianaPostalContextError(
            f"Soriana no confirma el CP {postal_code} en la ubicación "
            "persistente fuera del formulario. Captura rechazada."
        )
    return proof
