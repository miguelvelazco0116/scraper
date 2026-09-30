from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from ..config import Category, Location
from ..parsers import clean_text


BASE_URL = "https://www.farmaciasguadalajara.com/"
DIAGNOSTICS = Path("diagnostics")


class FarmaciasGuadalajaraBlocked(RuntimeError):
    pass


class FarmaciasGuadalajaraNetworkUnavailable(RuntimeError):
    pass


class FarmaciasGuadalajaraScraper:
    """Scraper del catálogo online público de Farmacias Guadalajara.

    No atribuye precios o disponibilidad a una sucursal concreta cuando no se
    configuró una tienda. Farmacias Guadalajara indica que el precio online y
    la disponibilidad pueden variar por ubicación.
    """

    PRODUCT_RE = re.compile(r"-(\d{5,14})\.html(?:$|[?#])", re.IGNORECASE)
    MONEY_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)")

    def __init__(self, headless: bool = True, max_load_more: int = 100) -> None:
        self.headless = headless
        self.max_load_more = max_load_more

    @classmethod
    def extract_sku(cls, url: str | None) -> str | None:
        if not url:
            return None
        match = cls.PRODUCT_RE.search(url)
        return match.group(1) if match else None

    @staticmethod
    def _infer_brand(name: str | None, text: str | None = None) -> str | None:
        blob = clean_text(text) or ""
        lines = [clean_text(x) for x in (text or "").splitlines()]
        lines = [x for x in lines if x]
        if name:
            try:
                idx = next(i for i, value in enumerate(lines) if value == name)
            except StopIteration:
                idx = -1
            if idx > 0:
                candidate = lines[idx - 1]
                if (
                    candidate
                    and len(candidate) <= 40
                    and candidate.upper() == candidate
                    and any(ch.isalpha() for ch in candidate)
                ):
                    return candidate.title() if len(candidate) > 4 else candidate

        brands = [
            "Colgate", "Oral-B", "Sensodyne", "Listerine", "Gum", "Curaprox",
            "Prudence", "Sico", "Trojan", "Durex", "Playboy",
            "Sterimar", "Neilmed", "Pharmalife", "Lysomucil", "Tabcin",
            "Ariel", "Ace", "Downy", "Suavitel", "Ensueño", "Fuerza Max",
            "Vanish", "Cloralex", "Persil", "Roma", "Foca",
        ]
        for brand in brands:
            if re.search(rf"(?<!\w){re.escape(brand)}(?!\w)", blob, re.IGNORECASE):
                return brand
        return None

    @classmethod
    def _prices_from_text(
        cls,
        text: str | None,
    ) -> tuple[float | None, float | None, str | None]:
        if not text:
            return None, None, None

        values: list[float] = []
        for raw in cls.MONEY_RE.findall(text):
            try:
                value = float(raw.replace(",", ""))
            except ValueError:
                continue
            if value > 0:
                values.append(value)
        if not values:
            return None, None, None

        current = values[-1]
        regular = values[0] if len(values) > 1 else current
        if current > regular:
            current, regular = regular, current

        promo_parts: list[str] = []
        for pattern in (
            r"\b\d+\s*x\s*\d+\b",
            r"\b\d+\s*x\s*\$\s*\d+(?:\.\d+)?",
        ):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                promo_parts.append(match.group(0))
        if current < regular:
            promo_parts.append("Precio promocional")

        promotion = " | ".join(dict.fromkeys(promo_parts)) if promo_parts else None
        return current, regular, promotion

    @staticmethod
    def _assert_not_blocked(page) -> None:
        text = (page.locator("body").inner_text(timeout=20_000) or "").casefold()
        markers = [
            "access denied",
            "verifica que eres humano",
            "verify you are human",
            "captcha",
            "request rejected",
        ]
        if any(marker in text for marker in markers):
            raise FarmaciasGuadalajaraBlocked(
                "Farmacias Guadalajara presentó un bloqueo o verificación"
            )

    @staticmethod
    def _target_count(page) -> int | None:
        try:
            text = page.locator("body").inner_text(timeout=10_000)
        except Exception:
            return None
        matches = re.findall(
            r"\(?\b(\d{1,5})\s+productos?\b\)?",
            text,
            flags=re.IGNORECASE,
        )
        if not matches:
            return None
        values = [int(x) for x in matches]
        return max(values) if values else None

    @staticmethod
    def _product_link_count(page) -> int:
        try:
            return int(
                page.locator('a[href*=".html"]').evaluate_all(
                    "els => new Set(els.map(a => a.href).filter(h => /-\\d{5,14}\\.html(?:$|[?#])/.test(h))).size"
                )
            )
        except Exception:
            return 0

    @staticmethod
    def _goto_with_retries(page, url: str):
        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                response = page.goto(url, wait_until="commit", timeout=20_000)
                page.wait_for_selector("body", state="attached", timeout=10_000)
                return response
            except PlaywrightError as exc:
                last_error = exc
                if attempt >= 2:
                    break
                try:
                    page.goto("about:blank", wait_until="commit", timeout=5_000)
                except Exception:
                    pass
                page.wait_for_timeout(1_000)

        detail = f"{type(last_error).__name__}: {last_error}" if last_error else "sin respuesta"
        raise FarmaciasGuadalajaraNetworkUnavailable(
            "No se pudo establecer conexión con Farmacias Guadalajara desde esta red. "
            f"Detalle: {detail}"
        )

    def _expand_all_products(self, page, target: int | None) -> dict:
        previous = self._product_link_count(page)
        page_requests: list[dict] = []

        stats = {
            "target_products": target,
            "initial_links": previous,
            "clicks": 0,
            "direct_loads": 0,
            "final_links": previous,
            "stop_reason": None,
            "button": None,
            "page_requests": page_requests,
        }

        for _ in range(self.max_load_more):
            if target and previous >= target:
                stats["stop_reason"] = "target_reached"
                break

            # Farmacias Guadalajara exposes the next catalog block in data-url.
            # Prefer that stable contract over matching the rendered button text,
            # which can arrive with mojibake in some Windows/browser contexts.
            button = None
            data_buttons = page.locator('button.more[data-url]')
            for idx in range(data_buttons.count() - 1, -1, -1):
                candidate = data_buttons.nth(idx)
                try:
                    if candidate.is_visible(timeout=700):
                        button = candidate
                        break
                except Exception:
                    continue

            if button is None:
                stats["stop_reason"] = "load_more_not_visible"
                try:
                    stats["button_candidates"] = page.locator("button").evaluate_all(
                        """els => els.map((el, i) => ({
                            index: i,
                            text: (el.innerText || el.textContent || '').trim(),
                            visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
                            disabled: !!el.disabled,
                            id: el.id || null,
                            className: el.className || null,
                            dataUrl: el.getAttribute('data-url'),
                            outerHTML: el.outerHTML.slice(0, 1200)
                        })).filter(x => x.dataUrl || /productos/i.test(x.text))"""
                    )
                except Exception:
                    stats["button_candidates"] = []
                break

            try:
                stats["button"] = button.evaluate(
                    """el => ({
                        tag: el.tagName,
                        text: (el.innerText || el.textContent || '').trim(),
                        outerHTML: el.outerHTML,
                        href: el.href || null,
                        dataUrl: el.getAttribute('data-url'),
                        id: el.id || null,
                        className: el.className || null,
                        dataset: {...el.dataset}
                    })"""
                )
            except Exception:
                pass

            next_url = button.get_attribute("data-url")
            current = previous

            if next_url:
                loader = None
                try:
                    # A page-level fetch is modified/intercepted by the retailer's
                    # frontend and can fail even though normal Edge navigation works.
                    # Load the official Search-UpdateGrid URL in a second tab so the
                    # request uses the browser's native network stack and the same
                    # browser context/cookies as the working category page.
                    loader = page.context.new_page()
                    response = self._goto_with_retries(loader, next_url)

                    status = response.status if response else None
                    if response and status >= 400:
                        page_requests.append(
                            {
                                "url": loader.url,
                                "status": status,
                                "ok": False,
                                "html_length": 0,
                            }
                        )
                        stats["stop_reason"] = "catalog_page_request_failed"
                        break

                    loader.wait_for_selector("body", state="attached", timeout=10_000)
                    html = loader.locator("body").inner_html(timeout=10_000)
                    page_requests.append(
                        {
                            "url": loader.url,
                            "status": status,
                            "ok": bool(response is None or status < 400),
                            "html_length": len(html or ""),
                        }
                    )

                    if not (html or "").strip():
                        stats["stop_reason"] = "catalog_page_empty"
                        break

                    # Remove the consumed button and append the official grid fragment
                    # to the category DOM. The newly appended fragment contains the
                    # next button.more[data-url], so the loop can continue naturally.
                    button.evaluate("el => el.remove()")
                    page.locator("body").evaluate(
                        """(body, html) => {
                            const container = document.createElement('div');
                            container.setAttribute('data-fg-loaded-page', '1');
                            container.innerHTML = html;
                            body.appendChild(container);
                        }""",
                        html,
                    )
                    stats["direct_loads"] += 1
                    page.wait_for_timeout(500)
                    current = self._product_link_count(page)
                except Exception as exc:
                    stats["stop_reason"] = f"catalog_page_request_error:{type(exc).__name__}"
                    stats["request_error"] = str(exc)
                    break
                finally:
                    if loader is not None:
                        try:
                            loader.close()
                        except Exception:
                            pass
            else:
                # Conservative fallback for a future markup change.
                try:
                    button.scroll_into_view_if_needed(timeout=5_000)
                    button.click(timeout=10_000)
                    stats["clicks"] += 1
                    for _ in range(15):
                        page.wait_for_timeout(1_000)
                        current = self._product_link_count(page)
                        if current > previous:
                            break
                except Exception as exc:
                    stats["stop_reason"] = f"load_more_click_error:{type(exc).__name__}"
                    break

            stats["final_links"] = current

            if current <= previous:
                stats["stop_reason"] = "no_growth_after_load"
                break

            previous = current
        else:
            stats["stop_reason"] = "max_load_more_reached"

        stats["final_links"] = self._product_link_count(page)
        if stats["stop_reason"] is None:
            stats["stop_reason"] = "completed"
        return stats

    @staticmethod
    def _extract_cards(page) -> list[dict]:
        return page.locator('a[href*=".html"]').evaluate_all(
            """
            anchors => {
              const out = [];
              const seen = new Set();
              const productRe = /-\\d{5,14}\\.html(?:$|[?#])/i;
              const moneyRe = /\\$\\s*[0-9][0-9,]*(?:\\.\\d{1,2})?/;
              for (const a of anchors) {
                const href = a.href || '';
                if (!productRe.test(href) || seen.has(href)) continue;
                let node = a;
                let card = null;
                for (let i = 0; i < 9 && node; i++, node = node.parentElement) {
                  const text = (node.innerText || '').trim();
                  if (moneyRe.test(text) && text.length >= 15 && text.length <= 3000) {
                    card = node;
                    if (/Agregar|Comparar|Favoritos/i.test(text)) break;
                  }
                }
                const source = card || a.parentElement || a;
                const text = (source.innerText || '').trim();
                if (!moneyRe.test(text)) continue;
                const named = source.querySelector('[class*="brand" i]');
                const titleNode = source.querySelector('h2, h3, h4, [class*="name" i], [class*="title" i]');
                let name = (a.innerText || a.getAttribute('aria-label') || a.getAttribute('title') || '').trim();
                if (!name && titleNode) name = (titleNode.innerText || '').trim();
                if (!name) {
                  const lines = text.split(/\\n+/).map(x => x.trim()).filter(Boolean);
                  name = lines.find(x => !moneyRe.test(x) && !/Agregar|Comparar|Oferta|Favoritos/i.test(x) && x.length > 8) || '';
                }
                if (!name) continue;
                seen.add(href);
                out.push({
                  href,
                  name,
                  brand: named ? (named.innerText || '').trim() : '',
                  text,
                  dataPid: source.getAttribute('data-product-id') || source.getAttribute('data-part-number') || a.getAttribute('data-product-id') || ''
                });
              }
              return out;
            }
            """
        )

    def _write_network_diagnostic(self, category: Category, exc: Exception) -> None:
        meta = {
            "retailer": "Farmacias Guadalajara",
            "category_id": category.id,
            "url": category.url,
            "status": "NETWORK_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "note": (
                "La configuración y parser están disponibles, pero la red actual no pudo "
                "establecer una respuesta HTTP con el dominio oficial."
            ),
        }
        (DIAGNOSTICS / f"farmacias_guadalajara_{category.id}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _env_flag(name: str, default: bool = False) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().casefold() in {"1", "true", "yes", "on"}

    @classmethod
    def _browser_launch_options(cls, headless: bool) -> dict:
        options: dict = {"headless": headless}
        args: list[str] = []

        # Preserve the old direct-Jupyter behavior unless the worker overrides it.
        if cls._env_flag("FG_DISABLE_HTTP2", default=True):
            args.append("--disable-http2")
        if cls._env_flag("FG_DISABLE_QUIC", default=False):
            args.append("--disable-quic")

        executable = (os.getenv("FG_BROWSER_EXECUTABLE") or "").strip()
        channel = (os.getenv("FG_BROWSER_CHANNEL") or "").strip()
        if executable:
            options["executable_path"] = executable
        elif channel:
            options["channel"] = channel
        if args:
            options["args"] = args
        return options

    @staticmethod
    def _browser_context_options() -> dict:
        options: dict = {
            "locale": "es-MX",
            "viewport": {"width": 1440, "height": 1000},
        }
        # Normally keep the browser's native User-Agent. A custom UA is only
        # applied when explicitly configured.
        user_agent = (os.getenv("FG_USER_AGENT") or "").strip()
        if user_agent:
            options["user_agent"] = user_agent
        return options

    def scrape_category(self, category: Category, location: Location) -> list[dict]:
        DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
        slug = category.id
        with sync_playwright() as p:
            browser = p.chromium.launch(**self._browser_launch_options(self.headless))
            context = browser.new_context(**self._browser_context_options())
            page = context.new_page()
            try:
                try:
                    response = self._goto_with_retries(page, category.url)
                except FarmaciasGuadalajaraNetworkUnavailable as exc:
                    self._write_network_diagnostic(category, exc)
                    raise

                if response and response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status} en {category.url}")

                page.wait_for_timeout(3_000)
                self._assert_not_blocked(page)
                target = self._target_count(page)
                expansion = self._expand_all_products(page, target)
                cards = self._extract_cards(page)

                now = datetime.now().astimezone().isoformat(timespec="seconds")
                rows: list[dict] = []
                for card in cards:
                    url = urljoin(BASE_URL, card.get("href") or "")
                    sku = clean_text(card.get("dataPid")) or self.extract_sku(url)
                    product = clean_text(card.get("name"))
                    if not sku or not product:
                        continue

                    current, regular, promotion = self._prices_from_text(card.get("text"))
                    if current is None:
                        continue
                    brand = clean_text(card.get("brand")) or self._infer_brand(
                        product,
                        card.get("text"),
                    )

                    rows.append(
                        {
                            "scrape_timestamp": now,
                            "retailer": "Farmacias Guadalajara",
                            "city": location.city,
                            "state": location.state,
                            "postal_code": location.postal_code,
                            "store": location.store,
                            "store_id": location.store_id,
                            "department": category.department,
                            "category": category.name,
                            "subcategory": category.subcategory,
                            "sub_subcategory": category.sub_subcategory,
                            "category_id": category.id,
                            "sku": sku,
                            "brand": brand,
                            "product": product,
                            "price_current": current,
                            "price_regular": regular,
                            "promotion": promotion,
                            "pickup_available": None,
                            "store_context_verified": False,
                            "store_context_method": "online_catalog_no_store_requested",
                            "url": url,
                            "price_raw": clean_text(card.get("text")),
                        }
                    )

                unique = {(row["sku"], row["url"]): row for row in rows}
                rows = list(unique.values())
                meta = {
                    "category_id": category.id,
                    "url": category.url,
                    "target_products": target,
                    "product_links": self._product_link_count(page),
                    "rows": len(rows),
                    "expansion": expansion,
                    "store_context": "online_catalog_no_store_requested",
                }
                (DIAGNOSTICS / f"farmacias_guadalajara_{slug}.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                (DIAGNOSTICS / f"farmacias_guadalajara_{slug}.html").write_text(
                    page.content(),
                    encoding="utf-8",
                )
                page.screenshot(
                    path=str(DIAGNOSTICS / f"farmacias_guadalajara_{slug}.png"),
                    full_page=True,
                )
                return rows
            finally:
                context.close()
                browser.close()


__all__ = [
    "FarmaciasGuadalajaraBlocked",
    "FarmaciasGuadalajaraNetworkUnavailable",
    "FarmaciasGuadalajaraScraper",
]
