from __future__ import annotations

import json
import re
import time
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from ..config import Category, Location
from ..parsers import clean_text


BASE_URL = "https://www.benavides.com.mx/"
DIAGNOSTICS = Path("diagnostics")


class BenavidesBlocked(RuntimeError):
    pass


class BenavidesNetworkUnavailable(RuntimeError):
    pass


class _VisibleTextParser(HTMLParser):
    BLOCK_TAGS = {
        "address", "article", "aside", "blockquote", "br", "div", "dl",
        "dt", "dd", "fieldset", "figcaption", "figure", "footer", "form",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main",
        "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot",
        "th", "thead", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if self._skip_depth == 0 and tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg"}:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth == 0 and tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data:
            self._parts.append(data)

    def text(self) -> str:
        raw = unescape("".join(self._parts))
        lines = [
            re.sub(r"\s+", " ", line).strip()
            for line in raw.splitlines()
        ]
        return "\n".join(line for line in lines if line)


class _CategoryProductParser(HTMLParser):
    """Extract Magento product links from server-rendered category HTML."""

    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.products: list[dict] = []
        self._product_depth = 0
        self._current: dict | None = None
        self._capture_title = False
        self._title_parts: list[str] = []
        self._card_text_parts: list[str] = []

    @staticmethod
    def _attrs(attrs) -> dict[str, str]:
        return {
            str(key).casefold(): str(value or "")
            for key, value in attrs
        }

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.casefold()
        data = self._attrs(attrs)
        classes = set(data.get("class", "").split())

        is_product_root = (
            tag in {"li", "div"}
            and (
                "product-item" in classes
                or (
                    "product" in classes
                    and "item" in classes
                )
            )
        )

        if is_product_root and self._product_depth == 0:
            self._product_depth = 1
            self._current = {
                "href": "",
                "title": "",
                "sku_hint": (
                    data.get("data-product-sku")
                    or data.get("data-sku")
                    or data.get("data-product-code")
                    or data.get("data-product-id")
                    or ""
                ),
            }
            self._card_text_parts = []
            return

        if self._product_depth:
            if tag not in self.VOID_TAGS:
                self._product_depth += 1

            for key in (
                "data-product-sku",
                "data-sku",
                "data-product-code",
                "data-product-id",
            ):
                value = data.get(key)
                if value and self._current and not self._current.get("sku_hint"):
                    self._current["sku_hint"] = value

            if tag == "a" and self._current is not None:
                href = data.get("href", "")
                class_text = data.get("class", "")
                if href and (
                    "product-item-link" in class_text
                    or "product-item-photo" in class_text
                    or not self._current.get("href")
                ):
                    absolute = urljoin(self.base_url, href)
                    if absolute.startswith("https://www.benavides.com.mx/"):
                        self._current["href"] = absolute.split("?", 1)[0].rstrip("/")

                if "product-item-link" in class_text:
                    self._capture_title = True
                    self._title_parts = []

            if tag == "img" and self._current is not None:
                alt = data.get("alt", "").strip()
                if alt and not self._current.get("title"):
                    self._current["title"] = alt

    def handle_data(self, data: str) -> None:
        if self._product_depth and data:
            self._card_text_parts.append(data)
        if self._capture_title and data:
            self._title_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture_title and tag.casefold() == "a":
            title = re.sub(r"\s+", " ", " ".join(self._title_parts)).strip()
            if self._current is not None and title:
                self._current["title"] = title
            self._capture_title = False
            self._title_parts = []

        if not self._product_depth or tag.casefold() in self.VOID_TAGS:
            return

        self._product_depth -= 1
        if self._product_depth == 0:
            current = self._current or {}
            href = clean_text(current.get("href"))
            title = clean_text(current.get("title"))
            if href and title:
                card_text = clean_text(
                    re.sub(r"\s+", " ", " ".join(self._card_text_parts))
                )
                self.products.append(
                    {
                        "href": href,
                        "title": title,
                        "card_text": card_text or "",
                        "sku_hint": clean_text(current.get("sku_hint")) or "",
                    }
                )
            self._current = None
            self._card_text_parts = []


class BenavidesScraper:
    """Scraper del catálogo público de Farmacias Benavides.

    Descubre el catálogo por la paginación pública ?p=N y visita cada ficha
    mediante el APIRequestContext de la misma sesión Playwright para obtener
    SKU, precio, marca y disponibilidad sin abrir cientos de pestañas.
    """

    TARGET_RE = re.compile(
        r"Art[ií]culos(?:\s+\d+\s*-\s*\d+)?\s+de\s+(\d{1,6})",
        re.IGNORECASE,
    )
    TARGET_SINGLE_RE = re.compile(
        r"Art[ií]culos\s+(\d{1,6})(?!\s*-|\s+de)",
        re.IGNORECASE,
    )
    SKU_RE = re.compile(r"\bSKU\s*\n?\s*([A-Za-z0-9._-]+)", re.IGNORECASE)
    MONEY_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)")
    PRICE_PAIR_RE = re.compile(
        r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)"
        r"\s+Precio\s+Regular\s+"
        r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
        re.IGNORECASE,
    )
    BLOCK_MARKERS = (
        "access denied",
        "sorry, you have been blocked",
        "forbidden",
        "verify you are human",
        "verifica que eres humano",
        "captcha",
        "request rejected",
        "press and hold",
        "mantén presionado",
        "manten presionado",
    )

    def __init__(
        self,
        headless: bool = True,
        browser_channel: str | None = "chrome",
        max_pages: int = 60,
        wait_ms: int = 450,
        detail_pause_ms: int = 60,
    ) -> None:
        self.headless = headless
        self.browser_channel = browser_channel
        self.max_pages = max_pages
        self.wait_ms = wait_ms
        self.detail_pause_ms = detail_pause_ms
        self.run_meta: dict = {}
        self._detail_transport = "request"

    @staticmethod
    def _normalize(value: str | None) -> str:
        if not value:
            return ""
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _page_url(base_url: str, page_number: int) -> str:
        parts = urlsplit(base_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if page_number <= 1:
            query.pop("p", None)
        else:
            query["p"] = str(page_number)
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    @staticmethod
    def _body_text(page) -> str:
        try:
            return page.locator("body").inner_text(timeout=10_000)
        except Exception:
            return ""

    @classmethod
    def _is_blocked_text(cls, text: str, status: int | None = None) -> bool:
        blob = (text or "").casefold()
        return bool(
            status in (401, 403, 429)
            or any(marker in blob for marker in cls.BLOCK_MARKERS)
        )

    def _goto(self, page, url: str) -> None:
        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=75_000,
            )
        except PlaywrightError as exc:
            raise BenavidesNetworkUnavailable(str(exc)) from exc

        status = response.status if response else None
        page.wait_for_timeout(self.wait_ms)
        body = self._body_text(page)
        if self._is_blocked_text(body, status):
            raise BenavidesBlocked(
                f"Farmacias Benavides bloqueó la navegación ({status or 'sin status'})."
            )

    @classmethod
    def _target_count(cls, body: str) -> int | None:
        values: list[int] = []
        for pattern in (cls.TARGET_RE, cls.TARGET_SINGLE_RE):
            for raw in pattern.findall(body or ""):
                try:
                    values.append(int(raw))
                except ValueError:
                    continue
        return max(values) if values else None

    @staticmethod
    def _product_links(page) -> list[dict]:
        return page.locator("body").evaluate(
            r"""
            () => {
              const norm = value =>
                String(value || '').replace(/\s+/g, ' ').trim();
              const money = /\$\s*[0-9]/;
              const out = [];
              const seen = new Set();

              const candidates = Array.from(document.querySelectorAll(
                'a.product-item-link, li.product-item a[href], .product-item a[href]'
              ));

              for (const a of candidates) {
                let url;
                try { url = new URL(a.href, location.href); } catch (_) { continue; }
                if (url.origin !== location.origin) continue;
                if (!url.pathname || url.pathname === '/') continue;
                if (/\/(customer|checkout|catalogsearch|wishlist|compare)\//i.test(url.pathname)) {
                  continue;
                }

                let card = a.closest('li.product-item, .product-item, [class*="product-item"]');
                if (!card) {
                  let node = a;
                  for (let i = 0; i < 7 && node; i++, node = node.parentElement) {
                    const text = norm(node.innerText || node.textContent);
                    if (text && money.test(text) && /Agregar al carrito|AGOTADO/i.test(text)) {
                      card = node;
                      break;
                    }
                  }
                }

                const cardText = norm(card ? (card.innerText || card.textContent) : '');
                if (!cardText || !money.test(cardText)) continue;

                const title =
                  norm(a.getAttribute('title')) ||
                  norm(a.innerText || a.textContent) ||
                  norm((a.querySelector('img[alt]') || {}).alt);

                if (!title || title.length < 2) continue;

                const href = url.origin + url.pathname.replace(/\/$/, '');
                if (seen.has(href)) continue;
                seen.add(href);

                let skuHint = '';
                if (card) {
                  const nodes = [card, ...Array.from(card.querySelectorAll('*')).slice(0, 180)];
                  for (const node of nodes) {
                    if (!node || !node.getAttribute) continue;
                    for (const attr of [
                      'data-product-sku', 'data-sku', 'data-product-code',
                      'data-product-id'
                    ]) {
                      const value = node.getAttribute(attr);
                      if (value) {
                        skuHint = norm(value);
                        break;
                      }
                    }
                    if (skuHint) break;
                  }
                }

                out.push({
                  href,
                  title,
                  card_text: cardText,
                  sku_hint: skuHint,
                });
              }
              return out;
            }
            """
        )

    @staticmethod
    def _html_to_text(html: str) -> str:
        parser = _VisibleTextParser()
        parser.feed(html or "")
        parser.close()
        return parser.text()

    @staticmethod
    def _float(raw: str | None) -> float | None:
        if raw is None:
            return None
        try:
            return float(raw.replace(",", ""))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _line_after(lines: list[str], label: str) -> str | None:
        label_cf = label.casefold()
        for index, line in enumerate(lines):
            if line.casefold() == label_cf and index + 1 < len(lines):
                return clean_text(lines[index + 1])
        return None

    @classmethod
    def _availability_from_card(cls, card_text: str | None) -> dict:
        text = clean_text(card_text)
        lower = (text or "").casefold()

        unavailable_markers = (
            "agotado",
            "sin existencia",
            "no disponible",
        )
        available_markers = (
            "agregar al carrito",
            "añadir al carrito",
            "comprar",
        )

        if any(marker in lower for marker in unavailable_markers):
            return {
                "is_available": 0,
                "availability_status": "UNAVAILABLE",
                "availability_raw": text,
                "availability_source": "category_card",
            }
        if any(marker in lower for marker in available_markers):
            return {
                "is_available": 1,
                "availability_status": "AVAILABLE",
                "availability_raw": text,
                "availability_source": "category_card",
            }
        return {
            "is_available": None,
            "availability_status": "UNKNOWN",
            "availability_raw": text or None,
            "availability_source": None,
        }

    @classmethod
    def _parse_detail(
        cls,
        text: str,
        fallback_title: str | None,
        sku_hint: str | None,
    ) -> dict:
        lines = [clean_text(x) for x in (text or "").splitlines()]
        lines = [x for x in lines if x]
        joined = "\n".join(lines)

        sku_match = cls.SKU_RE.search(joined)
        sku = clean_text(sku_match.group(1)) if sku_match else clean_text(sku_hint)

        product = clean_text(fallback_title)
        if sku_match:
            before = joined[:sku_match.start()].splitlines()
            for candidate in reversed(before[-8:]):
                candidate = clean_text(candidate)
                if (
                    candidate
                    and len(candidate) <= 280
                    and candidate.casefold() not in {
                        "sku",
                        "skip to the end of the images gallery",
                        "skip to the beginning of the images gallery",
                    }
                ):
                    product = candidate
                    break

        brand = None
        for index, line in enumerate(lines):
            if line.casefold() == "marca" and index + 1 < len(lines):
                brand = clean_text(lines[index + 1])
                break
            match = re.match(r"^Marca\s*\|\s*(.+)$", line, flags=re.IGNORECASE)
            if match:
                brand = clean_text(match.group(1))
                break

        price_segment = joined
        marker = re.search(
            r"Precios y disponibilidad válidos en tienda en línea",
            joined,
            flags=re.IGNORECASE,
        )
        if marker:
            price_segment = joined[marker.end():]
        info_marker = re.search(
            r"\nInformación del producto\b",
            price_segment,
            flags=re.IGNORECASE,
        )
        if info_marker:
            price_segment = price_segment[:info_marker.start()]

        pair = cls.PRICE_PAIR_RE.search(price_segment)
        if pair:
            current = cls._float(pair.group(1))
            regular = cls._float(pair.group(2))
        else:
            values = [
                cls._float(raw)
                for raw in cls.MONEY_RE.findall(price_segment)
            ]
            values = [value for value in values if value is not None and value > 0]
            current = values[0] if values else None
            regular = current

        if current is None:
            pair = cls.PRICE_PAIR_RE.search(joined)
            if pair:
                current = cls._float(pair.group(1))
                regular = cls._float(pair.group(2))

        promotion = None
        promo_match = re.search(
            r"Promociones\s*\|\s*(.+?)(?:\nTipo de entrega|\nInformación adicional)",
            joined,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if promo_match:
            promotion = clean_text(
                re.sub(r"\s+", " ", promo_match.group(1))
            )
        elif current is not None and regular is not None and current < regular:
            promotion = "Precio promocional"

        lower = joined.casefold()
        unavailable_markers = (
            "agotado",
            "el producto no está disponible",
            "el producto no esta disponible",
            "no disponible en la región seleccionada",
            "no disponible en la region seleccionada",
            "sin existencia",
        )
        available_markers = (
            "pieza disponible",
            "producto disponible",
            "disponible para envío",
            "disponible para envio",
        )

        unavailable = any(marker in lower for marker in unavailable_markers)
        available = any(marker in lower for marker in available_markers)

        if unavailable:
            is_available = 0
            availability_status = "UNAVAILABLE"
            availability_source = "pdp_text"
        elif available:
            is_available = 1
            availability_status = "AVAILABLE"
            availability_source = "pdp_text"
        else:
            is_available = None
            availability_status = "UNKNOWN"
            availability_source = None

        raw_parts = []
        for marker in (
            "Pieza disponible",
            "El producto no está disponible en la región seleccionada.",
            "El producto no esta disponible en la region seleccionada.",
            "Agotado",
        ):
            if marker.casefold() in lower:
                raw_parts.append(marker)

        return {
            "sku": sku,
            "product": product,
            "brand": brand,
            "price_current": current,
            "price_regular": regular,
            "promotion": promotion,
            "is_available": is_available,
            "availability_status": availability_status,
            "availability_raw": " | ".join(dict.fromkeys(raw_parts)) or None,
            "availability_source": availability_source,
        }

    def _fetch_detail(
        self,
        request_context,
        page,
        href: str,
    ) -> tuple[str, int | None, str]:
        if self._detail_transport == "browser":
            self._goto(page, href)
            return self._body_text(page), None, "browser"

        try:
            response = request_context.get(
                href,
                timeout=60_000,
                fail_on_status_code=False,
            )
            status = int(response.status)
            body = response.text()
            text = self._html_to_text(body)
        except PlaywrightError as exc:
            print(
                "Benavides detail request falló; cambiando a navegación "
                f"de Chrome: {type(exc).__name__}: {exc}",
                flush=True,
            )
            self._detail_transport = "browser"
            self._goto(page, href)
            return self._body_text(page), None, "browser"

        if self._is_blocked_text(text, status) or status >= 500:
            print(
                f"Benavides detail HTTP {status}; cambiando a navegación "
                "normal de Chrome.",
                flush=True,
            )
            self._detail_transport = "browser"
            self._goto(page, href)
            return self._body_text(page), status, "browser"

        return text, status, "request"

    def _fetch_category_html(
        self,
        request_context,
        page,
        url: str,
    ) -> tuple[str, int | None, str]:
        try:
            response = request_context.get(
                url,
                timeout=60_000,
                fail_on_status_code=False,
            )
            status = int(response.status)
            html = response.text()
            text = self._html_to_text(html)

            if self._is_blocked_text(text, status):
                raise BenavidesBlocked(
                    f"Farmacias Benavides bloqueó categoría {url} "
                    f"(HTTP {status})."
                )
            if status >= 500:
                raise BenavidesNetworkUnavailable(
                    f"Farmacias Benavides devolvió HTTP {status} en {url}."
                )
            if status >= 400:
                raise BenavidesNetworkUnavailable(
                    f"Farmacias Benavides devolvió HTTP {status} en {url}."
                )
            return html, status, "request"
        except BenavidesBlocked:
            raise
        except Exception as exc:
            print(
                "Benavides category request falló; usando navegación "
                f"de Chrome: {type(exc).__name__}: {exc}",
                flush=True,
            )
            self._goto(page, url)
            return page.content(), None, "browser"

    @classmethod
    def _product_links_from_html(
        cls,
        html: str,
        base_url: str,
    ) -> list[dict]:
        parser = _CategoryProductParser(base_url)
        parser.feed(html or "")
        parser.close()

        unique: dict[str, dict] = {}
        for item in parser.products:
            href = clean_text(item.get("href"))
            if href:
                unique[href] = item
        return list(unique.values())

    def _discover_links(
        self,
        request_context,
        page,
        category: Category,
    ) -> tuple[list[dict], dict]:
        found: dict[str, dict] = {}
        pages: list[dict] = []
        target: int | None = None
        stale_pages = 0

        for page_number in range(1, self.max_pages + 1):
            url = self._page_url(category.url, page_number)
            html, http_status, transport = self._fetch_category_html(
                request_context,
                page,
                url,
            )
            body = self._html_to_text(html)

            current_target = self._target_count(body)
            if current_target is not None:
                target = max(target or 0, current_target)

            links = self._product_links_from_html(html, url)

            before = len(found)
            for item in links:
                href = clean_text(item.get("href"))
                if href:
                    found[href] = item
            after = len(found)

            pages.append(
                {
                    "page": page_number,
                    "url": url,
                    "http_status": http_status,
                    "transport": transport,
                    "links_on_page": len(links),
                    "new_links": after - before,
                    "cumulative_links": after,
                    "target_products": target,
                }
            )

            print(
                f"Benavides [{category.id}] page={page_number}: "
                f"links={len(links)}, new={after - before}, "
                f"cumulative={after}, target={target}, "
                f"transport={transport}",
                flush=True,
            )

            if target is not None and after >= target:
                break

            stale_pages = stale_pages + 1 if after == before else 0
            if stale_pages >= 2:
                break

            if not links:
                break

        return list(found.values()), {
            "target_products": target,
            "product_links": len(found),
            "discovery_complete": bool(
                target is not None and len(found) == target
            ),
            "pages": pages,
        }

    def scrape_category(
        self,
        category: Category,
        location: Location,
    ) -> list[dict]:
        self.run_meta = {
            "retailer": "Farmacias Benavides",
            "category_id": category.id,
            "category_url": category.url,
            "blocked_detected": False,
        }

        with sync_playwright() as p:
            launch_kwargs = {"headless": self.headless}
            if self.browser_channel:
                launch_kwargs["channel"] = self.browser_channel

            try:
                browser = p.chromium.launch(**launch_kwargs)
            except PlaywrightError as exc:
                raise BenavidesNetworkUnavailable(
                    f"No fue posible abrir Chrome: {exc}"
                ) from exc

            context = browser.new_context(
                locale="es-MX",
                viewport={"width": 1440, "height": 1000},
            )
            page = context.new_page()

            try:
                links, discovery = self._discover_links(
                    context.request,
                    page,
                    category,
                )
                self.run_meta.update(discovery)

                now = datetime.now().astimezone().isoformat(timespec="seconds")
                rows: list[dict] = []
                detail_errors: list[dict] = []
                no_price: list[dict] = []
                unknown_availability: list[dict] = []
                detail_transport_counts = {"request": 0, "browser": 0}

                for index, item in enumerate(links, start=1):
                    href = clean_text(item.get("href"))
                    if not href:
                        continue

                    fallback_title = clean_text(item.get("title"))
                    sku_hint = clean_text(item.get("sku_hint"))

                    try:
                        text, http_status, detail_transport = self._fetch_detail(
                            context.request,
                            page,
                            href,
                        )
                        detail_transport_counts[detail_transport] = (
                            detail_transport_counts.get(detail_transport, 0) + 1
                        )
                        detail = self._parse_detail(
                            text,
                            fallback_title,
                            sku_hint,
                        )
                        if detail.get("is_available") not in (0, 1):
                            card_availability = self._availability_from_card(
                                item.get("card_text")
                            )
                            if card_availability.get("is_available") in (0, 1):
                                detail.update(card_availability)
                    except (BenavidesBlocked, BenavidesNetworkUnavailable):
                        raise
                    except Exception as exc:
                        detail_errors.append(
                            {
                                "url": href,
                                "title": fallback_title,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                        continue

                    if detail.get("price_current") is None:
                        no_price.append(
                            {
                                "url": href,
                                "sku": detail.get("sku"),
                                "title": detail.get("product") or fallback_title,
                            }
                        )

                    if detail.get("is_available") not in (0, 1):
                        unknown_availability.append(
                            {
                                "url": href,
                                "sku": detail.get("sku"),
                                "title": detail.get("product") or fallback_title,
                                "availability_raw": detail.get("availability_raw"),
                            }
                        )

                    print(
                        f"Benavides detail {index}/{len(links)}: "
                        f"sku={detail.get('sku') or '-'} | "
                        f"price={detail.get('price_current')} | "
                        f"available={detail.get('is_available')} | "
                        f"{detail.get('product') or fallback_title}",
                        flush=True,
                    )

                    rows.append(
                        {
                            "scrape_timestamp": now,
                            "retailer": "Farmacias Benavides",
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
                            "sku": detail.get("sku"),
                            "brand": detail.get("brand"),
                            "product": detail.get("product") or fallback_title,
                            "price_current": detail.get("price_current"),
                            "price_regular": detail.get("price_regular"),
                            "promotion": detail.get("promotion"),
                            "package_type": None,
                            "units_per_package": None,
                            "is_single_item": None,
                            "price_per_unit": None,
                            "single_item_presentation": None,
                            "single_item_price": None,
                            "availability_status": detail.get("availability_status"),
                            "is_available": detail.get("is_available"),
                            "availability_raw": detail.get("availability_raw"),
                            "pickup_available": None,
                            "store_context_verified": False,
                            "store_context_method": "benavides_online_catalog",
                            "url": href,
                            "price_raw": json.dumps(
                                {
                                    "price_current": detail.get("price_current"),
                                    "price_regular": detail.get("price_regular"),
                                    "promotion": detail.get("promotion"),
                                    "http_status": http_status,
                                    "detail_transport": detail_transport,
                                    "availability_source": detail.get("availability_source"),
                                },
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        }
                    )

                    if self.detail_pause_ms:
                        time.sleep(self.detail_pause_ms / 1000)

                unique: dict[str, dict] = {}
                for row in rows:
                    key = (
                        clean_text(row.get("sku"))
                        or clean_text(row.get("url"))
                        or clean_text(row.get("product"))
                    )
                    if key:
                        unique[key] = row
                rows = list(unique.values())

                target = self.run_meta.get("target_products")
                self.run_meta.update(
                    {
                        "rows": len(rows),
                        "sku_complete": sum(
                            bool(clean_text(row.get("sku"))) for row in rows
                        ),
                        "price_complete": sum(
                            row.get("price_current") is not None for row in rows
                        ),
                        "regular_price_complete": sum(
                            row.get("price_regular") is not None for row in rows
                        ),
                        "url_complete": sum(
                            bool(clean_text(row.get("url"))) for row in rows
                        ),
                        "availability_complete": sum(
                            row.get("is_available") in (0, 1) for row in rows
                        ),
                        "detail_errors": detail_errors,
                        "products_without_price": no_price,
                        "unknown_availability": unknown_availability,
                        "detail_transport_counts": detail_transport_counts,
                    }
                )

                complete = bool(
                    target is not None
                    and len(rows) == int(target)
                    and self.run_meta["product_links"] == int(target)
                    and self.run_meta["sku_complete"] == len(rows)
                    and self.run_meta["price_complete"] == len(rows)
                    and self.run_meta["url_complete"] == len(rows)
                    and self.run_meta["availability_complete"] == len(rows)
                    and not detail_errors
                )
                self.run_meta["status"] = "SUCCESS" if complete else "PARTIAL"

                DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
                (
                    DIAGNOSTICS
                    / f"benavides_{category.id}.json"
                ).write_text(
                    json.dumps(
                        self.run_meta,
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                return rows
            except BenavidesBlocked:
                self.run_meta["blocked_detected"] = True
                raise
            finally:
                browser.close()


__all__ = [
    "BenavidesBlocked",
    "BenavidesNetworkUnavailable",
    "BenavidesScraper",
]
