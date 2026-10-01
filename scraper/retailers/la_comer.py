from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urljoin

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from ..config import Category, Location
from ..parsers import clean_text


BASE_URL = "https://www.lacomer.com.mx/"
HOME_URL = "https://www.lacomer.com.mx/lacomer/#!/home?succId=287&succFmt=100"
DIAGNOSTICS = Path("diagnostics")


class LaComerBlocked(RuntimeError):
    pass


class LaComerNetworkUnavailable(RuntimeError):
    pass


class LaComerScraper:
    """Scraper de catálogo público de La Comer para categorías configuradas.

    El storefront público de La Comer depende del contexto de sucursal.
    Esta primera implementación usa el contexto ecommerce público succId=287
    y navega con Google Chrome/Playwright mediante la interfaz normal del sitio.

    No resuelve CAPTCHAs, no hace fingerprint spoofing y no intenta evadir
    controles anti-bot. Si la sesión es bloqueada, se guardan diagnósticos.
    """

    MONEY_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)")
    SKU_RE = re.compile(r"/detarticulo/(\d{8,14})(?:/|\?|#|$)", re.IGNORECASE)
    BLOCK_MARKERS = (
        "access denied",
        "forbidden",
        "verify you are human",
        "verifica que eres humano",
        "captcha",
        "request rejected",
    )
    SEARCH_QUERIES = {
        "detergentes-suavizantes": ("detergente", "suavizante"),
        "cuidado-bucal": (
            "cuidado bucal",
            "pasta dental",
            "cepillo dental",
            "enjuague bucal",
            "hilo dental",
        ),
    }
    EXCLUDE_TERMS = (
        "lavatrastes",
        "lava trastes",
        "lavavajillas",
        "lava vajillas",
        "dishwasher",
    )
    BRAND_CANDIDATES = (
        "Ariel", "Ace", "Roma", "Foca", "Blanca Nieves", "Persil", "Tide",
        "Bold", "Más Color", "Mas Color", "Carisma", "Viva", "1-2-3",
        "Downy", "Suavitel", "Ensueño", "Ensueño Max", "Members Mark",
        "Great Value", "Golden Hills", "Arm & Hammer", "Dreft", "Gain",
        "Colgate", "Oral-B", "Listerine", "Sensodyne", "Crest", "GUM",
        "Corega", "Aquafresh", "Curaprox", "Philips", "Bexident", "Parodontax",
        "Pro", "Reach",
    )

    ORAL_CARE_TERMS = (
        "dental",
        "bucal",
        "diente",
        "dentadura",
        "prótesis",
        "protesis",
        "cepillo",
        "enjuague",
        "hilo dental",
        "floss",
        "blanqueador",
        "colgate",
        "oral-b",
        "oral b",
        "listerine",
        "sensodyne",
        "crest",
        "corega",
        "aquafresh",
        "curaprox",
        "parodontax",
    )

    def __init__(
        self,
        headless: bool = False,
        browser_channel: str | None = "chrome",
        max_scroll_rounds: int = 80,
        wait_ms: int = 900,
    ) -> None:
        self.headless = headless
        self.browser_channel = browser_channel
        self.max_scroll_rounds = max_scroll_rounds
        self.wait_ms = wait_ms
        self.run_meta: dict = {}
        self.network_events: list[dict] = []

    @staticmethod
    def _search_url(query: str, store_id: str = "287") -> str:
        encoded = quote(query)
        return (
            "https://www.lacomer.com.mx/lacomer/goBusqueda.action"
            f"?succId={store_id}&ver=mislistas&succFmt=100&criterio={encoded}"
            f"#/{encoded}"
        )

    @staticmethod
    def _body_text(page) -> str:
        try:
            return page.locator("body").inner_text(timeout=8_000)
        except Exception:
            return ""

    def _save_diagnostics(self, page, prefix: str) -> None:
        DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", prefix)
        try:
            page.screenshot(
                path=str(DIAGNOSTICS / f"la_comer_{safe}.png"),
                full_page=True,
            )
        except Exception:
            pass
        try:
            (DIAGNOSTICS / f"la_comer_{safe}.html").write_text(
                page.content(),
                encoding="utf-8",
            )
        except Exception:
            pass
        meta = dict(self.run_meta)
        meta["network_events"] = self.network_events[-300:]
        try:
            (DIAGNOSTICS / f"la_comer_{safe}.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _capture_response(self, response) -> None:
        url = response.url
        lower = url.casefold()
        interesting = any(
            token in lower
            for token in (
                "busqueda",
                "search",
                "articulo",
                "producto",
                "product",
                "catalog",
                "categoria",
                "category",
            )
        )
        if not interesting:
            return
        self.network_events.append(
            {
                "status": response.status,
                "url": url,
                "resource_type": response.request.resource_type,
            }
        )

    def _assert_not_blocked(self, page, status: int | None = None) -> None:
        blob = f"{page.title()}\n{self._body_text(page)}".casefold()
        if status in (401, 403, 429) or any(x in blob for x in self.BLOCK_MARKERS):
            self.run_meta["blocked"] = True
            self.run_meta["blocked_status"] = status
            self._save_diagnostics(page, "blocked")
            raise LaComerBlocked(
                "La Comer bloqueó o desafió la sesión; se guardaron diagnósticos."
            )

    def _goto(self, page, url: str) -> None:
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        except PlaywrightError as exc:
            raise LaComerNetworkUnavailable(str(exc)) from exc
        status = response.status if response else None
        page.wait_for_timeout(max(self.wait_ms, 800))
        self._assert_not_blocked(page, status)

    def _try_search_ui(self, page, query: str) -> bool:
        selectors = (
            'input[type="search"]',
            'input[placeholder*="Buscar" i]',
            'input[placeholder*="busca" i]',
            'input[name*="buscar" i]',
            'input[name*="busq" i]',
            'input[name*="criterio" i]',
            'input[id*="buscar" i]',
            'input[id*="busq" i]',
        )
        for selector in selectors:
            try:
                nodes = page.locator(selector)
                for i in range(min(nodes.count(), 20)):
                    node = nodes.nth(i)
                    if not node.is_visible():
                        continue
                    node.fill(query, timeout=3_000)
                    node.press("Enter", timeout=3_000)
                    page.wait_for_timeout(2_000)
                    self._assert_not_blocked(page)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _click_load_more(page) -> bool:
        patterns = (
            "Ver más",
            "Ver mas",
            "Mostrar más",
            "Mostrar mas",
            "Cargar más",
            "Cargar mas",
            "Más productos",
            "Mas productos",
        )
        for label in patterns:
            try:
                nodes = page.get_by_text(label, exact=False)
                for i in range(min(nodes.count(), 20)):
                    node = nodes.nth(i)
                    if not node.is_visible():
                        continue
                    node.click(timeout=4_000)
                    page.wait_for_timeout(900)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _extract_raw_cards(page) -> list[dict]:
        return page.locator("body").evaluate(
            r"""
            () => {
              const normalize = v => String(v || '').replace(/\s+/g, ' ').trim();
              const money = /\$\s*[0-9][0-9,]*(?:\.\d{1,2})?/;

              function findCard(seed) {
                let node = seed;
                let best = null;
                for (let i = 0; i < 12 && node; i++, node = node.parentElement) {
                  const text = normalize(node.innerText || node.textContent);
                  if (!text || !money.test(text) || text.length > 3000) continue;
                  const detailLinks = node.querySelectorAll(
                    'a[href*="detarticulo"], a[href*="/producto"], a[href*="/product"]'
                  ).length;
                  if (detailLinks <= 2) {
                    best = node;
                    break;
                  }
                  if (!best) best = node;
                }
                return best || seed.parentElement || seed;
              }

              const seeds = [
                ...document.querySelectorAll(
                  'a[href*="detarticulo"], [data-product-id], [data-sku], [data-ean]'
                ),
                ...Array.from(document.querySelectorAll('button, a, [role="button"]'))
                  .filter(el => /Agregar|Añadir/i.test(normalize(el.innerText || el.textContent)))
              ];

              const out = [];
              const seen = new Set();

              for (const seed of seeds) {
                const card = findCard(seed);
                if (!card) continue;
                const rawText = (card.innerText || card.textContent || '').trim();
                const text = normalize(rawText);
                if (!text || !money.test(text)) continue;

                const links = Array.from(card.querySelectorAll('a[href]'));
                const detail = links.find(a =>
                  /detarticulo|\/producto|\/product/i.test(a.getAttribute('href') || '')
                );
                const href = detail ? detail.href : '';

                const dataNode = card.querySelector(
                  '[data-product-id], [data-sku], [data-ean]'
                );
                const code = dataNode
                  ? (
                      dataNode.getAttribute('data-sku') ||
                      dataNode.getAttribute('data-ean') ||
                      dataNode.getAttribute('data-product-id') ||
                      ''
                    )
                  : '';

                const img = card.querySelector('img[alt], img[title]');
                const heading = card.querySelector(
                  'h1, h2, h3, h4, h5, [class*="name"], [class*="title"]'
                );

                let title = detail
                  ? normalize(
                      detail.getAttribute('title') ||
                      detail.getAttribute('aria-label') ||
                      detail.innerText
                    )
                  : '';
                if (!title && heading) title = normalize(heading.innerText || heading.textContent);
                if (!title && img) {
                  title = normalize(img.getAttribute('alt') || img.getAttribute('title'));
                }

                const promo = rawText
                  .split(/\n+/)
                  .map(normalize)
                  .filter(x =>
                    /promoc|oferta|descuento|bonific|monedero|gratis|\d+\s*x\s*\d+/i.test(x)
                  )
                  .slice(0, 8);

                const key = [code, href, title, text.slice(0, 220)].join('|');
                if (seen.has(key)) continue;
                seen.add(key);

                out.push({code, href, title, text: rawText, promo});
              }
              return out;
            }
            """
        )

    @classmethod
    def _sku_from_card(cls, card: dict) -> str | None:
        href = clean_text(card.get("href"))
        if href:
            match = cls.SKU_RE.search(href)
            if match:
                return match.group(1)
        code = clean_text(card.get("code"))
        if code:
            match = re.search(r"\b(\d{8,14})\b", code)
            if match:
                return match.group(1)
        return None

    @classmethod
    def _prices(cls, text: str | None) -> tuple[float | None, float | None]:
        values: list[float] = []
        for raw in cls.MONEY_RE.findall(text or ""):
            try:
                value = float(raw.replace(",", ""))
            except ValueError:
                continue
            if value > 0 and value not in values:
                values.append(value)
        if not values:
            return None, None
        return min(values), max(values)

    @classmethod
    def _infer_brand(cls, product: str | None) -> str | None:
        if not product:
            return None
        for brand in cls.BRAND_CANDIDATES:
            if re.search(rf"(?<!\w){re.escape(brand)}(?!\w)", product, re.IGNORECASE):
                return brand
        first = clean_text(product.split(" ")[0])
        return first if first and len(first) > 2 else None

    @classmethod
    def _keep_result(
        cls,
        category_id: str,
        query: str,
        product: str,
        text: str,
    ) -> bool:
        blob = f"{product} {text}".casefold()

        if category_id == "detergentes-suavizantes":
            if any(term in blob for term in cls.EXCLUDE_TERMS):
                return False
            if query == "suavizante":
                return "suaviz" in blob
            if query == "detergente":
                return (
                    "deterg" in blob
                    or "jabón para ropa" in blob
                    or "jabon para ropa" in blob
                )
            return True

        if category_id == "cuidado-bucal":
            return any(term in blob for term in cls.ORAL_CARE_TERMS)

        return True

    def _collect_query(
        self,
        page,
        category: Category,
        query: str,
        location: Location,
    ) -> list[dict]:
        # Prefer normal user-facing search from the homepage. If the search box
        # is not exposed, fall back to La Comer's public search route.
        self._goto(page, HOME_URL)
        used_ui = self._try_search_ui(page, query)
        if not used_ui:
            self._goto(page, self._search_url(query, location.store_id or "287"))

        self.run_meta.setdefault("queries", {})[query] = {
            "used_search_ui": used_ui,
            "url": page.url,
            "title": page.title(),
        }

        cards_by_key: dict[str, dict] = {}
        stable = 0
        previous = 0

        for round_number in range(1, self.max_scroll_rounds + 1):
            self._assert_not_blocked(page)
            for card in self._extract_raw_cards(page):
                sku = self._sku_from_card(card) or ""
                href = clean_text(card.get("href")) or ""
                title = clean_text(card.get("title")) or ""
                text = clean_text(card.get("text")) or ""
                key = sku or href or f"{title}|{text[:180]}"
                if key:
                    cards_by_key[key] = card

            total = len(cards_by_key)
            if total == previous:
                stable += 1
            else:
                stable = 0
            previous = total

            if stable >= 3:
                if self._click_load_more(page):
                    stable = 0
                    continue
                break

            try:
                page.evaluate(
                    "window.scrollBy(0, Math.max(650, Math.floor(window.innerHeight * 0.8)))"
                )
            except Exception:
                pass
            page.wait_for_timeout(450)

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        rows: list[dict] = []

        for card in cards_by_key.values():
            text = clean_text(card.get("text")) or ""
            product = clean_text(card.get("title"))
            if not product:
                lines = [clean_text(x) for x in text.splitlines()]
                product = next(
                    (
                        x for x in lines
                        if x
                        and "$" not in x
                        and len(x) >= 5
                        and not re.search(
                            r"agregar|oferta|descuento|bonific|monedero",
                            x,
                            re.IGNORECASE,
                        )
                    ),
                    None,
                )
            if not product or not self._keep_result(
                category.id,
                query,
                product,
                text,
            ):
                continue

            current, regular = self._prices(text)
            if current is None:
                continue

            href = clean_text(card.get("href"))
            url = urljoin(BASE_URL, href) if href else None
            sku = self._sku_from_card(card)
            promotion = clean_text(" | ".join(card.get("promo") or []))

            rows.append(
                {
                    "scrape_timestamp": now,
                    "retailer": "La Comer",
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
                    "brand": self._infer_brand(product),
                    "product": product,
                    "price_current": current,
                    "price_regular": regular,
                    "promotion": promotion,
                    "pickup_available": None,
                    "store_context_verified": bool(location.store_id),
                    "store_context_method": "lacomer_ecommerce_succId_query",
                    "url": url,
                    "price_raw": text,
                }
            )

        self.run_meta["queries"][query]["cards_discovered"] = len(cards_by_key)
        self.run_meta["queries"][query]["rows_kept"] = len(rows)
        return rows

    def scrape_category(self, category: Category, location: Location) -> list[dict]:
        queries = self.SEARCH_QUERIES.get(category.id, (category.subcategory or category.name,))
        self.run_meta = {
            "retailer": "La Comer",
            "category_id": category.id,
            "home_url": HOME_URL,
            "store_id": location.store_id,
            "queries": {},
            "blocked": False,
        }
        self.network_events = []

        with sync_playwright() as p:
            launch_kwargs = {"headless": self.headless}
            if self.browser_channel:
                launch_kwargs["channel"] = self.browser_channel
            browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(
                locale="es-MX",
                viewport={"width": 1440, "height": 1000},
            )
            page = context.new_page()
            page.on("response", self._capture_response)

            try:
                all_rows: list[dict] = []
                for query in queries:
                    all_rows.extend(
                        self._collect_query(page, category, query, location)
                    )

                unique: dict[str, dict] = {}
                for row in all_rows:
                    key = (
                        clean_text(row.get("sku"))
                        or clean_text(row.get("url"))
                        or "|".join(
                            [
                                clean_text(row.get("product")) or "",
                                str(row.get("price_current") or ""),
                                clean_text(row.get("price_raw")) or "",
                            ]
                        )
                    )
                    if key:
                        unique[key] = row

                rows = list(unique.values())
                self.run_meta["rows"] = len(rows)
                self.run_meta["unique_skus"] = len(
                    {str(x["sku"]) for x in rows if x.get("sku")}
                )
                self.run_meta["price_complete"] = sum(
                    x.get("price_current") is not None for x in rows
                )
                self.run_meta["url_complete"] = sum(bool(x.get("url")) for x in rows)
                self.run_meta["final_url"] = page.url
                self.run_meta["body_preview"] = self._body_text(page)[:4000]
                self._save_diagnostics(page, category.id)
                return rows
            except (LaComerBlocked, LaComerNetworkUnavailable):
                raise
            except PlaywrightTimeoutError as exc:
                self.run_meta["error"] = f"TimeoutError: {exc}"
                self._save_diagnostics(page, f"{category.id}_timeout")
                raise LaComerNetworkUnavailable(str(exc)) from exc
            finally:
                context.close()
                browser.close()


__all__ = [
    "LaComerBlocked",
    "LaComerNetworkUnavailable",
    "LaComerScraper",
]
