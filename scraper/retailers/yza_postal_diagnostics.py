"""YZA diagnostic-only DOM inventory for postal controls.

Never log typed address text, input values, cookies or local storage.
The diagnostic is not proof of a selected delivery postcode.
"""
from __future__ import annotations


def inspect_postal_ui(page) -> dict:
    """Describe visible/address UI safely; never claim CP verification."""
    return page.evaluate(
        r"""() => {
          const visible = (el) => !!el.getClientRects().length
            && getComputedStyle(el).visibility !== 'hidden'
            && getComputedStyle(el).display !== 'none';
          const normalize = (value) => String(value || '')
            .replace(/\s+/g, ' ').trim();
          const inputs = [...document.querySelectorAll('input')]
            .filter(el => {
              const p = normalize(el.placeholder || '');
              const label = normalize(el.getAttribute('aria-label') || '');
              const parent = normalize(el.closest('[role="dialog"],dialog,form')
                ?.getAttribute('class') || '');
              return /direcci[oó]n|postal|c[oó]digo|ubicaci[oó]n|autocomplete|sugerencias|address|zip/i
                .test(p + ' ' + label + ' ' + el.name + ' ' + parent);
            }).sort((a, b) => Number(visible(b)) - Number(visible(a)))
              .slice(0, 24).map(el => ({
              placeholder: normalize(el.placeholder).slice(0, 130),
              aria_label: normalize(el.getAttribute('aria-label')).slice(0, 100),
              visible: visible(el),
              in_dialog: !!el.closest('[role="dialog"],dialog,.modal-popup'),
              type: el.type || null
            }));
          const labels = [...document.querySelectorAll(
            'button,[role="button"],h2,h3,h4,[role="dialog"] [class*="title" i]'
          )].map(el => ({
            text: normalize(el.innerText).slice(0, 95), visible: visible(el),
            in_dialog: !!el.closest('[role="dialog"],dialog,.modal-popup')
          })).filter(x => /consultar disponibilidad|direcci[oó]n|env[ií]o a domicilio|ubicaci[oó]n|c[oó]digo postal|^aceptar$|^cancelar$/i
            .test(x.text)).slice(0, 20);
          const modal_count = document.querySelectorAll(
            '[role="dialog"],dialog,.modal-popup').length;
          return {input_fields: inputs, location_controls: labels,
            postal_fields_visible: inputs.filter(x =>
              x.visible && /c[oó]digo\s*postal/i.test(x.placeholder)
            ).length,
            accept_buttons_visible: labels.filter(x =>
              x.visible && /^aceptar$/i.test(x.text)
            ).length,
            modal_count, url: location.origin + location.pathname};
        }"""
    )
