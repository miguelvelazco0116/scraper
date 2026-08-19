from __future__ import annotations

import json
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

    def _expand_all_products(self, page, target: int | None) -> None:
        stable_rounds = 0
        previous = self._product_link_count(page)
        for _ in range(self.max_load_more):
            if target and previous >= target:
                break

            button = page.get_by_text(
                re.compile(
                    r"^(Ver más productos|Mostrar los siguientes .*productos)$",
                    re.IGNORECASE,
                )
            ).last
            try:
                if button.count() == 0 or not button.is_visible(timeout=1_500):
                    break
                button.scroll_into_view_if_needed()
                button.click(timeout=10_000)
                page.wait_for_timeout(1_500)
            except Exception:
                break

            current = self._product_link_count(page)
            if current <= previous:
                stable_rounds += 1
            else:
                stable_rounds = 0
            previous = current
            if stable_rounds >= 2:
                break

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

    def scrape_category(self, category: Category, location: Location) -> list[dict]:
        DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
        slug = category.id
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless, args=["--disable-http2"])
            context = browser.new_context(
                locale="es-MX",
                viewport={"width": 1440, "height": 1000},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/139.0.0.0 Safari/537.36"
                ),
            )
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
                self._expand_all_products(page, target)
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
