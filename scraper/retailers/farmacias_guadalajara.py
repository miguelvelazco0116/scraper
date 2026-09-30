from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..config import Category, Location
from ..parsers import clean_text


BASE_URL = "https://www.farmaciasguadalajara.com/"
DIAGNOSTICS = Path("diagnostics")


class FarmaciasGuadalajaraBlocked(RuntimeError):
    pass


class FarmaciasGuadalajaraNetworkUnavailable(RuntimeError):
    pass


class FarmaciasGuadalajaraScraper:
    """Scraper de Farmacias Guadalajara con Chrome + Selenium.

    Flujo:
    1. Abre la categoría en Google Chrome.
    2. Lee la URL pública que el propio botón "Ver más productos" expone
       en su atributo data-url.
    3. Navega directamente por esas páginas del grid cambiando start=20,40...
       en vez de depender del handler JavaScript del botón.
    4. Extrae y deduplica productos.

    No usa Playwright, CDP, perfiles persistentes, OpenAI ni técnicas stealth.
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
    def _build_driver(headless: bool):
        options = webdriver.ChromeOptions()
        options.add_argument("--lang=es-MX")
        options.add_argument("--window-size=1440,1000")
        if headless:
            options.add_argument("--headless=new")

        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(60)
        return driver

    @staticmethod
    def _body_text(driver) -> str:
        try:
            return driver.find_element(By.TAG_NAME, "body").text or ""
        except Exception:
            return ""

    @classmethod
    def _assert_not_blocked(cls, driver) -> None:
        text = cls._body_text(driver).casefold()
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

    @classmethod
    def _goto(cls, driver, url: str) -> None:
        try:
            driver.get(url)
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except (TimeoutException, WebDriverException) as exc:
            raise FarmaciasGuadalajaraNetworkUnavailable(
                f"No se pudo cargar {url}. {type(exc).__name__}: {exc}"
            ) from exc

    @classmethod
    def _target_count(cls, driver) -> int | None:
        text = cls._body_text(driver)
        matches = re.findall(
            r"\(?\b(\d{1,5})\s+productos?\b\)?",
            text,
            flags=re.IGNORECASE,
        )
        return max((int(x) for x in matches), default=None)

    @staticmethod
    def _grid_url(driver) -> str | None:
        try:
            button = driver.find_element(By.CSS_SELECTOR, "button.more[data-url]")
        except Exception:
            return None
        raw = (button.get_attribute("data-url") or "").strip()
        return urljoin(driver.current_url, raw) if raw else None

    @staticmethod
    def _with_start(url: str, start: int, size: int = 20) -> str:
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["start"] = str(start)
        query["sz"] = str(size)
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(query),
                parts.fragment,
            )
        )

    @staticmethod
    def _extract_cards_from_root_script() -> str:
        return r"""
        const root = arguments[0] || document;
        const out = [];
        const seen = new Set();
        const productRe = /-\\d{5,14}\\.html(?:$|[?#])/i;
        const moneyRe = /\\$\\s*[0-9][0-9,]*(?:\\.\\d{1,2})?/;

        for (const a of Array.from(root.querySelectorAll('a[href*=".html"]'))) {
          const href = a.href || a.getAttribute('href') || '';
          if (!productRe.test(href) || seen.has(href)) continue;

          let node = a;
          let card = null;
          for (let i = 0; i < 9 && node; i++, node = node.parentElement) {
            const text = (node.innerText || node.textContent || '').trim();
            if (moneyRe.test(text) && text.length >= 15 && text.length <= 3000) {
              card = node;
              if (/Agregar|Comparar|Favoritos/i.test(text)) break;
            }
          }

          const source = card || a.parentElement || a;
          const text = (source.innerText || source.textContent || '').trim();
          if (!moneyRe.test(text)) continue;

          const brandNode = source.querySelector('[class*="brand" i]');
          const titleNode = source.querySelector(
            'h2, h3, h4, [class*="name" i], [class*="title" i]'
          );

          let name = (
            a.innerText ||
            a.textContent ||
            a.getAttribute('aria-label') ||
            a.getAttribute('title') ||
            ''
          ).trim();

          if (!name && titleNode) {
            name = (titleNode.innerText || titleNode.textContent || '').trim();
          }

          if (!name) {
            const lines = text.split(/\\n+/).map(x => x.trim()).filter(Boolean);
            name = lines.find(x =>
              !moneyRe.test(x) &&
              !/Agregar|Comparar|Oferta|Favoritos/i.test(x) &&
              x.length > 8
            ) || '';
          }

          if (!name) continue;

          seen.add(href);
          out.push({
            href,
            name,
            brand: brandNode
              ? (brandNode.innerText || brandNode.textContent || '').trim()
              : '',
            text,
            dataPid:
              source.getAttribute('data-product-id') ||
              source.getAttribute('data-part-number') ||
              a.getAttribute('data-product-id') ||
              ''
          });
        }

        return out;
        """

    @classmethod
    def _extract_cards(cls, driver) -> list[dict]:
        script = cls._extract_cards_from_root_script()
        return driver.execute_script(script, None) or []

    @classmethod
    def _fetch_grid_cards(cls, driver, url: str) -> dict:
        driver.set_script_timeout(30)

        extract = cls._extract_cards_from_root_script()
        # The public grid URL is exposed by the site's own "Ver más productos"
        # button. Request it from the already-open category page using a normal
        # same-origin XHR-style fetch, matching the storefront interaction
        # more closely than a full browser navigation.
        script = r"""
        const url = arguments[0];
        const done = arguments[arguments.length - 1];

        fetch(url, {
          method: 'GET',
          credentials: 'same-origin',
          headers: {
            'Accept': 'text/html, */*; q=0.01',
            'X-Requested-With': 'XMLHttpRequest'
          }
        })
        .then(async response => {
          const html = await response.text();
          const doc = new DOMParser().parseFromString(html, 'text/html');

          const root = doc;
          const out = [];
          const seen = new Set();
          const productRe = /-\\d{5,14}\\.html(?:$|[?#])/i;
          const moneyRe = /\\$\\s*[0-9][0-9,]*(?:\\.\\d{1,2})?/;

          for (const a of Array.from(root.querySelectorAll('a[href*=".html"]'))) {
            let href = a.getAttribute('href') || '';
            try { href = new URL(href, location.origin).href; } catch (_) {}
            if (!productRe.test(href) || seen.has(href)) continue;

            let node = a;
            let card = null;
            for (let i = 0; i < 9 && node; i++, node = node.parentElement) {
              const text = (node.textContent || '').trim();
              if (moneyRe.test(text) && text.length >= 15 && text.length <= 3000) {
                card = node;
                if (/Agregar|Comparar|Favoritos/i.test(text)) break;
              }
            }

            const source = card || a.parentElement || a;
            const text = (source.textContent || '').trim();
            if (!moneyRe.test(text)) continue;

            const brandNode = source.querySelector('[class*="brand" i]');
            const titleNode = source.querySelector(
              'h2, h3, h4, [class*="name" i], [class*="title" i]'
            );

            let name = (
              a.textContent ||
              a.getAttribute('aria-label') ||
              a.getAttribute('title') ||
              ''
            ).trim();

            if (!name && titleNode) {
              name = (titleNode.textContent || '').trim();
            }

            if (!name) {
              const lines = text.split(/\\n+/).map(x => x.trim()).filter(Boolean);
              name = lines.find(x =>
                !moneyRe.test(x) &&
                !/Agregar|Comparar|Oferta|Favoritos/i.test(x) &&
                x.length > 8
              ) || '';
            }

            if (!name) continue;

            seen.add(href);
            out.push({
              href,
              name,
              brand: brandNode ? (brandNode.textContent || '').trim() : '',
              text,
              dataPid:
                source.getAttribute('data-product-id') ||
                source.getAttribute('data-part-number') ||
                a.getAttribute('data-product-id') ||
                ''
            });
          }

          done({
            ok: response.ok,
            status: response.status,
            final_url: response.url,
            content_type: response.headers.get('content-type') || '',
            html_length: html.length,
            cards: out,
            body_preview: (doc.body ? doc.body.textContent : html)
              .replace(/\\s+/g, ' ')
              .trim()
              .slice(0, 300)
          });
        })
        .catch(error => {
          done({
            ok: false,
            status: null,
            final_url: url,
            content_type: '',
            html_length: 0,
            cards: [],
            error: String(error)
          });
        });
        """
        result = driver.execute_async_script(script, url) or {}
        if not isinstance(result, dict):
            return {"ok": False, "cards": [], "error": "invalid_fetch_result"}
        return result

    def _collect_catalog(
        self,
        driver,
        category_url: str,
        target: int | None,
    ) -> tuple[list[dict], dict]:
        initial_cards = self._extract_cards(driver)
        all_cards: dict[str, dict] = {
            str(card.get("href")): card
            for card in initial_cards
            if card.get("href")
        }

        grid_url = self._grid_url(driver)
        stats = {
            "target_products": target,
            "initial_cards": len(all_cards),
            "grid_url": grid_url,
            "pages_loaded": 0,
            "requests": [],
            "final_cards": len(all_cards),
            "stop_reason": None,
        }

        if not grid_url:
            stats["stop_reason"] = "grid_url_not_found"
            return list(all_cards.values()), stats

        size = 20
        start = 20

        for _ in range(self.max_load_more):
            if target and len(all_cards) >= target:
                stats["stop_reason"] = "target_reached"
                break

            page_url = self._with_start(grid_url, start=start, size=size)
            result = self._fetch_grid_cards(driver, page_url)
            page_cards = result.get("cards") or []
            stats["pages_loaded"] += 1
            stats["requests"].append(
                {
                    "start": start,
                    "status": result.get("status"),
                    "ok": result.get("ok"),
                    "final_url": result.get("final_url"),
                    "content_type": result.get("content_type"),
                    "html_length": result.get("html_length"),
                    "cards": len(page_cards),
                    "body_preview": result.get("body_preview"),
                    "error": result.get("error"),
                }
            )

            before = len(all_cards)
            for card in page_cards:
                href = str(card.get("href") or "")
                if href:
                    all_cards[href] = card
            after = len(all_cards)

            print(
                f"Farmacias Guadalajara XHR start={start}: "
                f"status={result.get('status')}, "
                f"html={result.get('html_length')}, "
                f"page={len(page_cards)}, cumulative={after}"
            )

            if not result.get("ok"):
                stats["stop_reason"] = "grid_request_failed"
                break

            if after <= before:
                stats["stop_reason"] = "page_no_growth"
                break

            if len(page_cards) < size:
                stats["stop_reason"] = "last_partial_page"
                break

            start += size

        if stats["stop_reason"] is None:
            stats["stop_reason"] = "max_load_more_reached"

        stats["final_cards"] = len(all_cards)
        return list(all_cards.values()), stats

    @staticmethod
    def _write_diagnostics(
        driver,
        category: Category,
        meta: dict,
    ) -> None:
        DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
        slug = category.id

        (DIAGNOSTICS / f"farmacias_guadalajara_{slug}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (DIAGNOSTICS / f"farmacias_guadalajara_{slug}.html").write_text(
            driver.page_source,
            encoding="utf-8",
        )
        try:
            driver.save_screenshot(
                str(DIAGNOSTICS / f"farmacias_guadalajara_{slug}.png")
            )
        except Exception:
            pass

    def scrape_category(
        self,
        category: Category,
        location: Location,
    ) -> list[dict]:
        DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
        driver = self._build_driver(self.headless)

        try:
            self._goto(driver, category.url)
            self._assert_not_blocked(driver)

            target = self._target_count(driver)
            initial_cards = len(self._extract_cards(driver))
            grid_url = self._grid_url(driver)

            print(
                f"Farmacias Guadalajara [{category.id}]: "
                f"target={target}, initial_cards={initial_cards}"
            )
            print(f"Grid URL: {grid_url}")

            cards, pagination = self._collect_catalog(
                driver,
                category_url=category.url,
                target=target,
            )

            now = datetime.now().astimezone().isoformat(timespec="seconds")
            rows: list[dict] = []

            for card in cards:
                url = urljoin(BASE_URL, card.get("href") or "")
                sku = clean_text(card.get("dataPid")) or self.extract_sku(url)
                product = clean_text(card.get("name"))
                if not sku or not product:
                    continue

                current, regular, promotion = self._prices_from_text(
                    card.get("text")
                )
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
                        "store_context_method": "selenium_chrome_grid_pagination",
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
                "initial_cards": initial_cards,
                "grid_url": grid_url,
                "cards_collected": len(cards),
                "rows": len(rows),
                "pagination": pagination,
                "browser": "Google Chrome",
                "engine": "Selenium WebDriver",
                "strategy": "official data-url grid pagination",
            }
            self._write_diagnostics(driver, category, meta)

            if target and len(cards) < target:
                raise RuntimeError(
                    "Catálogo incompleto: "
                    f"target={target}, cards={len(cards)}, "
                    f"stop_reason={pagination.get('stop_reason')}"
                )

            if target and len(rows) < target:
                raise RuntimeError(
                    "Extracción incompleta: "
                    f"target={target}, rows={len(rows)}"
                )

            return rows
        finally:
            driver.quit()


__all__ = [
    "FarmaciasGuadalajaraBlocked",
    "FarmaciasGuadalajaraNetworkUnavailable",
    "FarmaciasGuadalajaraScraper",
]
