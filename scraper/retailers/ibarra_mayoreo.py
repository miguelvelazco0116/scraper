from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from ..config import Category, Location
from ..parsers import clean_text


BASE_URL = "https://ibarramayoreo.com/"
DIAGNOSTICS = Path("diagnostics")


class IbarraMayoreoBlocked(RuntimeError):
    pass


class IbarraMayoreoNetworkUnavailable(RuntimeError):
    pass


class IbarraMayoreoScraper:
    """Scraper del catálogo público de Ibarra Mayoreo.

    Para este retailer el precio autoritativo es SIEMPRE la presentación
    "Caja" expuesta en la ficha del producto. Si un artículo tiene también
    precio por pieza, bolsa, barra, garrafa, etc., ese precio se ignora.

    No resuelve CAPTCHAs ni intenta evadir controles anti-bot.
    """

    MONEY_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)")
    SKU_RE = re.compile(r"\bSKU\s*:\s*([A-Za-z0-9._-]+)", re.IGNORECASE)
    BRAND_RE = re.compile(r"\bMarca\s*:\s*([^\r\n]+)", re.IGNORECASE)
    BOX_RE = re.compile(
        r"Presentaci[oó]n\s*:\s*Caja\s*-\s*"
        r"(\d+)\s*art[ií]culo(?:\(s\)|s)?\.?\s*"
        r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
        re.IGNORECASE,
    )
    BOX_FALLBACK_RE = re.compile(
        r"(\d+)\s*art[ií]culo(?:\(s\)|s)?\s*por\s*caja"
        r".{0,120}?\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
        re.IGNORECASE | re.DOTALL,
    )
    TARGET_RE = re.compile(
        r"DETERGENTES.*?LAVATRASTES.*?JAB\s*\(([\d,]+)\)",
        re.IGNORECASE | re.DOTALL,
    )
    BLOCK_MARKERS = (
        "access denied",
        "forbidden",
        "verify you are human",
        "verifica que eres humano",
        "captcha",
        "request rejected",
    )

    def __init__(
        self,
        headless: bool = False,
        browser_channel: str | None = "chrome",
        max_pages: int = 30,
        wait_ms: int = 700,
    ) -> None:
        self.headless = headless
        self.browser_channel = browser_channel
        self.max_pages = max_pages
        self.wait_ms = wait_ms
        self.last_meta: dict = {}

    @staticmethod
    def _page_url(base_url: str, page_number: int) -> str:
        parts = urlsplit(base_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if page_number <= 1:
            query.pop("p", None)
            query.pop("marca", None)
            query.pop("o", None)
        else:
            query["marca"] = "TODAS"
            query["o"] = "3"
            query["p"] = str(page_number)
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    @staticmethod
    def _body_text(page) -> str:
        try:
            return page.locator("body").inner_text(timeout=8_000)
        except Exception:
            return ""

    def _save_diagnostics(self, page, prefix: str, meta: dict) -> None:
        DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", prefix)
        try:
            page.screenshot(
                path=str(DIAGNOSTICS / f"ibarra_mayoreo_{safe}.png"),
                full_page=True,
            )
        except Exception:
            pass
        try:
            (DIAGNOSTICS / f"ibarra_mayoreo_{safe}.html").write_text(
                page.content(),
                encoding="utf-8",
            )
        except Exception:
            pass
        try:
            (DIAGNOSTICS / f"ibarra_mayoreo_{safe}.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _assert_not_blocked(self, page, status: int | None = None) -> None:
        blob = f"{page.title()}\n{self._body_text(page)}".casefold()
        if status in (401, 403, 429) or any(
            marker in blob for marker in self.BLOCK_MARKERS
        ):
            meta = dict(self.last_meta)
            meta["blocked"] = True
            meta["blocked_status"] = status
            self._save_diagnostics(page, "blocked", meta)
            raise IbarraMayoreoBlocked(
                "Ibarra Mayoreo bloqueó o desafió la sesión; "
                "se guardaron diagnósticos."
            )

    def _goto(self, page, url: str) -> None:
        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=90_000,
            )
        except PlaywrightError as exc:
            raise IbarraMayoreoNetworkUnavailable(str(exc)) from exc

        status = response.status if response else None
        page.wait_for_timeout(self.wait_ms)
        self._assert_not_blocked(page, status)

    @staticmethod
    def _extract_product_links(page) -> list[dict]:
        return page.locator("body").evaluate(
            r"""
            () => {
              const normalize = value =>
                String(value || '').replace(/\s+/g, ' ').trim();
              const money = /\$\s*[0-9][0-9,]*(?:\.\d{1,2})?/;
              const out = [];
              const seen = new Set();

              const anchors = Array.from(document.querySelectorAll('a[href]'));
              for (const a of anchors) {
                const href = a.href || '';
                const hrefLower = href.toLowerCase();

                if (
                  !href.startsWith(location.origin) ||
                  hrefLower.includes('/catalogo/') ||
                  hrefLower.includes('/carrito') ||
                  hrefLower.includes('/cuenta') ||
                  hrefLower.includes('/favorit') ||
                  hrefLower.includes('/login') ||
                  hrefLower.includes('facebook.com') ||
                  hrefLower.includes('wa.me')
                ) {
                  continue;
                }

                const path = (() => {
                  try { return new URL(href).pathname; } catch (_) { return ''; }
                })();
                const rootProductPath =
                  /^\/[a-z0-9áéíóúüñ%._~!                let card = a;
                let found = null;
                for (let i = 0; i < 10 && card; i++, card = card.parentElement) {
                  const text = normalize(card.innerText || card.textContent);
                  if (
                    /Agregar al carrito/i.test(text) &&
                    money.test(text) &&
                    text.length >= 20 &&
                    text.length <= 2200
                  ) {
                    found = card;
                    break;
                  }
                }
                if (!found) continue;

                const cardText = (found.innerText || found.textContent || '').trim();
                const text = normalize(cardText);

                // Discovery must include every catalogue product, even when
                // the card currently exposes only BOLSA/BARRA/etc. The Caja
                // rule is enforced later from the individual product page.
                const heading = found.querySelector(
'()*+,;=:@-]+\/?$/i.test(path);

                let card = a;
                let found = null;
                for (let i = 0; i < 10 && card; i++, card = card.parentElement) {
                  const text = normalize(card.innerText || card.textContent);
                  const looksLikeProductCard =
                    /Agregar al carrito|No disponible|Agotado|Sin existencia|art[ií]culo(?:\(s\)|s)?\s+por/i.test(text) ||
                    money.test(text);

                  if (
                    rootProductPath &&
                    looksLikeProductCard &&
                    text.length >= 8 &&
                    text.length <= 2600
                  ) {
                    found = card;
                    break;
                  }
                }
                if (!found) continue;

                const cardText = (found.innerText || found.textContent || '').trim();
                const text = normalize(cardText);

                // Discovery includes unavailable cards and non-Caja cards.
                // The authoritative Caja rule is enforced on product detail.
                const heading = found.querySelector(
                  'h1,h2,h3,h4,h5,[class*="name"],[class*="title"]'
                );
                const image = found.querySelector('img[alt]');
                const title =
                  normalize(a.getAttribute('title')) ||
                  normalize(a.getAttribute('aria-label')) ||
                  normalize(a.innerText) ||
                  normalize(heading ? heading.innerText : '') ||
                  normalize(image ? image.alt : '');

                if (!title || title.length < 3 || title.length > 220) continue;

                const key = href.split('#')[0];
                if (seen.has(key)) continue;
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
    def _catalogue_metadata(page, category: Category) -> dict:
        """Read the count for the exact category URL plus the last page."""
        target = None
        last_page = None

        target_path = unquote(urlsplit(category.url).path).rstrip("/").casefold()

        try:
            anchors = page.locator("a[href]")
            for index in range(min(anchors.count(), 2500)):
                anchor = anchors.nth(index)
                href = anchor.get_attribute("href")
                if not href:
                    continue

                try:
                    full_url = urljoin(page.url, href)
                    path = unquote(urlsplit(full_url).path).rstrip("/").casefold()
                except Exception:
                    path = ""

                if path == target_path:
                    try:
                        text = clean_text(anchor.inner_text(timeout=500)) or ""
                    except Exception:
                        text = ""
                    match = re.search(r"\(([\d,]+)\)", text)
                    if match:
                        try:
                            value = int(match.group(1).replace(",", ""))
                            target = max(target or 0, value)
                        except ValueError:
                            pass

                try:
                    parts = urlsplit(full_url)
                    query = dict(parse_qsl(parts.query, keep_blank_values=True))
                    raw_page = query.get("p")
                    if raw_page is not None:
                        page_number = int(raw_page)
                        if page_number > 0:
                            last_page = max(last_page or 0, page_number)
                except Exception:
                    pass
        except Exception:
            pass

        return {
            "target_products": target,
            "last_page": last_page,
        }

    @classmethod
    def _target_count(cls, body: str) -> int | None:
        values: list[int] = []
        for match in cls.TARGET_RE.finditer(body or ""):
            try:
                values.append(int(match.group(1).replace(",", "")))
            except ValueError:
                continue
        return max(values) if values else None

    def _discover_product_links(
        self,
        page,
        category: Category,
    ) -> tuple[list[dict], dict]:
        products: dict[str, dict] = {}
        pages: list[dict] = []
        target: int | None = None
        last_page: int | None = None
        stale_pages = 0

        for page_number in range(1, self.max_pages + 1):
            if last_page is not None and page_number > last_page:
                break

            url = self._page_url(category.url, page_number)
            self._goto(page, url)

            rendered_meta = self._catalogue_metadata(page, category)

            rendered_target = rendered_meta.get("target_products")
            if rendered_target is not None:
                target = max(target or 0, int(rendered_target))

            rendered_last_page = rendered_meta.get("last_page")
            if rendered_last_page is not None:
                last_page = max(last_page or 0, int(rendered_last_page))

            page_links = self._extract_product_links(page)

            if (
                page_number == 1
                and last_page is None
                and page_links
                and len(page_links) < 12
            ):
                last_page = 1

            before = len(products)
            for item in page_links:
                href = clean_text(item.get("href"))
                if href:
                    products[href] = item
            after = len(products)

            pages.append(
                {
                    "page": page_number,
                    "url": page.url,
                    "links_on_page": len(page_links),
                    "new_links": after - before,
                    "cumulative_links": after,
                    "target": target,
                    "last_page": last_page,
                }
            )
            print(
                f"Ibarra page={page_number}: links={len(page_links)}, "
                f"new={after - before}, cumulative={after}, "
                f"target={target}, last_page={last_page}"
            )

            if last_page is not None and page_number >= last_page:
                break

            if last_page is None and target is not None and after >= target:
                break

            if after == before:
                stale_pages += 1
            else:
                stale_pages = 0

            # Only use the stale-page guard when the site did not expose a
            # definite last page. If pagination says p=13, walk through p=13
            # even if one intermediate page renders fewer cards.
            if last_page is None and stale_pages >= 2:
                break

        meta = {
            "category_id": category.id,
            "category_url": category.url,
            "target_products": target,
            "last_page": last_page,
            "product_links": len(products),
            "discovery_complete": bool(
                (last_page is not None and pages and pages[-1]["page"] >= last_page)
                or (
                    last_page is None
                    and target is not None
                    and len(products) >= target
                )
            ),
            "pages": pages,
        }
        return list(products.values()), meta

    @classmethod
    def _parse_box_detail(
        cls,
        body: str,
        fallback_title: str | None = None,
    ) -> dict:
        text = body or ""
        title = clean_text(fallback_title)

        sku_match = cls.SKU_RE.search(text)
        sku = clean_text(sku_match.group(1)) if sku_match else None

        brand_match = cls.BRAND_RE.search(text)
        brand = clean_text(brand_match.group(1)) if brand_match else None
        if brand:
            brand = re.split(
                r"\s{2,}|\n|\r|\d+\s+de\s+5",
                brand,
                maxsplit=1,
            )[0].strip()

        box_match = cls.BOX_RE.search(text)
        if box_match is None:
            box_match = cls.BOX_FALLBACK_RE.search(text)

        pack_count = None
        box_price = None
        if box_match is not None:
            try:
                pack_count = int(box_match.group(1))
                box_price = float(box_match.group(2).replace(",", ""))
            except (TypeError, ValueError):
                pack_count = None
                box_price = None

        promo_match = re.search(
            r"(Ahorra\s*\$\s*[0-9][0-9,]*(?:\.\d{1,2})?)",
            text,
            flags=re.IGNORECASE,
        )
        promotion = clean_text(promo_match.group(1)) if promo_match else None

        return {
            "product": title,
            "sku": sku,
            "brand": brand,
            "box_units": pack_count,
            "box_price": box_price,
            "promotion": promotion,
        }

    def scrape_category(
        self,
        category: Category,
        location: Location,
    ) -> list[dict]:
        self.last_meta = {
            "retailer": "Ibarra Mayoreo",
            "category_id": category.id,
            "category_url": category.url,
            "blocked": False,
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
                links, meta = self._discover_product_links(page, category)
                self.last_meta.update(meta)

                rows: list[dict] = []
                no_box: list[dict] = []
                parse_errors: list[dict] = []
                now = datetime.now().astimezone().isoformat(timespec="seconds")

                for index, item in enumerate(links, start=1):
                    href = clean_text(item.get("href"))
                    fallback_title = clean_text(item.get("title"))
                    if not href:
                        continue

                    try:
                        self._goto(page, href)
                        body = self._body_text(page)
                        detail = self._parse_box_detail(body, fallback_title)
                    except (
                        IbarraMayoreoBlocked,
                        IbarraMayoreoNetworkUnavailable,
                    ):
                        raise
                    except Exception as exc:
                        parse_errors.append(
                            {
                                "url": href,
                                "title": fallback_title,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                        continue

                    print(
                        f"Ibarra detail {index}/{len(links)}: "
                        f"{detail.get('sku') or '-'} | "
                        f"{detail.get('product') or fallback_title} | "
                        f"caja={detail.get('box_price')}"
                    )

                    if detail.get("box_price") is None:
                        no_box.append(
                            {
                                "url": href,
                                "title": detail.get("product") or fallback_title,
                                "sku": detail.get("sku"),
                            }
                        )
                        continue

                    pack_count = detail.get("box_units")
                    box_price = detail.get("box_price")
                    if pack_count is not None:
                        price_raw = (
                            f"CAJA | {pack_count} artículos por caja | "
                            + "$"
                            + f"{box_price:.2f} MXN"
                        )
                    else:
                        price_raw = "CAJA | $" + f"{box_price:.2f} MXN"

                    rows.append(
                        {
                            "scrape_timestamp": now,
                            "retailer": "Ibarra Mayoreo",
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
                            "price_current": box_price,
                            "price_regular": box_price,
                            "promotion": detail.get("promotion"),
                            "pickup_available": None,
                            "store_context_verified": False,
                            "store_context_method": (
                                "ibarra_catalog_product_detail_box"
                            ),
                            "url": urljoin(BASE_URL, href),
                            "price_raw": price_raw,
                        }
                    )

                unique: dict[str, dict] = {}
                for row in rows:
                    key = (
                        clean_text(row.get("sku"))
                        or clean_text(row.get("url"))
                        or "|".join(
                            [
                                clean_text(row.get("product")) or "",
                                str(row.get("price_current") or ""),
                            ]
                        )
                    )
                    if key:
                        unique[key] = row
                rows = list(unique.values())

                discovery_complete = bool(
                    self.last_meta.get("discovery_complete")
                )
                status = "SUCCESS" if rows else "EMPTY"
                if parse_errors:
                    status = "PARTIAL"
                elif not discovery_complete:
                    status = "PARTIAL"

                self.last_meta.update(
                    {
                        "rows": len(rows),
                        "unique_skus": len(
                            {str(x["sku"]) for x in rows if x.get("sku")}
                        ),
                        "price_complete": sum(
                            x.get("price_current") is not None for x in rows
                        ),
                        "url_complete": sum(bool(x.get("url")) for x in rows),
                        "products_without_box_price": no_box,
                        "parse_errors": parse_errors,
                        "status": status,
                    }
                )

                self._save_diagnostics(
                    page,
                    category.id,
                    self.last_meta,
                )
                return rows
            finally:
                context.close()
                browser.close()


__all__ = [
    "IbarraMayoreoBlocked",
    "IbarraMayoreoNetworkUnavailable",
    "IbarraMayoreoScraper",
]
