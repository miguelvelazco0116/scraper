from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse, urlunparse

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

    @staticmethod
    def _sku_from_url(url: str | None) -> str | None:
        """Extrae el SKU de una URL de producto /p/<codigo>.

        San Pablo publica algunos códigos con padding de ceros. Para el
        concentrado se conserva la parte significativa, por ejemplo
        /p/000000000000700142 -> 700142.
        """
        href = clean_text(url)
        if not href:
            return None

        path = urlparse(href).path
        match = re.search(r"/p/(\d+)(?:/|$)", path, flags=re.IGNORECASE)
        if not match:
            return None

        raw = match.group(1)
        normalized = raw.lstrip("0")
        return normalized or "0"

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
            sku_from_url = cls._sku_from_url(href)
            if sku_from_url:
                return sku_from_url

            matches = cls.CODE_RE.findall(href)
            if matches:
                return matches[-1]

        return None

    @staticmethod
    def _extract_cards(driver) -> list[dict]:
        script = r"""
        const normalize = value => String(value || '').replace(/\s+/g, ' ').trim();
        const moneyRe = /\$\s*[0-9][0-9,]*(?:\.\d{1,2})?/;

        function moneyCount(text) {
          const matches = normalize(text).match(new RegExp(moneyRe.source, 'g'));
          return matches ? matches.length : 0;
        }

        function addCount(root) {
          return Array.from(root.querySelectorAll('button, a, [role="button"]'))
            .filter(el => /Agregar|Añadir/i.test(normalize(el.innerText || el.textContent)))
            .length;
        }

        function findCard(seed) {
          let node = seed;
          let best = null;

          for (let i = 0; i < 12 && node; i++, node = node.parentElement) {
            const text = normalize(node.innerText || node.textContent);
            if (!text || !moneyRe.test(text)) continue;
            if (text.length < 20 || text.length > 2600) continue;

            const adds = addCount(node);
            const images = node.querySelectorAll('img').length;

            // Prefer the smallest ancestor that looks like exactly one product.
            if (adds === 1 || (adds === 0 && images <= 3)) {
              best = node;
              break;
            }

            if (!best) best = node;
          }

          return best || seed.parentElement || seed;
        }

        function readData(root) {
          const keys = [
            'data-product-code', 'data-product-id', 'data-code', 'data-sku',
            'data-ean', 'data-upc', 'data-id', 'data-item-id'
          ];
          const result = {};
          const nodes = [root, ...Array.from(root.querySelectorAll('*')).slice(0, 350)];
          for (const el of nodes) {
            if (!el || !el.getAttribute) continue;
            for (const key of keys) {
              const value = el.getAttribute(key);
              if (value && !result[key]) result[key] = value;
            }
          }
          return result;
        }

        const seeds = [];

        // Explicit product metadata.
        seeds.push(...Array.from(document.querySelectorAll(
          '[data-product-code], [data-product-id], [data-code], [data-sku], ' +
          '[data-ean], [data-upc], [data-item-id]'
        )));

        // Add-to-cart controls. Do not require exact button text.
        seeds.push(...Array.from(document.querySelectorAll(
          'button, a, [role="button"]'
        )).filter(el =>
          /Agregar|Añadir/i.test(normalize(el.innerText || el.textContent))
        ));

        // Price-bearing leaf nodes catch cards that expose no useful data attributes.
        const all = Array.from(document.querySelectorAll('body *'));
        seeds.push(...all.filter(el => {
          const text = normalize(el.innerText || el.textContent);
          if (!text || text.length > 180 || !moneyRe.test(text)) return false;
          const childHasMoney = Array.from(el.children || []).some(child =>
            moneyRe.test(normalize(child.innerText || child.textContent))
          );
          return !childHasMoney;
        }));

        // Product images are another stable anchor on this storefront.
        seeds.push(...Array.from(document.querySelectorAll(
          'img[alt][src], img[title][src]'
        )).filter(img => {
          const label = normalize(img.getAttribute('alt') || img.getAttribute('title'));
          return label.length >= 6;
        }));

        const out = [];
        const seen = new Set();

        for (const seed of seeds) {
          const card = findCard(seed);
          if (!card) continue;

          const rawText = (card.innerText || card.textContent || '').trim();
          const text = normalize(rawText);
          if (!text || !moneyRe.test(text)) continue;

          const data = readData(card);

          const hrefs = Array.from(card.querySelectorAll('a[href]'))
            .map(a => a.href)
            .filter(Boolean);

          const href = hrefs.find(h =>
            !/javascript:|#$/i.test(h) &&
            !/\/c\//i.test(h) &&
            !/login|registro|carrito|sucursales|facturacion/i.test(h)
          ) || '';

          const titleSelectors = [
            '[class*="product"][class*="name"]',
            '[class*="product"][class*="title"]',
            '[class*="name"]',
            '[class*="title"]',
            'h2', 'h3', 'h4', 'h5'
          ];

          let title = '';
          for (const selector of titleSelectors) {
            const el = card.querySelector(selector);
            if (!el) continue;
            const candidate = normalize(el.innerText || el.textContent);
            if (
              candidate &&
              candidate.length >= 5 &&
              candidate.length <= 220 &&
              !moneyRe.test(candidate) &&
              !/Agregar|Añadir|Descuento|GRATIS/i.test(candidate)
            ) {
              title = candidate;
              break;
            }
          }

          if (!title) {
            const img = card.querySelector('img[alt], img[title]');
            if (img) {
              const candidate = normalize(
                img.getAttribute('alt') || img.getAttribute('title')
              );
              if (candidate.length >= 5 && candidate.length <= 220) {
                title = candidate;
              }
            }
          }

          if (!title) {
            const lines = rawText.split(/\n+/).map(normalize).filter(Boolean);
            const candidates = lines.filter(line =>
              line.length >= 5 &&
              line.length <= 220 &&
              !moneyRe.test(line) &&
              !/MXN|Agregar|Añadir|Descuento|GRATIS|Ordenar por|Artículos por página/i.test(line) &&
              !/^\d+\s*(ML|G|GR|KG|PZ|PZS|TABLETAS?|CAPSULAS?)\b/i.test(line)
            );

            // Brand + product name are commonly consecutive lines.
            if (
              candidates.length >= 2 &&
              candidates[0].length <= 40 &&
              candidates[1].length > 8
            ) {
              title = normalize(candidates[0] + ' ' + candidates[1]);
            } else {
              title = candidates[0] || '';
            }
          }

          if (!title) continue;

          const key = [
            data['data-product-code'] || '',
            data['data-product-id'] || '',
            data['data-code'] || '',
            data['data-sku'] || '',
            data['data-ean'] || '',
            data['data-upc'] || '',
            href,
            title,
            text.slice(0, 220)
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
            text: rawText
          });
        }

        return out;
        """
        return driver.execute_script(script) or []

    @classmethod
    def _collect_page_cards(
        cls,
        driver,
        expected_on_page: int | None,
    ) -> tuple[list[dict], dict]:
        """Recorre visualmente una página y acumula tarjetas aunque el DOM sea lazy/virtual."""

        collected: dict[str, dict] = {}
        samples: list[dict] = []

        try:
            driver.execute_script("window.scrollTo(0, 0);")
        except Exception:
            pass
        time.sleep(0.6)

        stable_bottom_rounds = 0
        previous_total = 0

        for step in range(90):
            cards = cls._extract_cards(driver)

            for card in cards:
                code = cls._code_from_card(card) or ""
                href = clean_text(card.get("href")) or ""
                title = clean_text(card.get("title")) or ""
                text = clean_text(card.get("text")) or ""
                key = code or href or f"{title}|{text[:180]}"
                if key:
                    collected[key] = card

            total = len(collected)
            if step == 0 or step % 5 == 0 or total != previous_total:
                samples.append(
                    {
                        "step": step,
                        "visible_cards": len(cards),
                        "cumulative_cards": total,
                    }
                )

            if expected_on_page and total >= expected_on_page:
                break

            state = driver.execute_script(
                """
                const y = window.scrollY || document.documentElement.scrollTop || 0;
                const h = Math.max(
                  document.body.scrollHeight,
                  document.documentElement.scrollHeight
                );
                const viewport = window.innerHeight || document.documentElement.clientHeight;
                window.scrollBy(0, Math.max(550, Math.floor(viewport * 0.72)));
                return {y, h, viewport};
                """
            ) or {}

            time.sleep(0.35)

            new_state = driver.execute_script(
                """
                return {
                  y: window.scrollY || document.documentElement.scrollTop || 0,
                  h: Math.max(
                    document.body.scrollHeight,
                    document.documentElement.scrollHeight
                  ),
                  viewport: window.innerHeight || document.documentElement.clientHeight
                };
                """
            ) or {}

            at_bottom = (
                int(new_state.get("y") or 0)
                + int(new_state.get("viewport") or 0)
                >= int(new_state.get("h") or 0) - 25
            )

            if at_bottom:
                if total <= previous_total:
                    stable_bottom_rounds += 1
                else:
                    stable_bottom_rounds = 0

                # Trigger any final lazy-loading observer at page bottom.
                try:
                    driver.execute_script(
                        "window.scrollTo(0, document.body.scrollHeight);"
                    )
                except Exception:
                    pass
                time.sleep(0.5)

                if stable_bottom_rounds >= 3:
                    break
            else:
                stable_bottom_rounds = 0

            previous_total = total

        try:
            driver.execute_script("window.scrollTo(0, 0);")
        except Exception:
            pass

        return list(collected.values()), {
            "expected_on_page": expected_on_page,
            "cards_collected": len(collected),
            "scroll_samples": samples,
        }


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

            expected_on_page = None
            if target:
                remaining = max(int(target) - (page_index * 48), 0)
                expected_on_page = min(48, remaining) if remaining else 0

            cards, hydration = self._collect_page_cards(
                driver,
                expected_on_page=expected_on_page,
            )
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
                    "expected_on_page": expected_on_page,
                    "cards_on_page": len(cards),
                    "cumulative_cards": after,
                    "hydration": hydration,
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

            # If target is unknown, stop when this page yielded fewer than
            # the catalog page size and there is no evidence of another page.
            if not target and cards and len(cards) < 48:
                try:
                    next_exists = bool(
                        driver.execute_script(
                            """
                            return Array.from(document.querySelectorAll('a, button'))
                              .some(el => /siguiente|next/i.test(
                                (el.innerText || el.getAttribute('aria-label') || '').trim()
                              ) && !el.disabled);
                            """
                        )
                    )
                except Exception:
                    next_exists = False
                if not next_exists:
                    break

        search_fallback_url = None

        # Some legacy/category URLs on San Pablo resolve without a usable
        # catalog (currently seen with preservativos). In that case use the
        # storefront's normal public search page for the same category term.
        if not all_cards and target is None:
            query = (
                clean_text(category.subcategory)
                or clean_text(category.name)
                or clean_text(category.id)
                or ""
            )
            if query:
                search_fallback_url = urljoin(
                    BASE_URL,
                    "search/" + quote(query, safe=""),
                )
                print(
                    f"San Pablo [{category.id}] category empty; "
                    f"trying public search: {search_fallback_url}"
                )
                self._goto(driver, search_fallback_url)

                search_target = self._target_count(driver)
                if search_target:
                    target = search_target

                expected = min(48, int(target)) if target else None
                search_cards, hydration = self._collect_page_cards(
                    driver,
                    expected_on_page=expected,
                )

                for card in search_cards:
                    code = self._code_from_card(card) or ""
                    href = clean_text(card.get("href")) or ""
                    title = clean_text(card.get("title")) or ""
                    text = clean_text(card.get("text")) or ""
                    key = code or href or f"{title}|{text[:180]}"
                    if key:
                        all_cards[key] = card

                pages.append(
                    {
                        "page": "search-fallback",
                        "url": search_fallback_url,
                        "expected_on_page": expected,
                        "cards_on_page": len(search_cards),
                        "cumulative_cards": len(all_cards),
                        "hydration": hydration,
                    }
                )

        meta = {
            "category_id": category.id,
            "category_url": category.url,
            "search_fallback_url": search_fallback_url,
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

                # Keep the product even when the card has no visible price.
                # Catalog completeness and price completeness are measured
                # separately in the output summary.
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
