from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
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
    """Scraper limpio de Farmacias Guadalajara usando Google Chrome + Selenium.

    No usa Playwright, CDP, perfiles persistentes, OpenAI API ni técnicas de
    evasión. El navegador se abre con Selenium WebDriver estándar.
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
    def _target_count(cls, driver) -> int | None:
        text = cls._body_text(driver)
        matches = re.findall(
            r"\(?\b(\d{1,5})\s+productos?\b\)?",
            text,
            flags=re.IGNORECASE,
        )
        if not matches:
            return None
        return max(int(x) for x in matches)

    @staticmethod
    def _product_link_count(driver) -> int:
        script = r"""
        const re = /-\d{5,14}\.html(?:$|[?#])/i;
        const links = Array.from(document.querySelectorAll('a[href*=".html"]'))
          .map(a => a.href)
          .filter(h => re.test(h));
        return new Set(links).size;
        """
        try:
            return int(driver.execute_script(script) or 0)
        except Exception:
            return 0

    @staticmethod
    def _build_driver(headless: bool):
        options = webdriver.ChromeOptions()
        options.add_argument("--lang=es-MX")
        options.add_argument("--window-size=1440,1000")
        if headless:
            options.add_argument("--headless=new")

        # Selenium Manager resuelve ChromeDriver automáticamente.
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(60)
        return driver

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

    def _expand_all_products(self, driver, target: int | None) -> dict:
        previous = self._product_link_count(driver)
        stats = {
            "target_products": target,
            "initial_links": previous,
            "clicks": 0,
            "final_links": previous,
            "stop_reason": None,
        }

        for _ in range(self.max_load_more):
            self._assert_not_blocked(driver)

            if target and previous >= target:
                stats["stop_reason"] = "target_reached"
                break

            try:
                button = WebDriverWait(driver, 8).until(
                    EC.visibility_of_element_located(
                        (By.CSS_SELECTOR, "button.more[data-url]")
                    )
                )
            except TimeoutException:
                stats["stop_reason"] = "load_more_not_visible"
                break

            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});",
                    button,
                )
                WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable(
                        (By.CSS_SELECTOR, "button.more[data-url]")
                    )
                )
                button.click()
                stats["clicks"] += 1
            except (ElementClickInterceptedException, WebDriverException):
                stats["stop_reason"] = "load_more_click_failed"
                break

            try:
                WebDriverWait(driver, 20).until(
                    lambda d: self._product_link_count(d) > previous
                )
            except TimeoutException:
                stats["stop_reason"] = "load_more_no_growth"
                break

            current = self._product_link_count(driver)
            print(f"Farmacias Guadalajara: {previous} -> {current}")
            previous = current
            stats["final_links"] = current

        if stats["stop_reason"] is None:
            stats["stop_reason"] = "max_load_more_reached"
        stats["final_links"] = self._product_link_count(driver)
        return stats

    @staticmethod
    def _extract_cards(driver) -> list[dict]:
        script = r"""
        const out = [];
        const seen = new Set();
        const productRe = /-\d{5,14}\.html(?:$|[?#])/i;
        const moneyRe = /\$\s*[0-9][0-9,]*(?:\.\d{1,2})?/;

        for (const a of Array.from(document.querySelectorAll('a[href*=".html"]'))) {
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

          const brandNode = source.querySelector('[class*="brand" i]');
          const titleNode = source.querySelector(
            'h2, h3, h4, [class*="name" i], [class*="title" i]'
          );

          let name = (
            a.innerText ||
            a.getAttribute('aria-label') ||
            a.getAttribute('title') ||
            ''
          ).trim();

          if (!name && titleNode) {
            name = (titleNode.innerText || '').trim();
          }

          if (!name) {
            const lines = text.split(/\n+/).map(x => x.trim()).filter(Boolean);
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
            brand: brandNode ? (brandNode.innerText || '').trim() : '',
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
        return driver.execute_script(script) or []

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
            initial_links = self._product_link_count(driver)
            print(
                f"Farmacias Guadalajara [{category.id}]: "
                f"target={target}, initial_links={initial_links}"
            )

            expansion = self._expand_all_products(driver, target)
            final_links = self._product_link_count(driver)
            cards = self._extract_cards(driver)

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
                        "store_context_method": "selenium_chrome_online_catalog",
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
                "initial_links": initial_links,
                "product_links": final_links,
                "rows": len(rows),
                "expansion": expansion,
                "browser": "Google Chrome",
                "engine": "Selenium WebDriver",
            }
            self._write_diagnostics(driver, category, meta)

            if target and final_links < target:
                raise RuntimeError(
                    "Catálogo incompleto: "
                    f"target={target}, links={final_links}, "
                    f"stop_reason={expansion.get('stop_reason')}"
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
