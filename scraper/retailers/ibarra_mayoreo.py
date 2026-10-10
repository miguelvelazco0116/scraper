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
    PRESENTATION_RE = re.compile(
        r"Presentaci[oó]n\s*:\s*"
        r"([A-Za-zÁÉÍÓÚÜÑáéíóúüñ ]+?)\s*-\s*"
        r"(\d+)\s*art[ií]culo(?:\(s\)|s)?\.?\s*"
        r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
        re.IGNORECASE,
    )
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
        low_memory: bool = False,
        page_recycle_interval: int = 50,
    ) -> None:
        self.headless = headless
        self.browser_channel = browser_channel
        self.max_pages = max_pages
        self.wait_ms = wait_ms
        self.low_memory = low_memory
        self.page_recycle_interval = max(10, int(page_recycle_interval))
        self.last_meta: dict = {}

    @staticmethod
    def _low_memory_browser_args() -> list[str]:
        return [
            "--disable-gpu",
            "--disable-extensions",
            "--disable-sync",
            "--disable-translate",
            "--no-first-run",
            "--no-default-browser-check",
            "--renderer-process-limit=2",
        ]

    @staticmethod
    def _install_low_memory_routes(context) -> None:
        def handle(route, request):
            if request.resource_type in {"image", "media", "font"}:
                route.abort()
            else:
                route.continue_()

        context.route("**/*", handle)

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
        last_error: PlaywrightError | None = None

        for attempt in range(1, 5):
            try:
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=120_000,
                )
                status = response.status if response else None
                page.wait_for_timeout(max(self.wait_ms, 1_000))
                self._assert_not_blocked(page, status)
                return
            except IbarraMayoreoBlocked:
                raise
            except PlaywrightError as exc:
                last_error = exc
                if attempt < 4:
                    try:
                        page.wait_for_timeout(min(1_500 * attempt, 4_500))
                    except Exception:
                        pass

        raise IbarraMayoreoNetworkUnavailable(
            f"No fue posible cargar {url} tras 4 intentos: {last_error}"
        ) from last_error

    @staticmethod
    def _is_product_candidate(item: dict) -> bool:
        title = clean_text(item.get("title")) or ""
        href = clean_text(item.get("href")) or ""

        folded_title = (
            title.casefold()
            .replace("á", "a")
            .replace("é", "e")
            .replace("í", "i")
            .replace("ó", "o")
            .replace("ú", "u")
            .replace("ü", "u")
        )
        slug = unquote(urlsplit(href).path).strip("/").casefold()

        blocked = {
            "bonus",
            "destacados",
            "promociones",
            "promocion",
            "ofertas",
            "marcas",
            "categorias",
            "categoria",
            "catalogo",
            "inicio",
            "novedades",
        }
        return folded_title not in blocked and slug not in blocked

    @classmethod
    def _extract_product_links(cls, page) -> list[dict]:
        """Discover every product card shown on the current catalogue page."""
        items = page.locator("body").evaluate(
            r"""
            () => {
              const normalize = value =>
                String(value || '').replace(/\s+/g, ' ').trim();
              const money = /\$\s*[0-9][0-9,]*(?:\.\d{1,2})?/;
              const out = [];
              const seen = new Set();

              for (const a of Array.from(document.querySelectorAll('a[href]'))) {
                const href = a.href || '';
                if (!href.startsWith(location.origin)) continue;

                let parsed;
                try {
                  parsed = new URL(href);
                } catch (_) {
                  continue;
                }

                const lower = parsed.href.toLowerCase();
                const parts = parsed.pathname.split('/').filter(Boolean);

                // Product detail pages on Ibarra are root-level slugs.
                // Category/account/cart/navigation links are deeper paths.
                if (
                  parts.length !== 1 ||
                  lower.includes('/carrito') ||
                  lower.includes('/cuenta') ||
                  lower.includes('/favorit') ||
                  lower.includes('/login')
                ) {
                  continue;
                }

                let card = a;
                let found = null;

                for (let i = 0; i < 10 && card; i++, card = card.parentElement) {
                  const text = normalize(card.innerText || card.textContent);
                  if (!text || text.length < 8 || text.length > 2800) continue;

                  const productSignals =
                    /Agregar al carrito|No disponible|Agotado|Sin existencia/i.test(text) ||
                    /art[ií]culo(?:\(s\)|s)?\s+por\s+(caja|bolsa|barra|garrafa|botella|paquete|saco)/i.test(text) ||
                    money.test(text);

                  if (productSignals) {
                    found = card;
                    break;
                  }
                }

                if (!found) continue;

                const cardText = (found.innerText || found.textContent || '').trim();

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

                const key = parsed.origin + parsed.pathname.replace(/\/$/, '');
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
        return [
            item for item in items
            if isinstance(item, dict) and cls._is_product_candidate(item)
        ]

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

        recovery_pages: list[dict] = []
        if (
            target is not None
            and len(products) < target
            and (last_page is not None or pages)
        ):
            recovery_last_page = (
                int(last_page)
                if last_page is not None
                else int(pages[-1]["page"])
            )
            print(
                f"Ibarra recovery: faltan {target - len(products)} "
                f"producto(s); segunda pasada hasta pagina {recovery_last_page}"
            )

            for page_number in range(1, min(recovery_last_page, self.max_pages) + 1):
                if len(products) >= target:
                    break

                url = self._page_url(category.url, page_number)
                before = len(products)
                try:
                    self._goto(page, url)
                    page.wait_for_timeout(max(self.wait_ms, 1_500))
                    page_links = self._extract_product_links(page)
                except (IbarraMayoreoBlocked, IbarraMayoreoNetworkUnavailable):
                    raise
                except Exception as exc:
                    recovery_pages.append(
                        {
                            "page": page_number,
                            "url": url,
                            "links_on_page": 0,
                            "new_links": 0,
                            "cumulative_links": len(products),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    continue

                for item in page_links:
                    href = clean_text(item.get("href"))
                    if href:
                        products[href] = item

                after = len(products)
                recovery_pages.append(
                    {
                        "page": page_number,
                        "url": page.url,
                        "links_on_page": len(page_links),
                        "new_links": after - before,
                        "cumulative_links": after,
                    }
                )
                print(
                    f"Ibarra recovery page={page_number}: "
                    f"links={len(page_links)}, new={after - before}, "
                    f"cumulative={after}/{target}"
                )

        meta = {
            "category_id": category.id,
            "category_url": category.url,
            "target_products": target,
            "last_page": last_page,
            "product_links": len(products),
            "discovery_complete": bool(
                (
                    target is not None
                    and len(products) >= target
                )
                or (
                    target is None
                    and last_page is not None
                    and pages
                    and pages[-1]["page"] >= last_page
                )
            ),
            "pages": pages,
            "recovery_pages": recovery_pages,
            "recovery_attempted": bool(recovery_pages),
            "recovery_recovered": (
                max(len(products) - (pages[-1]["cumulative_links"] if pages else 0), 0)
                if recovery_pages
                else 0
            ),
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

        presentations: list[dict] = []
        for match in cls.PRESENTATION_RE.finditer(text):
            try:
                presentation = clean_text(match.group(1))
                units = int(match.group(2))
                price = float(match.group(3).replace(",", ""))
            except (TypeError, ValueError):
                continue
            if not presentation or units <= 0 or price <= 0:
                continue
            presentations.append(
                {
                    "presentation": presentation,
                    "units": units,
                    "price": price,
                }
            )

        box = next(
            (
                item for item in presentations
                if (clean_text(item.get("presentation")) or "").casefold() == "caja"
            ),
            None,
        )

        pack_count = None
        box_price = None
        if box is not None:
            pack_count = int(box["units"])
            box_price = float(box["price"])
        else:
            box_match = cls.BOX_FALLBACK_RE.search(text)
            if box_match is not None:
                try:
                    pack_count = int(box_match.group(1))
                    box_price = float(box_match.group(2).replace(",", ""))
                except (TypeError, ValueError):
                    pack_count = None
                    box_price = None

        single = next(
            (
                item for item in presentations
                if int(item.get("units") or 0) == 1
                and (clean_text(item.get("presentation")) or "").casefold() != "caja"
            ),
            None,
        )

        single_presentation = (
            clean_text(single.get("presentation"))
            if single is not None
            else None
        )
        single_price = (
            float(single.get("price"))
            if single is not None
            else None
        )

        if box_price is not None and pack_count:
            sale_presentation = "CAJA"
            sale_units = pack_count
            sale_price = box_price
        elif single_price is not None:
            sale_presentation = (single_presentation or "PIEZA").upper()
            sale_units = 1
            sale_price = single_price
        else:
            sale_presentation = None
            sale_units = None
            sale_price = None

        price_per_unit = (
            round(float(sale_price) / int(sale_units), 4)
            if sale_price is not None and sale_units
            else None
        )
        is_single_item = (
            bool(int(sale_units) == 1)
            if sale_units is not None
            else None
        )

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
            "sale_presentation": sale_presentation,
            "sale_units": sale_units,
            "sale_price": sale_price,
            "price_per_unit": price_per_unit,
            "is_single_item": is_single_item,
            "single_item_presentation": single_presentation,
            "single_item_price": single_price,
            "presentations": presentations,
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
            if self.low_memory:
                launch_kwargs["args"] = self._low_memory_browser_args()

            browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(
                locale="es-MX",
                viewport={"width": 1440, "height": 1000},
            )
            if self.low_memory:
                self._install_low_memory_routes(context)
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
                                "single_item_presentation": detail.get(
                                    "single_item_presentation"
                                ),
                                "single_item_price": detail.get("single_item_price"),
                            }
                        )

                    sale_price = detail.get("sale_price")
                    sale_units = detail.get("sale_units")
                    sale_presentation = detail.get("sale_presentation")
                    price_per_unit = detail.get("price_per_unit")
                    if (
                        sale_price is None
                        or sale_units is None
                        or price_per_unit is None
                    ):
                        continue

                    pack_count = detail.get("box_units")
                    box_price = detail.get("box_price")
                    single_presentation = detail.get("single_item_presentation")
                    single_price = detail.get("single_item_price")

                    price_raw_parts = [
                        f"{sale_presentation} | {sale_units} unidad(es)",
                        "precio observado=$" + f"{float(sale_price):.2f} MXN",
                        "precio por pieza=$" + f"{float(price_per_unit):.4f} MXN",
                    ]
                    if box_price is not None and pack_count:
                        price_raw_parts.append(
                            f"caja={pack_count} x $" + f"{float(box_price):.2f} MXN"
                        )
                    if single_price is not None:
                        price_raw_parts.append(
                            f"{single_presentation or 'SINGLE'}=$"
                            + f"{float(single_price):.2f} MXN"
                        )
                    price_raw = " | ".join(price_raw_parts)

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
                            "price_current": sale_price,
                            "price_regular": sale_price,
                            "promotion": detail.get("promotion"),
                            "package_type": sale_presentation,
                            "units_per_package": sale_units,
                            "is_single_item": detail.get("is_single_item"),
                            "price_per_unit": price_per_unit,
                            "single_item_presentation": single_presentation,
                            "single_item_price": single_price,
                            "pickup_available": None,
                            "store_context_verified": False,
                            "store_context_method": (
                                "ibarra_catalog_product_detail_box"
                            ),
                            "url": urljoin(BASE_URL, href),
                            "price_raw": price_raw,
                        }
                    )

                    if (
                        self.low_memory
                        and index < len(links)
                        and index % self.page_recycle_interval == 0
                    ):
                        try:
                            page.close(run_before_unload=False)
                        except Exception:
                            pass
                        page = context.new_page()
                        print(
                            f"Ibarra low-memory: renderer reciclado "
                            f"despues de {index} productos"
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
                        "unit_count_complete": sum(
                            x.get("units_per_package") is not None for x in rows
                        ),
                        "unit_price_complete": sum(
                            x.get("price_per_unit") is not None for x in rows
                        ),
                        "single_item_products": sum(
                            bool(x.get("is_single_item")) for x in rows
                        ),
                        "single_item_price_complete": sum(
                            x.get("single_item_price") is not None for x in rows
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
