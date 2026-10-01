from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from ..config import Category, Location
from ..parsers import clean_text


BASE_URL = "https://www.farmaciasdesimilares.com/"
DIAGNOSTICS = Path("diagnostics")


class FarmaciasSimilaresBlocked(RuntimeError):
    pass


class FarmaciasSimilaresNetworkUnavailable(RuntimeError):
    pass


class FarmaciasSimilaresScraper:
    """Scraper del catálogo público de Farmacias Similares.

    Descubre los productos desde la categoría y visita cada ficha de producto
    para obtener la Referencia (SKU) y el precio autoritativo mostrado en PDP.
    No intenta resolver ni evadir CAPTCHAs o controles anti-bot.
    """

    TARGET_RE = re.compile(r"\b(\d{1,5})\s+productos?\b", re.IGNORECASE)
    SKU_RE = re.compile(r"\bReferencia\s*:\s*([A-Za-z0-9._-]+)", re.IGNORECASE)
    DE_RE = re.compile(
        r"\bDe\s*\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
        re.IGNORECASE,
    )
    POR_RE = re.compile(
        r"\bPor\s*\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
        re.IGNORECASE,
    )
    AHORRA_RE = re.compile(
        r"\bAhorra\s*\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
        re.IGNORECASE,
    )
    MONEY_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)")
    BLOCK_MARKERS = (
        "access denied",
        "forbidden",
        "verify you are human",
        "verifica que eres humano",
        "captcha",
        "request rejected",
        "press and hold",
        "mantén presionado",
        "manten presionado",
    )

    BRAND_CANDIDATES = (
        "Sterimar",
        "Histiacil",
        "Gotinal",
        "Simibaby",
        "Simiwell",
        "Vick",
        "Afrin",
        "Iliadin",
        "Neilmed",
        "Sinomarin",
    )

    def __init__(
        self,
        headless: bool = False,
        browser_channel: str | None = "chrome",
        max_pages: int = 20,
        wait_ms: int = 900,
        manual_verification_timeout_ms: int = 180_000,
    ) -> None:
        self.headless = headless
        self.browser_channel = browser_channel
        self.max_pages = max_pages
        self.wait_ms = wait_ms
        self.manual_verification_timeout_ms = manual_verification_timeout_ms
        self.run_meta: dict = {}

    @staticmethod
    def _normalize(value: str | None) -> str:
        if not value:
            return ""
        value = value.casefold()
        value = (
            value.replace("á", "a")
            .replace("é", "e")
            .replace("í", "i")
            .replace("ó", "o")
            .replace("ú", "u")
            .replace("ü", "u")
        )
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _page_url(base_url: str, page_number: int) -> str:
        parts = urlsplit(base_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if page_number <= 1:
            query.pop("page", None)
        else:
            query["page"] = str(page_number)
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
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
                path=str(DIAGNOSTICS / f"farmacias_similares_{safe}.png"),
                full_page=True,
            )
        except Exception:
            pass
        try:
            (DIAGNOSTICS / f"farmacias_similares_{safe}.html").write_text(
                page.content(),
                encoding="utf-8",
            )
        except Exception:
            pass
        try:
            (DIAGNOSTICS / f"farmacias_similares_{safe}.json").write_text(
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
            or any(self._normalize(marker) in body for marker in self.BLOCK_MARKERS)
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
                "MANUAL_VERIFICATION_REQUIRED: Farmacias Similares mostró una "
                "verificación. Complétala manualmente en Chrome; el scraper "
                "esperará hasta 3 minutos.",
                flush=True,
            )
            deadline = time.monotonic() + (
                self.manual_verification_timeout_ms / 1000
            )
            while time.monotonic() < deadline:
                page.wait_for_timeout(1_000)
                if not self._is_blocked(page):
                    self.run_meta["manual_verification_resolved"] = True
                    self._save_diagnostics(page, "manual_verification_resolved")
                    print(
                        "MANUAL_VERIFICATION_RESOLVED: continuando.",
                        flush=True,
                    )
                    return

        raise FarmaciasSimilaresBlocked(
            "Farmacias Similares bloqueó o desafió la sesión automatizada. "
            "Se guardaron diagnósticos; no se intenta evadir la protección."
        )

    def _goto(self, page, url: str) -> None:
        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=90_000,
            )
        except PlaywrightError as exc:
            raise FarmaciasSimilaresNetworkUnavailable(str(exc)) from exc

        status = response.status if response else None
        page.wait_for_timeout(self.wait_ms)
        self._assert_not_blocked(page, status)

    @classmethod
    def _target_count(cls, page) -> int | None:
        text = cls._body_text(page)
        values = []
        for match in cls.TARGET_RE.finditer(text):
            try:
                values.append(int(match.group(1)))
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
              const out = [];
              const seen = new Set();

              for (const a of Array.from(document.querySelectorAll('a[href]'))) {
                let url;
                try { url = new URL(a.href, location.href); } catch (_) { continue; }
                if (url.origin !== location.origin) continue;
                if (!/\/p\/?$/i.test(url.pathname)) continue;

                const key = url.origin + url.pathname.replace(/\/$/, '');
                if (seen.has(key)) continue;

                let card = a;
                let cardText = '';
                for (let i = 0; i < 10 && card; i++, card = card.parentElement) {
                  const text = norm(card.innerText || card.textContent);
                  if (
                    text.length >= 10 &&
                    text.length <= 2800 &&
                    /Comprar ahora/i.test(text) &&
                    /\$\s*[0-9]/.test(text)
                  ) {
                    cardText = text;
                    break;
                  }
                }

                const image = a.querySelector('img[alt]');
                const title =
                  norm(a.getAttribute('aria-label')) ||
                  norm(a.getAttribute('title')) ||
                  norm(a.innerText) ||
                  norm(image ? image.alt : '');

                if (!cardText && !title) continue;

                seen.add(key);
                out.push({
                  href: key,
                  title,
                  card_text: cardText,
                });
              }
              return out;
            }
            """
        )

    @staticmethod
    def _float(raw: str | None) -> float | None:
        if not raw:
            return None
        try:
            return float(raw.replace(",", ""))
        except ValueError:
            return None

    @classmethod
    def _parse_detail(
        cls,
        body: str,
        fallback_title: str | None,
    ) -> dict:
        text = body or ""

        sku_match = cls.SKU_RE.search(text)
        sku = clean_text(sku_match.group(1)) if sku_match else None

        title = None
        lines = [clean_text(x) for x in text.splitlines()]
        lines = [x for x in lines if x]
        ref_index = next(
            (i for i, x in enumerate(lines) if re.match(r"Referencia\s*:", x, re.I)),
            None,
        )
        if ref_index is not None and ref_index > 0:
            candidate = lines[ref_index - 1]
            if candidate and len(candidate) <= 260:
                title = candidate
        title = title or clean_text(fallback_title)

        de_match = cls.DE_RE.search(text)
        por_match = cls.POR_RE.search(text)

        regular = cls._float(de_match.group(1)) if de_match else None
        current = cls._float(por_match.group(1)) if por_match else None

        if current is None:
            # On non-promotional PDPs, use the first price after Referencia.
            tail = text
            if sku_match:
                tail = text[sku_match.end():]
            values = [cls._float(x) for x in cls.MONEY_RE.findall(tail)]
            values = [x for x in values if x is not None and x > 0]
            if values:
                current = values[0]

        if regular is None:
            regular = current

        promotion = None
        ahorra_match = cls.AHORRA_RE.search(text)
        if ahorra_match:
            promotion = "Ahorra $" + ahorra_match.group(1)
        elif (
            current is not None
            and regular is not None
            and current < regular
        ):
            promotion = "Precio promocional"

        brand = None
        blob = title or ""
        for candidate in cls.BRAND_CANDIDATES:
            if re.search(
                rf"(?<!\w){re.escape(candidate)}(?!\w)",
                blob,
                re.IGNORECASE,
            ):
                brand = candidate
                break

        return {
            "sku": sku,
            "product": title,
            "brand": brand,
            "price_current": current,
            "price_regular": regular,
            "promotion": promotion,
            "has_de": de_match is not None,
            "has_por": por_match is not None,
        }

    def _detail_body_after_hydration(self, page) -> str:
        """Return the richest PDP text after client-side price hydration.

        Similares may first render the list price and inject De/Por/Ahorra
        shortly afterwards. We sample multiple snapshots and prefer the one
        containing explicit promotional price labels when present.
        """
        snapshots: list[str] = []

        for delay_ms in (0, 900, 1200, 1500):
            if delay_ms:
                page.wait_for_timeout(delay_ms)
            body = self._body_text(page)
            if body:
                snapshots.append(body)

        if not snapshots:
            return ""

        def score(text: str) -> tuple[int, int, int]:
            promo_labels = sum(
                int(bool(pattern.search(text)))
                for pattern in (self.DE_RE, self.POR_RE, self.AHORRA_RE)
            )
            money_count = len(self.MONEY_RE.findall(text))
            has_reference = int(bool(self.SKU_RE.search(text)))
            return (promo_labels, money_count, has_reference)

        return max(snapshots, key=score)

    def _discover_links(
        self,
        page,
        category: Category,
    ) -> tuple[list[dict], dict]:
        found: dict[str, dict] = {}
        pages: list[dict] = []
        target: int | None = None
        stale_pages = 0

        for page_number in range(1, self.max_pages + 1):
            url = self._page_url(category.url, page_number)
            self._goto(page, url)

            current_target = self._target_count(page)
            if current_target is not None:
                target = max(target or 0, current_target)

            try:
                page.wait_for_selector('a[href$="/p"], a[href*="/p?"]', timeout=20_000)
            except PlaywrightTimeoutError:
                links = []
            else:
                links = self._product_links(page)

            before = len(found)
            for item in links:
                href = clean_text(item.get("href"))
                if href:
                    found[href] = item
            after = len(found)

            pages.append(
                {
                    "page": page_number,
                    "url": page.url,
                    "links_on_page": len(links),
                    "new_links": after - before,
                    "cumulative_links": after,
                    "target_products": target,
                }
            )

            print(
                f"Farmacias Similares page={page_number}: "
                f"links={len(links)}, new={after - before}, "
                f"cumulative={after}, target={target}",
                flush=True,
            )

            if target is not None and after >= target:
                break

            stale_pages = stale_pages + 1 if after == before else 0
            if stale_pages >= 2:
                break

        discovery_complete = bool(
            target is not None and len(found) >= target
        )

        return list(found.values()), {
            "target_products": target,
            "product_links": len(found),
            "discovery_complete": discovery_complete,
            "pages": pages,
        }

    def scrape_category(
        self,
        category: Category,
        location: Location,
    ) -> list[dict]:
        self.run_meta = {
            "retailer": "Farmacias Similares",
            "category_id": category.id,
            "category_url": category.url,
            "blocked_detected": False,
            "manual_verification_required": False,
            "manual_verification_resolved": False,
        }

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
                links, discovery = self._discover_links(page, category)
                self.run_meta.update(discovery)

                now = datetime.now().astimezone().isoformat(timespec="seconds")
                rows: list[dict] = []
                detail_errors: list[dict] = []
                no_price: list[dict] = []

                for index, item in enumerate(links, start=1):
                    href = clean_text(item.get("href"))
                    fallback_title = clean_text(item.get("title"))
                    if not href:
                        continue

                    try:
                        self._goto(page, href)
                        body = self._detail_body_after_hydration(page)
                        detail = self._parse_detail(body, fallback_title)
                    except (
                        FarmaciasSimilaresBlocked,
                        FarmaciasSimilaresNetworkUnavailable,
                    ):
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

                    print(
                        f"Farmacias Similares detail {index}/{len(links)}: "
                        f"{detail.get('sku') or '-'} | "
                        f"{detail.get('product') or fallback_title} | "
                        f"current={detail.get('price_current')} | "
                        f"regular={detail.get('price_regular')}",
                        flush=True,
                    )

                    if detail.get("price_current") is None:
                        no_price.append(
                            {
                                "url": href,
                                "sku": detail.get("sku"),
                                "title": detail.get("product") or fallback_title,
                            }
                        )
                        continue

                    price_raw = json.dumps(
                        {
                            "detail_current": detail.get("price_current"),
                            "detail_regular": detail.get("price_regular"),
                            "promotion": detail.get("promotion"),
                            "has_de": detail.get("has_de"),
                            "has_por": detail.get("has_por"),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )

                    rows.append(
                        {
                            "scrape_timestamp": now,
                            "retailer": "Farmacias Similares",
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
                            "pickup_available": None,
                            "store_context_verified": False,
                            "store_context_method": (
                                "similares_online_product_detail"
                            ),
                            "url": urljoin(BASE_URL, href),
                            "price_raw": price_raw,
                        }
                    )

                unique: dict[str, dict] = {}
                for row in rows:
                    key = clean_text(row.get("sku")) or clean_text(row.get("url"))
                    if key:
                        unique[key] = row
                rows = list(unique.values())

                status = "SUCCESS"
                if not rows:
                    status = "EMPTY"
                elif detail_errors or no_price:
                    status = "PARTIAL"
                elif not self.run_meta.get("discovery_complete"):
                    status = "PARTIAL"

                self.run_meta.update(
                    {
                        "rows": len(rows),
                        "sku_complete": sum(
                            bool(clean_text(x.get("sku"))) for x in rows
                        ),
                        "price_complete": sum(
                            x.get("price_current") is not None for x in rows
                        ),
                        "regular_price_complete": sum(
                            x.get("price_regular") is not None for x in rows
                        ),
                        "url_complete": sum(bool(clean_text(x.get("url"))) for x in rows),
                        "products_without_price": no_price,
                        "detail_errors": detail_errors,
                        "status": status,
                    }
                )

                self._save_diagnostics(page, category.id)
                return rows
            finally:
                context.close()
                browser.close()


__all__ = [
    "FarmaciasSimilaresBlocked",
    "FarmaciasSimilaresNetworkUnavailable",
    "FarmaciasSimilaresScraper",
]
