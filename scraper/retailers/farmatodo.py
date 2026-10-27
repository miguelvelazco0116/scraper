"""Farmatodo Mexico catalogue-only Playwright parser.

No postcode verification, no store inference, no hidden APIs. Products belong
to the public online catalogue only. Fail closed on coverage or identifiers.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlparse

from ..availability import AVAILABLE, UNAVAILABLE, UNKNOWN
from ..parsers import clean_text

HOST = "www.farmatodo.com.mx"
# Underscore is a word character, so \\b misses EANs in 750..._01.webp.
# Disallow adjoining letters/digits and partial matches inside longer IDs.
CODE_RE = re.compile(r"(?<![A-Za-z0-9])(\d{8,14})(?![A-Za-z0-9])")
MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
PDP_SKU_RE = re.compile(r"(?i)\bsku\s*[:：#-]\s*([A-Za-z0-9][A-Za-z0-9_-]{2,31})\b")
COUNT_RE = re.compile(r"\b([\d,]+)\s+productos?\b", re.I)
BLOCK_MARKERS = (
    "access denied", "captcha", "verify you are human",
    "acceso denegado", "too many requests",
)


class FarmatodoCaptureError(RuntimeError):
    pass


def prices_from_card(text: str) -> tuple[float | None, float | None]:
    """Handle Farmatodo 'De $145.00 Para $108.50' and single price."""
    raw = str(text or "")
    discount = re.search(
        r"\bDe\s*\$\s*([\d,]+(?:\.\d{1,2})?)\s*"
        r"(?:Por|Para)\s*\$\s*([\d,]+(?:\.\d{1,2})?)", raw, re.I
    )
    if discount:
        previous, current = (
            float(discount.group(1).replace(",", "")),
            float(discount.group(2).replace(",", "")),
        )
        if previous > 0 and current > 0 and previous >= current:
            return current, previous
    values = [
        float(token.replace(",", "")) for token in MONEY_RE.findall(raw)
    ]
    values = [value for value in values if value > 0]
    if not values:
        return None, None
    current = min(values[:2])
    regular = max(values[:2])
    return current, regular



def promotion_from_card(text: str, current: float | None,
                        regular: float | None) -> str | None:
    """Preserve visible 3+1 badges separately from monetary discounts.

    A 3+1 promotion is not itself a cheaper unit price. Screenshot badges
    do not establish eligibility, terms, or participating store locations.
    """
    raw = str(text or "")
    labels = []
    if re.search(r"(?<!\d)3\s*\+\s*1(?!\d)", raw):
        labels.append("3+1 (condiciones no verificadas)")
    if (current is not None and regular is not None
            and regular > current):
        labels.append("Precio promocional")
    return " | ".join(labels) if labels else None


def sku_from_card(card: dict) -> tuple[str | None, str]:
    """Never derive SKU from a display-name slug or invent one."""
    for field in ("sku", "data_sku", "data_pid", "product_id"):
        raw = str(card.get(field) or "").strip()
        if raw and re.fullmatch(r"[A-Za-z0-9_-]{5,32}", raw):
            return raw, field
    for value in (card.get("image_url"), card.get("image_alt")):
        if value:
            candidates = CODE_RE.findall(str(value))
            if candidates:
                return candidates[0], "image_ean_candidate"
    return None, "not_found"



def sku_from_visible_pdp_text(text: str) -> str | None:
    """Accept only an explicit, readable 'Sku: <identifier>' in a PDP.

    The image URL or the product-name slug is NOT certified evidence.
    """
    found = PDP_SKU_RE.search(str(text or ""))
    return found.group(1) if found else None


def inspect_public_product_page(page, url: str) -> dict:
    """Visit public PDP in the same Chrome context; extract visible SKU text.

    Does not access product APIs, cookies, hidden application state or cart.
    """
    parsed = urlparse(str(url))
    if (parsed.scheme != "https" or parsed.hostname != HOST
            or not parsed.path.rstrip("/").endswith("/p")):
        raise FarmatodoCaptureError("Ficha de producto fuera de Farmatodo")
    page.goto(url, wait_until="domcontentloaded", timeout=65000)
    page.wait_for_timeout(500)
    final_url = urlparse(page.url)
    if final_url.hostname != HOST or not final_url.path.rstrip("/").endswith("/p"):
        raise FarmatodoCaptureError("Redirección inválida de ficha de producto")
    sku = None
    for _ in range(5):
        body_text = page.locator("body").inner_text(timeout=12000)
        if any(marker in body_text.casefold() for marker in BLOCK_MARKERS):
            raise FarmatodoCaptureError("Bloqueo al consultar la ficha de producto")
        sku = sku_from_visible_pdp_text(body_text)
        if sku:
            break
        page.wait_for_timeout(500)
    if sku:
        return {"sku": sku, "source": "pdp_visible_sku", "verified": True}
    return {"sku": None, "source": "pdp_sku_not_visible", "verified": False}



def explicit_online_availability(text: str) -> tuple[str, bool | None, str | None]:
    """No postal/store availability is inferred; only on-card text."""
    raw = str(text or "")
    if re.search(r"\b(agotado|sin\s+existencias?|sin\s+stock)\b", raw, re.I):
        return UNAVAILABLE, False, "card_stockout_text"
    if re.search(r"\b\d[\d,]*\s+disponibles?\b", raw, re.I):
        return AVAILABLE, True, "card_quantity_display"
    return UNKNOWN, None, "catalogue_no_stock_signal"


def extract_catalogue(page) -> tuple[list[dict], int | None, bool]:
    """Discover visible product URL /p cards from main content and load-more."""
    data = page.evaluate(
        r"""() => {
          const host = 'www.farmatodo.com.mx';
          const root = document.querySelector('main') ||
                       document.querySelector('[role="main"]') || document.body;
          const nodes = [...root.querySelectorAll('a[href]')];
          const byUrl = new Map();
          for (const a of nodes) {
            if (!a.getClientRects().length) continue;
            const href = a.href.split(/[?#]/)[0].replace(/\/+$/, '');
            let url;
            try { url = new URL(href); } catch (_) { continue; }
            if (url.hostname !== host || !url.pathname.endsWith('/p')) continue;
            let node = a, card = null;
            for (let i=0; i<9 && node; i++) {
              const links = [...new Set(
                [...node.querySelectorAll('a[href]')]
                  .map(link => link.href.split(/[?#]/)[0].replace(/\/+$/, ''))
                  .filter(link => link.endsWith('/p'))
              )];
              if (links.length > 1) break;
              const text = (node.innerText || '').replace(/\s+/g,' ').trim();
              if (/\$\s*[\d,]+/.test(text) && text.length < 1300 && text.length > 7)
                card = node;
              node = node.parentElement;
            }
            if (!card) continue;
            const text = (card.innerText || '').replace(/\s+/g,' ').trim();
            const img = card.querySelector('img[src]');
            const titleNode = card.querySelector(
              'h3,h4,[class*="product-name" i],[class*="product-title" i],'
              + '[data-testid*="product-name" i]'
            );
            const title = (
              titleNode?.innerText || a.innerText || a.getAttribute('title')
              || img?.getAttribute('alt') || ''
            ).replace(/\s+/g,' ').trim();
            const skuNode = card.querySelector(
              '[data-sku],[data-pid],[data-product-id]'
            );
            const data = {
              url: href,
              product: title,
              text,
              sku: card.getAttribute('data-sku') || skuNode?.getAttribute('data-sku'),
              data_pid: card.getAttribute('data-pid') || skuNode?.getAttribute('data-pid'),
              product_id: card.getAttribute('data-product-id')
                          || skuNode?.getAttribute('data-product-id'),
              image_url: img?.src || null,
              image_alt: img?.getAttribute('alt') || null
            };
            if (!byUrl.has(href) || text.length < byUrl.get(href).text.length)
              byUrl.set(href, data);
          }
          const text = (root.innerText || '').slice(0,8000);
          const count = text.match(/\b([\d,]+)\s+productos?\b/i);
          const all = [...document.querySelectorAll('button,[role="button"]')];
          const more = all.some(button =>
            button.getClientRects().length &&
            !button.disabled &&
            /^mostrar\s+m[aá]s$/i.test((button.innerText || '').trim())
          );
          return {
            cards: [...byUrl.values()],
            target: count ? Number(count[1].replace(/,/g,'')) : null,
            has_more: more
          };
        }"""
    )
    return data["cards"], data["target"], data["has_more"]


def validate_category_page(page, category) -> None:
    url = urlparse(page.url)
    if url.hostname != HOST or url.scheme != "https":
        raise FarmatodoCaptureError("Navegación fuera del dominio Farmatodo")
    raw = page.locator("body").inner_text(timeout=10000)
    if any(marker in raw.casefold() for marker in BLOCK_MARKERS):
        raise FarmatodoCaptureError("Bloqueo del catálogo detectado")
    if "no se encontr" in raw.casefold() and "producto" in raw.casefold():
        raise FarmatodoCaptureError("Categoría sin productos")


class FarmatodoScraper:
    def __init__(self, *, max_rounds: int = 100, wait_ms: int = 950):
        self.max_rounds = max_rounds
        self.wait_ms = wait_ms

    def capture_category(self, page, category, *, probe: bool = False) -> tuple[list[dict], dict]:
        page.goto(category.url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(1000)
        validate_category_page(page, category)
        cards, target, more = extract_catalogue(page)
        if not cards:
            raise FarmatodoCaptureError(
                "No hay tarjetas con URL /p y precio: revisar DOM."
            )
        seen = {card["url"]: card for card in cards}
        initial_count = len(seen)
        rounds = 0
        stalled = False
        # A probe must also click Mostrar más ONCE. Otherwise the test may
        # pass its SKU samples yet fail on the first full-capture click.
        while more and rounds < (1 if probe else self.max_rounds):
                button = page.get_by_text(
                    re.compile(r"^\s*Mostrar\s+m[aá]s\s*$", re.I)
                )
                if not button.count() or not button.last.is_visible():
                    stalled = True
                    break
                before = len(seen)
                old_urls = sorted(seen)
                button.last.scroll_into_view_if_needed(timeout=8000)
                button.last.click(timeout=12000)
                # Wait for a NEW visible product link, not a fixed delay.
                # Products can arrive asynchronously in batches of six.
                page.wait_for_function(
                    "old => Array.from(document.querySelectorAll('main a[href]'))"
                    ".some(a => a.getClientRects().length"
                    " && a.href.split(/[?#]/)[0].endsWith('/p')"
                    " && !old.includes(a.href.split(/[?#]/)[0]))",
                    arg=old_urls, timeout=25000, polling=250,
                )
                page.wait_for_timeout(min(self.wait_ms, 500))
                validate_category_page(page, category)
                additional, next_target, more = extract_catalogue(page)
                if next_target and target and next_target != target:
                    raise FarmatodoCaptureError(
                        f"El total de productos cambió ({target} -> {next_target})"
                    )
                target = target or next_target
                seen.update({item["url"]: item for item in additional})
                rounds += 1
                if len(seen) <= before:
                    stalled = True
                    break

        # SKU is certified only by the visible 'Sku:' field on the
        # product page. In probe, inspect three representative PDPs. In
        # capture, inspect all PDPs only if loading has reached the target;
        # otherwise keep the result PARTIAL without hundreds of extra visits.
        can_full_check = (
            not probe and not stalled and not more
            and isinstance(target, int) and target > 0
            and 0.90 <= len(seen) / target <= 1.0
        )
        pdp_limit = len(seen) if can_full_check else min(3, len(seen))
        pdp_checks: dict[str, dict] = {}
        if pdp_limit:
            detail_tab = page.context.new_page()
            try:
                for idx, card in enumerate(seen.values()):
                    if idx >= pdp_limit:
                        break
                    try:
                        found = inspect_public_product_page(detail_tab, card["url"])
                    except Exception as exc:
                        found = {
                            "sku": None,
                            "source": "pdp_error_" + type(exc).__name__,
                            "verified": False,
                        }
                    candidate, candidate_source = sku_from_card(card)
                    found["image_or_card_candidate"] = candidate
                    found["candidate_source"] = candidate_source
                    found["candidate_matches_pdp"] = (
                        candidate == found["sku"]
                        if candidate and found["sku"] else None
                    )
                    pdp_checks[card["url"]] = found
                    if not found["verified"]:
                        print(
                            f"Farmatodo | SKU no visible en PDP {idx + 1}/"
                            f"{pdp_limit}: {card['url']}",
                            flush=True,
                        )
            finally:
                detail_tab.close()

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        rows: list[dict] = []
        for card in seen.values():
            current, regular = prices_from_card(card["text"])
            certification = pdp_checks.get(card["url"], {})
            sku = certification.get("sku")
            source = certification.get("source", "pdp_not_inspected")
            verified = certification.get("verified") is True
            status, available, raw = explicit_online_availability(card["text"])
            rows.append({
                "scrape_timestamp": now, "retailer": "Farmatodo",
                "city": "Catálogo online", "state": "Nacional",
                "postal_code": None, "store": None, "store_id": None,
                "department": category.department,
                "category": category.name,
                "subcategory": category.subcategory,
                "sub_subcategory": category.sub_subcategory,
                "category_id": category.id, "sku": sku,
                "sku_source": source, "sku_verified": verified,
                "brand": None, "product": clean_text(card["product"]),
                "price_current": current, "price_regular": regular,
                "promotion": promotion_from_card(
                    card["text"], current, regular
                ),
                "availability_status": status, "is_available": available,
                "availability_raw": raw,
                "store_context_verified": False,
                "store_context_method": "PUBLIC_ONLINE_CATALOGUE_NO_POSTAL_VERIFICATION",
                "url": card["url"], "price_raw": card["text"][:300],
            })
        meta = {
            "category_id": category.id, "capture_date": now[:10],
            "product_links": len(seen), "products": len(rows),
            "target_products": target, "load_more_clicks": rounds,
            "load_more_stalled": stalled,
            "load_more_remaining": more,
            "probe_load_more_verified": (
                (target is not None and initial_count >= target)
                or (rounds > 0 and len(seen) > initial_count and not stalled)
            ),
            "postal_verified": False,
            "inventory_scope": "GENERIC_ONLINE_CATALOGUE",
            "pdp_sku_checked": len(pdp_checks),
            "pdp_sku_verified": sum(
                x.get("verified") is True for x in pdp_checks.values()
            ),
            "sku_source_counts": {
                key: sum(row["sku_source"] == key for row in rows)
                for key in {row["sku_source"] for row in rows}
            },
            # Public image/link evidence only, to diagnose missing identifiers.
            # A card without a provable SKU cannot pass production quality.
            "sku_debug_samples": [
                {
                    "url": x.get("url"),
                    "image_url": x.get("image_url"),
                    "image_alt": x.get("image_alt"),
                    "sku": pdp_checks.get(x["url"], {}).get("sku"),
                    "sku_source": pdp_checks.get(x["url"], {}).get(
                        "source", "pdp_not_inspected"
                    ),
                    "candidate": sku_from_card(x)[0],
                    "candidate_source": sku_from_card(x)[1],
                }
                for x in list(seen.values())
                if not pdp_checks.get(x["url"], {}).get("verified", False)
            ][:3],
            "pdp_sku_samples": [
                {"url": url, **result}
                for url, result in list(pdp_checks.items())[:3]
            ],
            "probe": probe,
        }
        return rows, meta
