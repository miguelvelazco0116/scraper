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
    """Certify an SKU from public, rendered PDP text, never the URL slug.

    VTEX PDP widgets hydrate asynchronously; a missing label immediately
    after DOMContentLoaded is NOT evidence that the product has no SKU.
    Wait for rendered text, then reload at most once before failing closed.
    """
    parsed = urlparse(str(url))
    if (parsed.scheme != "https" or parsed.hostname != HOST
            or not parsed.path.rstrip("/").endswith("/p")):
        raise FarmatodoCaptureError("Ficha de producto fuera de Farmatodo")

    attempts = 0
    reload_count = 0
    last_body = ""
    last_url = url
    for load_round in range(2):
        if load_round == 0:
            page.goto(url, wait_until="domcontentloaded", timeout=65000)
        else:
            page.reload(wait_until="domcontentloaded", timeout=65000)
            reload_count += 1
        final_url = urlparse(page.url)
        if (final_url.scheme != "https" or final_url.hostname != HOST
                or not final_url.path.rstrip("/").endswith("/p")):
            raise FarmatodoCaptureError(
                "Redirección inválida de ficha de producto"
            )
        last_url = page.url

        for _ in range(10):
            attempts += 1
            last_body = page.locator("body").inner_text(timeout=12000)
            if any(
                marker in last_body.casefold() for marker in BLOCK_MARKERS
            ):
                raise FarmatodoCaptureError(
                    "Bloqueo al consultar la ficha de producto"
                )
            sku = sku_from_visible_pdp_text(last_body)
            if sku:
                return {
                    "sku": sku, "source": "pdp_visible_sku",
                    "verified": True,
                    "pdp_load_checks": attempts,
                    "pdp_reload_count": reload_count,
                }
            page.wait_for_timeout(600)

    return {
        "sku": None,
        "source": "pdp_sku_not_visible",
        "verified": False,
        "pdp_load_checks": attempts,
        "pdp_reload_count": reload_count,
        "pdp_body_characters": len(last_body),
        "pdp_sku_label_present": bool(
            re.search(r"(?i)\bsku\s*[:：#-]", last_body)
        ),
        "pdp_url": last_url,
    }



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
            const namedLink = [...card.querySelectorAll('a[href]')]
              .find(link => link.href.split(/[?#]/)[0].replace(/\/+$/, '') === href
                && (link.innerText || '').trim().length > 8);
            const title = (
              titleNode?.innerText || namedLink?.innerText || a.innerText
              || a.getAttribute('title') || img?.getAttribute('alt') || ''
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
              image_alt: img?.getAttribute('alt') || null,
              promo_badge: [...card.querySelectorAll(
                '[aria-label],[title],img[alt]'
              )].map(el => (
                el.getAttribute('aria-label') || el.getAttribute('title')
                || el.getAttribute('alt') || ''
              )).find(value => /3\s*\+\s*1/.test(value)) || null
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


def inspect_public_pagination_ui(page, category_url: str) -> dict:
    """Public DOM-only diagnosis of pagination and subcategory navigation.

    Records no cookies, tokens, private requests, HTML blobs or embedded
    hydration data. Linked URLs must stay on the original catalogue route.
    """
    try:
        data = page.evaluate(
            r"""(categoryUrl) => {
              const base = new URL(categoryUrl);
              const prefix = base.pathname.replace(/\/+$/, '') + '/';
              const textOf = el => (
                el.innerText || el.getAttribute('aria-label') || ''
              ).replace(/\s+/g, ' ').trim().slice(0, 90);
              const links = [...document.querySelectorAll('a[href]')];
              const children = [];
              const navigation = [];
              for (const a of links) {
                if (!a.getClientRects().length) continue;
                let url;
                try { url = new URL(a.href); } catch (_) { continue; }
                if (url.hostname !== base.hostname ||
                    url.protocol !== 'https:') continue;
                if (url.pathname.startsWith(prefix) &&
                    !url.pathname.replace(/\/+$/, '').endsWith('/p')) {
                  children.push({
                    label: textOf(a), url: url.origin + url.pathname
                  });
                }
                if (url.searchParams.has('page') ||
                    url.searchParams.has('from') ||
                    url.searchParams.has('_from')) {
                  navigation.push({
                    label: textOf(a),
                    url: url.origin + url.pathname + url.search
                  });
                }
              }
              const buttons = [...document.querySelectorAll(
                'button,[role="button"]'
              )].map(b => ({
                text: textOf(b),
                visible: Boolean(b.getClientRects().length),
                disabled: Boolean(b.disabled || b.getAttribute(
                  'aria-disabled') === 'true')
              })).filter(b => b.text &&
                /mostrar|cargar|siguiente|anterior|ver m[aá]s/i.test(b.text)
              );
              const filters = [...document.querySelectorAll('label')].
                map(l => ({
                  label: textOf(l), visible: Boolean(l.getClientRects().length),
                  input_type: l.querySelector('input')?.type || null
                })).filter(l => l.label).slice(0, 40);
              return {
                url: location.origin + location.pathname + location.search,
                title: document.title.slice(0, 90),
                visible_product_links: links.filter(a =>
                  a.getClientRects().length &&
                  a.href.split(/[?#]/)[0].replace(/\/+$/, '')
                    .endsWith('/p')).length,
                visible_buttons: buttons.slice(0, 20),
                public_subcategory_links: children.filter(
                  (x,i) => children.findIndex(y => y.url === x.url) === i
                ).slice(0, 40),
                visible_pagination_links: navigation.filter(
                  (x,i) => navigation.findIndex(y => y.url === x.url) === i
                ).slice(0, 20),
                filter_labels: filters
              };
            }""",
            category_url,
        )
    except Exception as exc:
        return {"diagnostic_unavailable": type(exc).__name__}
    return data if isinstance(data, dict) else {
        "diagnostic_unavailable": "unexpected_result_type"
    }


class FarmatodoScraper:
    def __init__(self, *, max_rounds: int = 100, wait_ms: int = 950):
        self.max_rounds = max_rounds
        self.wait_ms = wait_ms

    def capture_category(self, page, category, *, probe: bool = False) -> tuple[list[dict], dict]:
        # Hydration of the VTEX catalogue is variable, especially in
        # Respiratorios. DOMContentLoaded is not catalogue readiness.
        # Wait for an ACTUAL priced card from our extractor; if the SPA
        # remains empty, reload just once, then fail closed with evidence.
        cards: list[dict] = []
        target: int | None = None
        more = False
        load_attempts = 0
        initial_reload_count = 0
        for reload_index in range(2):
            page.goto(category.url, wait_until="domcontentloaded", timeout=90000)
            validate_category_page(page, category)
            for _ in range(20):
                load_attempts += 1
                cards, target, more = extract_catalogue(page)
                if cards:
                    break
                page.wait_for_timeout(750)
                validate_category_page(page, category)
            if cards:
                break
            if reload_index == 0:
                initial_reload_count += 1
                print(
                    f"Farmatodo {category.id} | aún sin tarjetas después "
                    "de esperar la carga inicial; recargando categoría",
                    flush=True,
                )
        if not cards:
            signals = page.evaluate(
                """() => ({
                  ready_state: document.readyState,
                  visible_product_links: [...document.querySelectorAll('a[href]')]
                    .filter(a => a.getClientRects().length &&
                      a.href.split(/[?#]/)[0].endsWith('/p')).length,
                  visible_prices: [...document.querySelectorAll('main,body')]
                    .some(el => /\\$\\s*[0-9]/.test(el.innerText || '')),
                  title: document.title.slice(0, 100)
                })"""
            )
            raise FarmatodoCaptureError(
                f"Catálogo {category.id} sin tarjetas con precio ni URL /p "
                f"tras {load_attempts} verificaciones y "
                f"{initial_reload_count} recarga; DOM={signals}"
            )
        seen = {card["url"]: card for card in cards}
        initial_count = len(seen)
        rounds = 0
        stalled = False
        load_more_error = None
        # The storefront briefly hides/disables Mostrar más while updating
        # its catalogue. 'has_more=False' at that instant is NOT a verified
        # terminal page. When the site publishes a target, keep trying until
        # that target is reached or a concrete stall is diagnosed.
        click_budget = 1 if probe else self.max_rounds
        stop_reason = "not_started"
        while rounds < click_budget:
            pending = (
                len(seen) < target
                if isinstance(target, int) and target > 0
                else more
            )
            if not pending:
                stop_reason = (
                    "published_target_reached"
                    if isinstance(target, int) and target > 0
                    else "no_button_and_no_published_target"
                )
                break

            button = None
            # Poll the real visible/enabled button, NOT the instantaneous
            # boolean from extract_catalogue. Some VTEX re-renders remove
            # the button temporarily after each click.
            for retry in range(16):
                controls = page.get_by_text(
                    re.compile(r"^\s*Mostrar\s+m[aá]s\s*$", re.I)
                )
                if controls.count():
                    candidate = controls.last
                    enabled = (
                        candidate.is_enabled()
                        if hasattr(candidate, "is_enabled") else True
                    )
                    if candidate.is_visible() and enabled:
                        button = candidate
                        break
                page.wait_for_timeout(750)
                validate_category_page(page, category)
                extra, updated_target, more = extract_catalogue(page)
                if updated_target and target and updated_target != target:
                    raise FarmatodoCaptureError(
                        f"El total de productos cambió ({target} -> {updated_target})"
                    )
                target = target or updated_target
                seen.update({item["url"]: item for item in extra})
                if isinstance(target, int) and len(seen) >= target:
                    break

            if button is None:
                if isinstance(target, int) and len(seen) >= target:
                    stop_reason = "published_target_reached"
                    break
                stalled = True
                stop_reason = "button_not_visible_or_enabled_before_target"
                load_more_error = stop_reason
                print(
                    f"Farmatodo {category.id} | sin botón Mostrar más "
                    f"habilitado tras 16 comprobaciones | "
                    f"tarjetas={len(seen)}/{target or '?'}",
                    flush=True,
                )
                break

            before = len(seen)
            old_urls = sorted(seen)
            try:
                button.scroll_into_view_if_needed(timeout=8000)
                button.click(timeout=12000)
                # The DOM may contain new links before the corresponding
                # cards have their displayed prices. Use the SAME extractor
                # and wait for a priced, previously unseen product.
                for _ in range(25):
                    page.wait_for_timeout(650)
                    validate_category_page(page, category)
                    additional, next_target, more = extract_catalogue(page)
                    if next_target and target and next_target != target:
                        raise FarmatodoCaptureError(
                            f"El total de productos cambió ({target} -> {next_target})"
                        )
                    target = target or next_target
                    seen.update({item["url"]: item for item in additional})
                    if len(seen) > before:
                        break
                else:
                    stalled = True
                    stop_reason = "click_added_no_priced_products"
                    load_more_error = stop_reason
                    print(
                        f"Farmatodo {category.id} | Mostrar más no añadió "
                        f"tarjetas con precio | {len(seen)}/{target or '?'}",
                        flush=True,
                    )
                    break
            except FarmatodoCaptureError:
                raise
            except Exception as exc:
                stalled = True
                stop_reason = "load_more_click_error"
                load_more_error = type(exc).__name__
                print(
                    f"Farmatodo {category.id} | error en Mostrar más "
                    f"({load_more_error}) | {len(seen)}/{target or '?'}",
                    flush=True,
                )
                break

            rounds += 1
            print(
                f"Farmatodo {category.id} | Mostrar más {rounds} | "
                f"tarjetas={len(seen)}/{target or '?'} | "
                f"nuevos={len(seen)-before} | boton_activo={more}",
                flush=True,
            )

        if stop_reason == "not_started":
            stop_reason = (
                "published_target_reached"
                if isinstance(target, int) and len(seen) >= target
                else (
                    "probe_one_click_verified"
                    if probe and rounds == 1 and not stalled
                    else "max_rounds_reached"
                )
            )
        if (not probe and isinstance(target, int)
                and target > 0 and len(seen) < target):
            # Never silently certify 40/169 as the end of the catalogue.
            # If the URL/price extractor later reports <target, the
            # publication remains blocked regardless of button state.
            stalled = True
            if stop_reason in {"not_started", "max_rounds_reached"}:
                load_more_error = "target_not_reached"

        # A stable stop around 100 despite a much larger public target
        # needs DOM evidence of alternative PUBLIC catalogue navigation
        # before attempting any fallback or inventing pagination URLs.
        load_more_ui_debug = (
            inspect_public_pagination_ui(page, category.url)
            if stalled and not probe and isinstance(target, int)
            and len(seen) < target else None
        )
        if load_more_ui_debug is not None:
            print(
                f"Farmatodo {category.id} | diagnóstico navegación: "
                f"rutas_subcategoria="
                f"{len(load_more_ui_debug.get('public_subcategory_links', []))} | "
                f"links_paginación="
                f"{len(load_more_ui_debug.get('visible_pagination_links', []))} | "
                f"botones="
                f"{len(load_more_ui_debug.get('visible_buttons', []))}",
                flush=True,
            )

        # SKU is certified only by the visible 'Sku:' field on the
        # product page. In probe, inspect three representative PDPs. In
        # capture, inspect all PDPs only if loading has reached the target;
        # otherwise keep the result PARTIAL without hundreds of extra visits.
        can_full_check = (
            not probe and not stalled
            and stop_reason == "published_target_reached"
            and isinstance(target, int) and target > 0
            and len(seen) == target
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
                    (card["text"] + " " + str(card.get("promo_badge") or "")),
                    current, regular
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
            "initial_products": initial_count, "loaded_products": len(seen),
            "initial_load_checks": load_attempts,
            "initial_reload_count": initial_reload_count,
            "target_products": target, "load_more_clicks": rounds,
            "load_more_stalled": stalled,
            "load_more_error": load_more_error,
            "load_more_stop_reason": stop_reason,
            "load_more_ui_debug": load_more_ui_debug,
            # Remaining work is based on the PUBLISHED product total,
            # not a temporarily rendered/disabled load-more control.
            "load_more_remaining": (
                len(seen) < target
                if isinstance(target, int) and target > 0
                else more
            ),
            "load_more_button_visible_at_stop": more,
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
