from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from ..availability import UNAVAILABLE, availability_fields
from ..config import Category, Location
from ..parsers import clean_text, extract_sku


BASE_URL = "https://despensa.bodegaaurrera.com.mx/"
DIAGNOSTICS = Path("diagnostics")

BLOCK_MARKERS = (
    "access denied",
    "robot or human",
    "captcha",
    "reference #18",
    "forbidden",
    "verifica tu identidad",
    "mantén presionado",
    "manten presionado",
    "confirma que no eres un robot",
)


class BodegaAurreraBlocked(RuntimeError):
    pass


class BodegaAurreraNetworkUnavailable(RuntimeError):
    pass


class BodegaAurreraScraper:
    """Scraper inicial del catálogo público de Bodega Aurrera.

    Bodega Aurrera comparte infraestructura con Walmart y puede presentar
    desafíos de identidad. Este scraper no resuelve CAPTCHAs ni intenta evadir
    controles anti-bot. En navegador visible únicamente espera a que el usuario
    complete manualmente una verificación, si el sitio la presenta.
    """

    MONEY_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)")
    CURRENT_RE = re.compile(
        r"precio\s+actual\s*\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
        re.IGNORECASE,
    )
    BEFORE_RE = re.compile(
        r"Antes\s*\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
        re.IGNORECASE,
    )

    BRAND_CANDIDATES = (
        "Colgate",
        "Oral-B",
        "Oral B",
        "Listerine",
        "Sensodyne",
        "Crest",
        "Equate",
        "GUM",
        "Corega",
        "Aquafresh",
        "Parodontax",
        "Curaprox",
        "Philips",
        "Reach",
        "Pro",
    )

    def __init__(
        self,
        headless: bool = False,
        browser_channel: str | None = "chrome",
        max_pages: int = 30,
        wait_ms: int = 1200,
        manual_verification_timeout_ms: int = 180_000,
    ) -> None:
        self.headless = headless
        self.browser_channel = browser_channel
        self.max_pages = max_pages
        self.wait_ms = wait_ms
        self.manual_verification_timeout_ms = manual_verification_timeout_ms
        self.run_meta: dict[str, Any] = {}

    @staticmethod
    def _paged_url(url: str, page_number: int) -> str:
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if page_number <= 1:
            query.pop("page", None)
        else:
            query["page"] = str(page_number)
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    @staticmethod
    def _normalize(value: str | None) -> str:
        if not value:
            return ""
        normalized = value.casefold()
        normalized = (
            normalized.replace("á", "a")
            .replace("é", "e")
            .replace("í", "i")
            .replace("ó", "o")
            .replace("ú", "u")
            .replace("ü", "u")
        )
        return re.sub(r"\s+", " ", normalized).strip()

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
                path=str(DIAGNOSTICS / f"bodega_aurrera_{safe}.png"),
                full_page=True,
            )
        except Exception:
            pass

        try:
            (DIAGNOSTICS / f"bodega_aurrera_{safe}.html").write_text(
                page.content(),
                encoding="utf-8",
            )
        except Exception:
            pass

        try:
            (DIAGNOSTICS / f"bodega_aurrera_{safe}.json").write_text(
                json.dumps(self.run_meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _is_blocked(self, page, status: int | None = None) -> bool:
        body = self._normalize(self._body_text(page))
        blocked_url = "/blocked" in (page.url or "").casefold()
        return bool(
            status in (401, 403, 429)
            or blocked_url
            or any(self._normalize(marker) in body for marker in BLOCK_MARKERS)
        )

    def _assert_not_blocked(self, page, status: int | None = None) -> None:
        if not self._is_blocked(page, status):
            return

        self.run_meta["blocked_detected"] = True
        self.run_meta["blocked_status"] = status
        self.run_meta["blocked_url"] = page.url
        self._save_diagnostics(page, "blocked")

        if not self.headless:
            self.run_meta["manual_verification_required"] = True
            self.run_meta["manual_verification_resolved"] = False
            print(
                "MANUAL_VERIFICATION_REQUIRED: Bodega Aurrera mostró una "
                "verificación de identidad. Complétala manualmente en Chrome; "
                "el scraper esperará hasta 3 minutos.",
                flush=True,
            )

            deadline = time.monotonic() + (
                self.manual_verification_timeout_ms / 1000
            )
            while time.monotonic() < deadline:
                page.wait_for_timeout(1_000)
                if not self._is_blocked(page):
                    self.run_meta["manual_verification_resolved"] = True
                    self._save_diagnostics(
                        page,
                        "manual_verification_resolved",
                    )
                    print(
                        "MANUAL_VERIFICATION_RESOLVED: continuando.",
                        flush=True,
                    )
                    return

        raise BodegaAurreraBlocked(
            "Bodega Aurrera bloqueó o desafió la sesión automatizada. "
            "Se guardaron diagnósticos; no se intenta evadir la protección."
        )

    def _goto(self, page, url: str) -> None:
        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=120_000,
            )
        except PlaywrightError as exc:
            raise BodegaAurreraNetworkUnavailable(str(exc)) from exc

        status = response.status if response else None
        page.wait_for_timeout(self.wait_ms)
        self._assert_not_blocked(page, status)

    @staticmethod
    def _raw_cards(page) -> list[dict]:
        return page.locator("body").evaluate(
            r"""
            () => {
              const normalize = value =>
                String(value || '').replace(/\s+/g, ' ').trim();

              const out = [];
              const seen = new Set();
              const unavailable = /Agotado|No disponible|Sin existencia|Sin stock|Out of stock/i;

              for (const a of Array.from(
                document.querySelectorAll('a[href*="/ip/"]')
              )) {
                const href = a.href || '';
                if (!href || seen.has(href)) continue;

                let card = a;
                let found = null;

                for (let i = 0; i < 12 && card; i++, card = card.parentElement) {
                  const text = normalize(card.innerText || card.textContent);
                  if (
                    text &&
                    (/\$\s*[0-9][0-9,]*(?:\.\d{1,2})?/.test(text) || unavailable.test(text)) &&
                    text.length >= 10 &&
                    text.length <= 3500
                  ) {
                    const productLinks = card.querySelectorAll(
                      'a[href*="/ip/"]'
                    ).length;

                    if (productLinks <= 3) {
                      found = card;
                      break;
                    }
                  }
                }

                if (!found) continue;

                const rawText = (found.innerText || found.textContent || '').trim();

                const heading = found.querySelector(
                  'h1,h2,h3,h4,h5,[class*="name"],[class*="title"]'
                );
                const image = found.querySelector('img[alt]');
                const brandNode = found.querySelector(
                  '[data-automation-id*="brand"], [class*="brand"]'
                );

                const title =
                  normalize(a.getAttribute('aria-label')) ||
                  normalize(a.getAttribute('title')) ||
                  normalize(a.innerText) ||
                  normalize(heading ? heading.innerText : '') ||
                  normalize(image ? image.alt : '');

                const brand = normalize(
                  brandNode ? brandNode.innerText || brandNode.textContent : ''
                );

                seen.add(href);
                out.push({
                  href,
                  title,
                  brand,
                  text: rawText,
                });
              }

              return out;
            }
            """
        )

    @classmethod
    def _prices(
        cls,
        text: str | None,
    ) -> tuple[float | None, float | None]:
        blob = text or ""

        current_match = cls.CURRENT_RE.search(blob)
        before_match = cls.BEFORE_RE.search(blob)

        current = None
        regular = None

        if current_match:
            try:
                current = float(current_match.group(1).replace(",", ""))
            except ValueError:
                current = None

        if before_match:
            try:
                regular = float(before_match.group(1).replace(",", ""))
            except ValueError:
                regular = None

        if current is None:
            values: list[float] = []
            for raw in cls.MONEY_RE.findall(blob):
                try:
                    value = float(raw.replace(",", ""))
                except ValueError:
                    continue
                if value > 0 and value not in values:
                    values.append(value)

            if values:
                current = values[0]
                if regular is None:
                    regular = max(values)

        if current is not None and regular is None:
            regular = current

        return current, regular

    @classmethod
    def _infer_brand(
        cls,
        product: str | None,
        explicit: str | None = None,
    ) -> str | None:
        if explicit:
            return clean_text(explicit)

        if not product:
            return None

        for brand in cls.BRAND_CANDIDATES:
            if re.search(
                rf"(?<!\w){re.escape(brand)}(?!\w)",
                product,
                re.IGNORECASE,
            ):
                return brand

        return None

    @staticmethod
    def _promotion(text: str | None) -> str | None:
        blob = text or ""
        promos: list[str] = []

        for pattern in (
            r"Rebaja",
            r"Precio en línea",
            r"Ahorra\s*\$\s*[0-9][0-9,]*(?:\.\d{1,2})?",
            r"Combina\s+\d+\s*x\s*\$\s*[0-9][0-9,]*(?:\.\d{1,2})?",
            r"\d+\s*x\s*\$\s*[0-9][0-9,]*(?:\.\d{1,2})?",
        ):
            match = re.search(pattern, blob, flags=re.IGNORECASE)
            if match:
                promos.append(match.group(0))

        return clean_text(" | ".join(dict.fromkeys(promos)))

    def _extract_rows(
        self,
        page,
        category: Category,
        location: Location,
    ) -> list[dict]:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        rows: list[dict] = []
        raw_cards = self._raw_cards(page)

        rejected_missing_sku = 0
        rejected_missing_price = 0

        for card in raw_cards:
            href = clean_text(card.get("href"))
            product = clean_text(card.get("title"))
            text = clean_text(card.get("text")) or ""

            if not href or not product:
                continue

            sku = extract_sku(href)
            if not sku:
                rejected_missing_sku += 1
                continue

            current, regular = self._prices(text)
            availability = availability_fields(text=text)
            if current is None and availability["availability_status"] != UNAVAILABLE:
                rejected_missing_price += 1
                continue

            rows.append(
                {
                    "scrape_timestamp": now,
                    "retailer": "Bodega Aurrera",
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
                    "brand": self._infer_brand(
                        product,
                        clean_text(card.get("brand")),
                    ),
                    "product": product,
                    "price_current": current,
                    "price_regular": regular,
                    "promotion": self._promotion(text),
                    **availability,
                    "pickup_available": None,
                    "store_context_verified": False,
                    "store_context_method": (
                        "bodega_public_catalog_no_store_context"
                    ),
                    "url": urljoin(BASE_URL, href),
                    "price_raw": text,
                }
            )

        self.run_meta.setdefault("card_extraction", []).append(
            {
                "page": page.url,
                "raw_cards": len(raw_cards),
                "rows": len(rows),
                "rejected_missing_sku": rejected_missing_sku,
                "rejected_missing_price": rejected_missing_price,
            }
        )
        return rows

    @staticmethod
    def _product_link_count(page) -> int:
        try:
            return page.locator('a[href*="/ip/"]').count()
        except Exception:
            return 0

    def _expand_current_page(
        self,
        page,
        *,
        source_url: str,
        max_rounds: int = 30,
    ) -> dict:
        """Expand lazy-loaded content until the visible product set stabilizes."""
        rounds: list[dict] = []
        stale_rounds = 0
        previous = self._product_link_count(page)

        for round_number in range(1, max_rounds + 1):
            clicked = False
            for label in (
                "Ver más",
                "Ver mas",
                "Mostrar más",
                "Mostrar mas",
                "Cargar más",
                "Cargar mas",
            ):
                try:
                    button = page.get_by_text(label, exact=False).first
                    if button.count() and button.is_visible():
                        button.click(timeout=2_000)
                        clicked = True
                        page.wait_for_timeout(500)
                        break
                except Exception:
                    continue

            try:
                page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
            except Exception:
                pass
            page.wait_for_timeout(max(self.wait_ms, 900))

            current = self._product_link_count(page)
            rounds.append(
                {
                    "round": round_number,
                    "product_links": current,
                    "clicked_load_more": clicked,
                }
            )

            if current > previous:
                stale_rounds = 0
            else:
                stale_rounds += 1

            previous = max(previous, current)
            if stale_rounds >= 3:
                break

        return {
            "source_url": source_url,
            "product_links": previous,
            "scroll_rounds": len(rounds),
            "stabilized": stale_rounds >= 3,
            "rounds": rounds,
        }

    @staticmethod
    def _explicit_page_urls(page) -> list[str]:
        try:
            raw = page.locator('a[href*="page="]').evaluate_all(
                r"""
                anchors => Array.from(
                  new Set(
                    anchors
                      .map(a => a.href || '')
                      .filter(Boolean)
                  )
                )
                """
            )
        except Exception:
            return []

        urls: list[str] = []
        for href in raw:
            try:
                query = dict(
                    parse_qsl(
                        urlsplit(href).query,
                        keep_blank_values=True,
                    )
                )
                page_number = int(query.get("page", "0"))
            except (TypeError, ValueError):
                continue
            if page_number > 1 and href not in urls:
                urls.append(href)

        return sorted(
            urls,
            key=lambda href: int(
                dict(
                    parse_qsl(
                        urlsplit(href).query,
                        keep_blank_values=True,
                    )
                ).get("page", "0")
            ),
        )

    @staticmethod
    def _content_child_urls(page, category: Category) -> list[str]:
        """Discover direct content subsections from a parent content category."""
        parent = urlsplit(category.url)
        parent_path = parent.path.rstrip("/")
        if "/content/" not in parent_path:
            return []

        # Parent URLs end in the category id, e.g.
        # /content/cuidado-de-la-ropa/3680083
        # while child sections are siblings below the slug, e.g.
        # /content/cuidado-de-la-ropa/detergente/3680083_3570057
        parts = [part for part in parent_path.split("/") if part]
        if len(parts) < 3:
            return []
        prefix = "/" + "/".join(parts[:-1]) + "/"

        try:
            hrefs = page.locator("a[href]").evaluate_all(
                "anchors => anchors.map(a => a.href || '').filter(Boolean)"
            )
        except Exception:
            return []

        children: list[str] = []
        for href in hrefs:
            try:
                parts = urlsplit(href)
            except Exception:
                continue

            if parts.netloc != parent.netloc:
                continue

            child_path = parts.path.rstrip("/")
            if not child_path.startswith(prefix):
                continue
            if child_path == parent_path:
                continue

            # Direct content subsections have one extra slug plus their id.
            # Keep them as catalogue sources and deduplicate later by SKU.
            clean_url = urlunsplit(
                (parts.scheme, parts.netloc, parts.path, "", "")
            )
            if clean_url not in children:
                children.append(clean_url)

        return children

    def _collect_source(
        self,
        page,
        source_url: str,
        category: Category,
        location: Location,
        seen_skus: set[str],
    ) -> tuple[list[dict], dict]:
        self._goto(page, source_url)

        try:
            page.wait_for_selector(
                'a[href*="/ip/"]',
                timeout=25_000,
            )
        except PlaywrightTimeoutError:
            source_meta = {
                "source_url": source_url,
                "actual_url": page.url,
                "rows": 0,
                "new_rows": 0,
                "product_links": 0,
                "explicit_pages": 0,
                "stabilized": False,
                "no_product_links": True,
            }
            self._save_diagnostics(
                page,
                "no_products_" + str(len(self.run_meta.get("sources", [])) + 1),
            )
            return [], source_meta

        expansion = self._expand_current_page(
            page,
            source_url=source_url,
        )
        explicit_pages = self._explicit_page_urls(page)

        page_rows = self._extract_rows(
            page,
            category,
            location,
        )

        all_rows = list(page_rows)
        explicit_page_meta: list[dict] = []
        visited_page_urls = {page.url}

        for page_url in explicit_pages:
            if page_url in visited_page_urls:
                continue
            visited_page_urls.add(page_url)

            self._goto(page, page_url)
            page_expansion = self._expand_current_page(
                page,
                source_url=page_url,
            )
            extra_rows = self._extract_rows(
                page,
                category,
                location,
            )
            all_rows.extend(extra_rows)
            explicit_page_meta.append(
                {
                    "requested_url": page_url,
                    "actual_url": page.url,
                    "rows": len(extra_rows),
                    "product_links": page_expansion.get("product_links"),
                    "scroll_rounds": page_expansion.get("scroll_rounds"),
                    "stabilized": page_expansion.get("stabilized"),
                }
            )

        unique_source: dict[str, dict] = {}
        for row in all_rows:
            sku = clean_text(str(row.get("sku") or ""))
            if sku:
                unique_source[sku] = row

        new_rows = [
            row
            for sku, row in unique_source.items()
            if sku not in seen_skus
        ]
        for row in new_rows:
            seen_skus.add(str(row.get("sku")))

        source_meta = {
            "source_url": source_url,
            "actual_url": page.url,
            "rows": len(unique_source),
            "new_rows": len(new_rows),
            "product_links": expansion.get("product_links"),
            "scroll_rounds": expansion.get("scroll_rounds"),
            "stabilized": expansion.get("stabilized"),
            "explicit_pages": len(explicit_pages),
            "explicit_pages_stabilized": all(
                bool(item.get("stabilized"))
                for item in explicit_page_meta
            ),
            "explicit_page_meta": explicit_page_meta,
        }
        return new_rows, source_meta

    def scrape_category_on_page(
        self,
        page,
        category: Category,
        location: Location,
        *,
        navigate_to_category: bool = True,
    ) -> list[dict]:
        """Extrae una categoría usando una pestaña/sesión ya existente."""
        self.run_meta = {
            "retailer": "Bodega Aurrera",
            "category_id": category.id,
            "category_url": category.url,
            "blocked_detected": False,
            "manual_verification_required": False,
            "manual_verification_resolved": False,
            "sources": [],
            "attached_session": True,
        }

        if navigate_to_category:
            self._goto(page, category.url)
        else:
            self._assert_not_blocked(page)

        child_urls = self._content_child_urls(page, category)
        sources = [category.url] + [
            url for url in child_urls if url != category.url
        ]

        self.run_meta["content_child_urls"] = child_urls
        self.run_meta["sources_discovered"] = len(sources)

        rows: list[dict] = []
        seen_skus: set[str] = set()

        for source_index, source_url in enumerate(sources, start=1):
            source_rows, source_meta = self._collect_source(
                page,
                source_url,
                category,
                location,
                seen_skus,
            )
            rows.extend(source_rows)
            source_meta["source_index"] = source_index
            self.run_meta["sources"].append(source_meta)

            print(
                f"Bodega Aurrera source={source_index}/{len(sources)}: "
                f"rows={source_meta.get('rows')}, "
                f"new={source_meta.get('new_rows')}, "
                f"cumulative={len(rows)}, "
                f"explicit_pages={source_meta.get('explicit_pages')}, "
                f"stabilized={source_meta.get('stabilized')}",
                flush=True,
            )

        self.run_meta["unique_products"] = len(rows)
        self.run_meta["sku_complete"] = sum(
            bool(row.get("sku")) for row in rows
        )
        self.run_meta["price_complete"] = sum(
            row.get("price_current") is not None for row in rows
        )
        self.run_meta["url_complete"] = sum(
            bool(row.get("url")) for row in rows
        )

        stabilized = all(
            bool(item.get("stabilized"))
            and bool(item.get("explicit_pages_stabilized", True))
            for item in self.run_meta.get("sources", [])
            if not item.get("no_product_links")
        )
        self.run_meta["pagination_verified"] = stabilized
        self.run_meta["status"] = (
            "SUCCESS"
            if rows and stabilized
            else "PARTIAL"
            if rows
            else "EMPTY"
        )

        self._save_diagnostics(
            page,
            f"success_{category.id}",
        )
        return rows

    def scrape_category(
        self,
        category: Category,
        location: Location,
    ) -> list[dict]:
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

            try:
                return self.scrape_category_on_page(
                    page,
                    category,
                    location,
                    navigate_to_category=True,
                )
            finally:
                context.close()
                browser.close()


__all__ = [
    "BodegaAurreraBlocked",
    "BodegaAurreraNetworkUnavailable",
    "BodegaAurreraScraper",
]
