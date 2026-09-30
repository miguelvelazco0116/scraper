from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..config import Category, Location
from ..parsers import clean_text


BASE_URL = "https://www.farmaciasanpablo.com.mx/"
DIAGNOSTICS = Path("diagnostics")


class FarmaciasSanPabloBlocked(RuntimeError):
    pass


class FarmaciasSanPabloNetworkUnavailable(RuntimeError):
    pass


class FarmaciasSanPabloScraper:
    """Scraper de catálogo de Farmacias San Pablo con Chrome + Selenium.

    Se apoya en las tarjetas visibles del catálogo. No requiere enlaces /p/:
    primero busca identificadores data-product-* y, como respaldo, localiza
    la tarjeta contenedora de cada botón "Agregar".
    """

    MONEY_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)")
    CODE_RE = re.compile(r"(?<!\d)(\d{5,14})(?!\d)")
    BRAND_CANDIDATES = [
        "Afrin", "Arm & Hammer", "Bexident", "Colgate", "Corega", "Curaprox",
        "Dentaflox", "Durex", "Fluoxytil", "Fullsen", "GUM", "Lacer",
        "Listerine", "NeilMed", "Oral-B", "Parodontax", "Playboy", "Prudence",
        "Real Sea", "Rinomar", "Sensodyne", "Sico", "Sinomarin", "Stérimar",
        "Sterimar", "Trojan", "Vantal", "Xerolacer",
    ]

    def __init__(self, headless: bool = True, max_pages: int = 100) -> None:
        self.headless = headless
        self.max_pages = max_pages
        self.last_meta: dict = {}

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
    def _page_url(url: str, page_index: int) -> str:
        # SAP Commerce/Hybris normally uses currentPage as a zero-based index.
        # The base category URL is page 0; the second page is currentPage=1.
        if page_index <= 0:
            return url
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["currentPage"] = [str(page_index)]
        encoded = urlencode(
            [(key, value) for key, values in query.items() for value in values]
        )
        return urlunparse(parsed._replace(query=encoded))

    @staticmethod
    def _body_text(driver) -> str:
        try:
            return driver.find_element(By.TAG_NAME, "body").text or ""
        except Exception:
            return ""

    @staticmethod
    def _is_blocked(title: str | None, body: str | None) -> bool:
        blob = f"{title or ''}\n{body or ''}".casefold()
        markers = [
            "access denied",
            "you don't have permission to access",
            "request rejected",
            "verify you are human",
            "verifica que eres humano",
            "captcha",
        ]
        return any(marker in blob for marker in markers)

    @classmethod
    def _assert_not_blocked(cls, driver) -> None:
        title = driver.title or ""
        body = cls._body_text(driver)
        if cls._is_blocked(title, body):
            raise FarmaciasSanPabloBlocked(
                "Farmacias San Pablo presentó Access Denied o verificación"
            )

    @classmethod
    def _goto(cls, driver, url: str) -> None:
        try:
            driver.get(url)
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except (TimeoutException, WebDriverException) as exc:
            raise FarmaciasSanPabloNetworkUnavailable(
                f"No se pudo cargar {url}. {type(exc).__name__}: {exc}"
            ) from exc
        cls._assert_not_blocked(driver)

    @classmethod
    def _target_count(cls, driver) -> int | None:
        text = cls._body_text(driver)
        values: list[int] = []

        for pattern in (
            r"\((\d{1,5})\s+resultados?\)",
            r"\b\d+\s*-\s*\d+\s+de\s+(\d{1,5})\b",
            r"\b(\d{1,5})\s+resultados?\b",
        ):
            values.extend(
                int(match)
                for match in re.findall(pattern, text, flags=re.IGNORECASE)
            )

        return max(values) if values else None

    @classmethod
    def _parse_money_values(cls, text: str | None) -> list[float]:
        values: list[float] = []
        for raw in cls.MONEY_RE.findall(text or ""):
            try:
                value = float(raw.replace(",", ""))
            except ValueError:
                continue
            if value > 0:
                values.append(value)
        return values

    @classmethod
    def _prices_from_text(
        cls,
        text: str | None,
    ) -> tuple[float | None, float | None, str | None]:
        values = cls._parse_money_values(text)
        if not values:
            return None, None, None

        current = min(values)
        regular = max(values)

        promo_parts: list[str] = []
        discount = re.search(
            r"\b(\d{1,2})\s*%\s*(?:de\s*)?descuento\b",
            text or "",
            re.IGNORECASE,
        )
        if discount:
            promo_parts.append(f"{discount.group(1)}% de descuento")

        multi = re.search(
            r"\bacumula\s+\d+\s+y\s+el\s+\d+[^\n]{0,40}gratis\b",
            text or "",
            re.IGNORECASE,
        )
        if multi:
            promo_parts.append(clean_text(multi.group(0)) or multi.group(0))

        bundle = re.search(
            r"\b\d+\s*x\s*\d+\b",
            text or "",
            re.IGNORECASE,
        )
        if bundle:
            promo_parts.append(bundle.group(0))

        if current < regular:
            promo_parts.append("Precio promocional")

        promotion = " | ".join(dict.fromkeys(promo_parts)) if promo_parts else None
        return current, regular, promotion

    @classmethod
    def _infer_brand(cls, product: str | None, text: str | None = None) -> str | None:
        blob = clean_text(f"{product or ''} {text or ''}") or ""
        for brand in cls.BRAND_CANDIDATES:
            if re.search(rf"(?<!\w){re.escape(brand)}(?!\w)", blob, re.IGNORECASE):
                return "Stérimar" if brand.casefold() == "sterimar" else brand

        lines = [
            clean_text(line)
            for line in (text or "").splitlines()
            if clean_text(line)
        ]
        for line in lines[:4]:
            if (
                line
                and len(line) <= 35
                and "$" not in line
                and not re.search(r"descuento|gratis|agregar", line, re.IGNORECASE)
            ):
                return line
        return None

    @classmethod
    def _code_from_card(cls, card: dict) -> str | None:
        candidates = [
            clean_text(card.get("productCode")),
            clean_text(card.get("productId")),
            clean_text(card.get("code")),
            clean_text(card.get("sku")),
            clean_text(card.get("ean")),
            clean_text(card.get("upc")),
        ]
        for value in candidates:
            if value:
                match = cls.CODE_RE.search(value)
                if match:
                    return match.group(1)

        href = clean_text(card.get("href"))
        if href:
            matches = cls.CODE_RE.findall(href)
            if matches:
                return matches[-1]

        return None

    @staticmethod
    def _extract_cards(driver) -> list[dict]:
        script = r"""
        const normalize = value => String(value || '').replace(/\s+/g, ' ').trim();

        function priceCount(text) {
          const matches = normalize(text).match(/\$\s*[0-9][0-9,]*(?:\.\d{1,2})?/g);
          return matches ? matches.length : 0;
        }

        function findCard(node) {
          let current = node;
          let best = null;
          for (let i = 0; i < 10 && current; i++, current = current.parentElement) {
            const text = normalize(current.innerText || current.textContent);
            if (priceCount(text) >= 1 && text.length >= 20 && text.length <= 2500) {
              best = current;
              const addCount = Array.from(current.querySelectorAll('button, a'))
                .filter(x => /^(Agregar|Añadir)$/i.test(normalize(x.innerText || x.textContent)))
                .length;
              if (addCount <= 1) break;
            }
          }
          return best || node.parentElement || node;
        }

        function allData(root) {
          const keys = [
            'data-product-code', 'data-product-id', 'data-code', 'data-sku',
            'data-ean', 'data-upc'
          ];
          const result = {};
          const nodes = [root, ...Array.from(root.querySelectorAll('*')).slice(0, 250)];
          for (const el of nodes) {
            for (const key of keys) {
              const value = el.getAttribute && el.getAttribute(key);
              if (value && !result[key]) result[key] = value;
            }
          }
          return result;
        }

        const seeds = [
          ...Array.from(document.querySelectorAll(
            '[data-product-code], [data-product-id], [data-code], [data-sku], [data-ean], [data-upc]'
          )),
          ...Array.from(document.querySelectorAll('button, a')).filter(el =>
            /^(Agregar|Añadir)$/i.test(normalize(el.innerText || el.textContent))
          )
        ];

        const out = [];
        const seen = new Set();

        for (const seed of seeds) {
          const card = findCard(seed);
          const text = (card.innerText || card.textContent || '').trim();
          if (!text || priceCount(text) === 0) continue;

          const data = allData(card);
          const hrefs = Array.from(card.querySelectorAll('a[href]'))
            .map(a => a.href)
            .filter(Boolean);
          const href = hrefs.find(h =>
            !/javascript:|#$/i.test(h) &&
            !/\/c\//i.test(h)
          ) || hrefs[0] || '';

          const titleNode = card.querySelector(
            '[class*="product"][class*="name"], [class*="name"], ' +
            '[class*="title"], h2, h3, h4, h5'
          );
          const image = card.querySelector('img[alt]');

          let title = titleNode
            ? normalize(titleNode.innerText || titleNode.textContent)
            : '';
          if (!title && image) title = normalize(image.getAttribute('alt'));

          const lines = text.split(/\n+/).map(normalize).filter(Boolean);
          if (!title) {
            const cleanLines = lines.filter(line =>
              !/^\$/.test(line) &&
              !/MXN|Agregar|Añadir|Descuento|GRATIS/i.test(line) &&
              !/^\d+\s*(ML|G|GR|KG|PZ|PZS|TABLETAS?|CAPSULAS?)\b/i.test(line)
            );
            if (cleanLines.length >= 2 && cleanLines[0].length <= 35) {
              title = normalize(cleanLines[0] + ' ' + cleanLines[1]);
            } else {
              title = cleanLines[0] || '';
            }
          }

          const key = [
            data['data-product-code'] || '',
            data['data-product-id'] || '',
            data['data-code'] || '',
            data['data-sku'] || '',
            href,
            title,
            text.slice(0, 160)
          ].join('|');

          if (!key || seen.has(key)) continue;
          seen.add(key);

          out.push({
            productCode: data['data-product-code'] || '',
            productId: data['data-product-id'] || '',
            code: data['data-code'] || '',
            sku: data['data-sku'] || '',
            ean: data['data-ean'] || '',
            upc: data['data-upc'] || '',
            href,
            title,
            text
          });
        }

        return out;
        """
        return driver.execute_script(script) or []

    def _discover_cards(
        self,
        driver,
        category: Category,
    ) -> tuple[list[dict], dict]:
        all_cards: dict[str, dict] = {}
        target: int | None = None
        pages: list[dict] = []
        empty_rounds = 0

        for page_index in range(self.max_pages):
            page_number = page_index + 1
            url = self._page_url(category.url, page_index)
            self._goto(driver, url)

            page_target = self._target_count(driver)
            if page_target:
                target = max(target or 0, page_target)

            cards = self._extract_cards(driver)
            before = len(all_cards)

            for card in cards:
                code = self._code_from_card(card) or ""
                href = clean_text(card.get("href")) or ""
                title = clean_text(card.get("title")) or ""
                text = clean_text(card.get("text")) or ""
                key = code or href or f"{title}|{text[:180]}"
                if key:
                    all_cards[key] = card

            after = len(all_cards)
            pages.append(
                {
                    "page": page_number,
                    "url": url,
                    "cards_on_page": len(cards),
                    "cumulative_cards": after,
                }
            )
            print(
                f"San Pablo [{category.id}] page={page_number}: "
                f"cards={len(cards)}, cumulative={after}, target={target}"
            )

            if target and after >= target:
                break

            if after <= before:
                empty_rounds += 1
            else:
                empty_rounds = 0

            if empty_rounds >= 1:
                break

            # Most San Pablo category pages expose 48 products per page.
            if cards and len(cards) < 48 and (not target or after >= target):
                break

        meta = {
            "category_id": category.id,
            "category_url": category.url,
            "target_products": target,
            "cards_discovered": len(all_cards),
            "pages": pages,
        }
        return list(all_cards.values()), meta

    @staticmethod
    def _write_diagnostics(
        driver,
        category: Category,
        meta: dict,
    ) -> None:
        DIAGNOSTICS.mkdir(parents=True, exist_ok=True)

        (DIAGNOSTICS / f"farmacias_san_pablo_{category.id}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            driver.save_screenshot(
                str(DIAGNOSTICS / f"farmacias_san_pablo_{category.id}.png")
            )
        except Exception:
            pass

    def scrape_category(
        self,
        category: Category,
        location: Location,
    ) -> list[dict]:
        DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        driver = self._build_driver(self.headless)

        try:
            cards, meta = self._discover_cards(driver, category)
            rows: list[dict] = []

            for card in cards:
                text = clean_text(card.get("text"))
                product = clean_text(card.get("title"))
                if not text or not product:
                    continue

                current, regular, promotion = self._prices_from_text(text)
                if current is None:
                    continue

                sku = self._code_from_card(card)
                href = clean_text(card.get("href"))
                url = urljoin(BASE_URL, href) if href else None

                rows.append(
                    {
                        "scrape_timestamp": now,
                        "retailer": "Farmacias San Pablo",
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
                        "brand": self._infer_brand(product, text),
                        "product": product,
                        "price_current": current,
                        "price_regular": regular,
                        "promotion": promotion,
                        "pickup_available": None,
                        "store_context_verified": False,
                        "store_context_method": "selenium_chrome_category_card",
                        "url": url,
                        "price_raw": text,
                    }
                )

            # Deduplicate without inventing identifiers. Prefer SKU, then URL,
            # then a stable content key for rows where the site exposes neither.
            unique: dict[str, dict] = {}
            for row in rows:
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

            meta.update(
                {
                    "rows": len(rows),
                    "unique_skus": len(
                        {str(row["sku"]) for row in rows if row.get("sku")}
                    ),
                    "unique_urls": len(
                        {str(row["url"]) for row in rows if row.get("url")}
                    ),
                    "price_complete": sum(
                        row.get("price_current") is not None for row in rows
                    ),
                    "browser": "Google Chrome",
                    "engine": "Selenium WebDriver",
                    "status": "SUCCESS" if rows else "EMPTY",
                }
            )

            target = meta.get("target_products")
            if target and len(rows) < int(target):
                meta["status"] = "PARTIAL"

            self.last_meta = meta
            self._write_diagnostics(driver, category, meta)
            return rows
        finally:
            driver.quit()


__all__ = [
    "FarmaciasSanPabloBlocked",
    "FarmaciasSanPabloNetworkUnavailable",
    "FarmaciasSanPabloScraper",
]
