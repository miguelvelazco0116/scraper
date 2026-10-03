from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, Response, TimeoutError as PlaywrightTimeoutError, sync_playwright

from ..availability import AVAILABLE, UNAVAILABLE, UNKNOWN
from ..config import Category, Location
from ..parsers import absolute_url, clean_text, extract_sku, parse_money

BASE_URL = "https://www.soriana.com/"
PRODUCT_SELECTOR = ".product-tile.js-product-card"
BLOCK_MARKERS = ("GF R01", "Access Denied", "Forbidden")
NETWORK_MARKERS = ("Search-UpdateGrid", "Search-ShowAjax")


class SorianaBlocked(RuntimeError):
    pass


class SorianaDeferred(RuntimeError):
    pass


class SorianaScraper:
    """Browser-based scraper that follows Soriana's normal storefront navigation.

    It intentionally does not implement CAPTCHA solving, proxy rotation,
    fingerprint spoofing, or other anti-bot bypass techniques. If Soriana
    blocks the session, diagnostics are saved and the run stops.
    """

    def __init__(
        self,
        headless: bool = True,
        diagnostics_dir: str | Path = "diagnostics",
        max_load_more: int = 100,
        wait_ms: int = 1200,
        browser_channel: str | None = None,
        profile_dir: str | Path | None = None,
        warmup_homepage: bool = True,
        circuit_cooldown_seconds: int = 600,
    ) -> None:
        self.headless = headless
        self.diagnostics_dir = Path(diagnostics_dir)
        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        # Kept for CLI backwards compatibility. For Soriana this now acts as
        # the maximum number of catalogue pages to traverse.
        self.max_load_more = max_load_more
        self.wait_ms = wait_ms
        self.browser_channel = browser_channel
        self.profile_dir = Path(profile_dir) if profile_dir else None
        if self.profile_dir is not None:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.warmup_homepage = warmup_homepage
        self.circuit_cooldown_seconds = max(0, int(circuit_cooldown_seconds))
        self.circuit_file = (
            self.profile_dir / "soriana_circuit.json"
            if self.profile_dir is not None
            else self.diagnostics_dir / "soriana_circuit.json"
        )
        self.grid_responses: list[dict[str, Any]] = []

    def _read_circuit(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.circuit_file.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def circuit_remaining_seconds(self) -> int:
        payload = self._read_circuit()
        blocked_at = payload.get("blocked_at_epoch")
        cooldown = payload.get(
            "cooldown_seconds",
            self.circuit_cooldown_seconds,
        )
        try:
            remaining = int(
                float(blocked_at) + float(cooldown) - time.time()
            )
        except (TypeError, ValueError):
            return 0
        return max(0, remaining)

    def _assert_circuit_ready(self) -> None:
        remaining = self.circuit_remaining_seconds()
        if remaining > 0:
            raise SorianaDeferred(
                "Soriana está en cooldown local después de un bloqueo previo; "
                f"faltan aproximadamente {remaining}s."
            )

    def _record_block(self, page: Page, status: int | None = None) -> None:
        payload = {
            "blocked_at_epoch": time.time(),
            "blocked_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "cooldown_seconds": self.circuit_cooldown_seconds,
            "status": status,
            "url": page.url,
        }
        try:
            self.circuit_file.parent.mkdir(parents=True, exist_ok=True)
            self.circuit_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _clear_circuit(self) -> None:
        try:
            self.circuit_file.unlink(missing_ok=True)
        except Exception:
            pass

    def _capture_grid_response(self, response: Response) -> None:
        if not any(marker in response.url for marker in NETWORK_MARKERS):
            return
        record: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "status": response.status,
            "url": response.url,
        }
        try:
            text = response.text()
            record["body_length"] = len(text)
        except Exception as exc:
            record["read_error"] = str(exc)
        self.grid_responses.append(record)

    def _save_diagnostics(self, page: Page, prefix: str) -> None:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", prefix)
        try:
            page.screenshot(path=str(self.diagnostics_dir / f"{safe}.png"), full_page=True)
        except Exception:
            pass
        try:
            (self.diagnostics_dir / f"{safe}.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        (self.diagnostics_dir / "grid_responses.json").write_text(
            json.dumps(self.grid_responses, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _assert_not_blocked(self, page: Page, status: int | None = None) -> None:
        body = ""
        try:
            body = page.locator("body").inner_text(timeout=5_000)
        except Exception:
            pass
        blocked = status == 403 or any(marker.lower() in body.lower() for marker in BLOCK_MARKERS)
        if blocked:
            self._record_block(page, status)
            self._save_diagnostics(page, "blocked")
            raise SorianaBlocked(
                "Soriana bloqueó la sesión (403/GF R01). Se guardaron diagnósticos; "
                "el scraper no intenta evadir la protección del sitio."
            )

    def _extract_cards(self, page: Page, category: Category, location: Location) -> list[dict[str, Any]]:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        js = r"""
        els => els.map(el => {
          const text = (sel) => {
            const node = el.querySelector(sel);
            return node ? node.textContent.trim() : null;
          };
          const attr = (sel, name) => {
            const node = el.querySelector(sel);
            return node ? node.getAttribute(name) : null;
          };
          const link = el.querySelector(
            'a.product-tile--link[href*=".html"], .pdp-link a[href*=".html"], a[data-quickview="false"][href*=".html"]'
          );
          const image = el.querySelector('img.tile-image');
          const priceCandidates = [
            text('.sales'),
            text('.list .value'),
            text('.list'),
            text('.price.product-tile--price')
          ].filter(Boolean);
          const promoNodes = Array.from(
            el.querySelectorAll('.product-badge, .badge-label-coupons, [class*="promo"]')
          ).map(n => n.textContent.trim()).filter(Boolean);
          const availabilityText = [
            (el.innerText || el.textContent || '').trim(),
            ...Array.from(el.querySelectorAll('button, [role="button"]'))
              .map(n => (n.innerText || n.textContent || '').trim())
              .filter(Boolean)
          ].join(' | ');
          return {
            data_pid: el.getAttribute('data-pid') || attr('[data-pid]', 'data-pid'),
            product: text('a.product-tile--link') || text('.pdp-link a') || (image ? image.getAttribute('alt') : null),
            url: link ? link.getAttribute('href') : null,
            price_texts: [...new Set(priceCandidates)],
            promo_texts: [...new Set(promoNodes)],
            availability_text: availabilityText
          };
        })
        """
        locator = page.locator(PRODUCT_SELECTOR)
        if not locator.count():
            return []
        raw = locator.evaluate_all(js)
        out: list[dict[str, Any]] = []
        for item in raw:
            url = absolute_url(item.get("url"))
            product = clean_text(item.get("product"))
            if not product or not url:
                continue

            money_texts = item.get("price_texts") or []
            values: list[float] = []
            for txt in money_texts:
                for token in re.findall(r"\$\s*[\d,]+(?:\.\d{1,2})?", txt):
                    val = parse_money(token)
                    if val is not None and val not in values:
                        values.append(val)

            current_price = values[0] if values else None
            regular_price = values[1] if len(values) > 1 else current_price
            if current_price and regular_price and current_price > regular_price:
                current_price, regular_price = regular_price, current_price

            availability = self._availability_from_card_text(
                item.get("availability_text")
            )

            out.append(
                {
                    "scrape_timestamp": now,
                    "retailer": "Soriana",
                    "city": location.city,
                    "state": location.state,
                    "postal_code": location.postal_code,
                    "store": location.store,
                    "category": category.name,
                    "subcategory": category.subcategory,
                    "sub_subcategory": category.sub_subcategory,
                    "category_id": category.id,
                    "sku": extract_sku(url, item.get("data_pid")),
                    "brand": self._infer_brand(product),
                    "product": product,
                    "price_current": current_price,
                    "price_regular": regular_price,
                    "promotion": clean_text(" | ".join(item.get("promo_texts") or [])),
                    **availability,
                    "url": url,
                    "price_raw": clean_text(" | ".join(money_texts)),
                }
            )
        return out

    @staticmethod
    def _availability_from_card_text(text: str | None) -> dict[str, Any]:
        """Clasifica disponibilidad Soriana con señales estrictas.

        El storefront puede mostrar frases como "No disponible" asociadas a
        modalidades de entrega, por lo que no se usan como señal global de
        quiebre. Sólo marcadores inequívocos de stock clasifican UNAVAILABLE.
        """
        raw = clean_text(text)
        if not raw:
            return {
                "availability_status": UNKNOWN,
                "is_available": None,
                "availability_raw": None,
            }

        negative = re.search(
            r"\b(agotad[oa]s?|sin\s+existencia|sin\s+stock|fuera\s+de\s+stock|out\s+of\s+stock)\b",
            raw,
            flags=re.IGNORECASE,
        )
        if negative:
            return {
                "availability_status": UNAVAILABLE,
                "is_available": False,
                "availability_raw": negative.group(0),
            }

        positive = re.search(
            r"\b(agregar(?:\s+al\s+carrito)?|añadir(?:\s+al\s+carrito)?|comprar\s+ahora)\b",
            raw,
            flags=re.IGNORECASE,
        )
        if positive:
            return {
                "availability_status": AVAILABLE,
                "is_available": True,
                "availability_raw": positive.group(0),
            }

        return {
            "availability_status": UNKNOWN,
            "is_available": None,
            "availability_raw": raw[:500],
        }

    @staticmethod
    def _infer_brand(product: str | None) -> str | None:
        if not product:
            return None
        known = [
            # Cuidado bucal
            "Colgate", "Oral-B", "Listerine", "Sensodyne", "Crest", "Gum",
            "Curaprox", "Philips", "Aquafresh", "Bexident", "Corega",
            # Cuidado del hogar / limpiadores
            "Ariel", "Pinol", "Fabuloso", "Cloralex", "Clorox", "Axion",
            "Harpic", "Windex", "Drano", "Brasso", "Flash", "Tide", "Persil",
            "Escudo", "Vanish", "Downy", "Suavitel", "Salvo", "Maestro Limpio",
            "Roma", "Zote", "Carisma", "Ace", "Bold", "Blanca Nieves",
        ]
        lower = product.lower()
        for brand in known:
            if brand.lower() in lower:
                return brand
        return None

    @staticmethod
    def _total_pages(page: Page) -> int:
        pager = page.locator("section.c-pagination-carousel[data-total-pages]").first
        try:
            raw = pager.get_attribute("data-total-pages", timeout=3_000)
            return max(1, int(float(raw))) if raw else 1
        except Exception:
            return 1

    def _go_to_page(self, page: Page, page_number: int) -> bool:
        selector = f"button.more.page[data-page-number='{page_number}']"
        button = page.locator(selector).first
        if not button.count():
            return False

        before = None
        try:
            before = page.locator(f"{PRODUCT_SELECTOR} a.product-tile--link").first.get_attribute("href", timeout=2_000)
        except Exception:
            pass

        try:
            # The pagination carousel keeps all page buttons in the DOM; some
            # later pages are off-screen, so a DOM click is more reliable than
            # a visibility-dependent mouse click and still uses Soriana's own JS.
            button.evaluate("el => el.click()")
            page.wait_for_function(
                r"""
                ([n, previous]) => {
                  const selected = document.querySelector(`button.more.page[data-page-number="${n}"]`);
                  const link = document.querySelector('.product-tile.js-product-card a.product-tile--link');
                  const href = link ? link.getAttribute('href') : null;
                  return selected && selected.classList.contains('selected') && href && (!previous || href !== previous);
                }
                """,
                arg=[str(page_number), before],
                timeout=20_000,
            )
        except PlaywrightTimeoutError:
            page.wait_for_timeout(self.wait_ms)
        except Exception:
            return False

        page.wait_for_timeout(self.wait_ms)
        self._assert_not_blocked(page)
        return page.locator(PRODUCT_SELECTOR).count() > 0

    def _collect_pages(self, page: Page, category: Category, location: Location) -> list[dict[str, Any]]:
        total_pages = self._total_pages(page)
        max_pages = total_pages if self.max_load_more <= 0 else min(total_pages, self.max_load_more)
        rows: list[dict[str, Any]] = []

        for page_number in range(1, max_pages + 1):
            if page_number > 1 and not self._go_to_page(page, page_number):
                break
            rows.extend(self._extract_cards(page, category, location))

        return rows

    def _warmup(self, page: Page) -> None:
        """Carga el storefront antes de entrar a una categoría cuando la sesión es nueva."""
        if not self.warmup_homepage:
            return

        try:
            cookies = page.context.cookies(BASE_URL)
        except Exception:
            cookies = []

        # Un perfil persistente con cookies vigentes ya fue calentado en una
        # ejecución anterior. Evitamos una carga extra innecesaria.
        if self.profile_dir is not None and cookies:
            return

        response = page.goto(
            BASE_URL,
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        self._assert_not_blocked(page, response.status if response else None)
        page.wait_for_timeout(max(self.wait_ms, 1_500))

    def _scrape_category_on_page(
        self,
        page: Page,
        category: Category,
        location: Location,
        *,
        warmup: bool,
    ) -> list[dict[str, Any]]:
        if warmup:
            self._warmup(page)

        response = page.goto(
            category.url,
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        status = response.status if response else None
        self._assert_not_blocked(page, status)
        page.wait_for_timeout(2_000)
        try:
            page.wait_for_selector(PRODUCT_SELECTOR, timeout=25_000)
        except PlaywrightTimeoutError:
            self._save_diagnostics(page, f"no_products_{category.id}")
            return []

        rows = self._collect_pages(page, category, location)
        self._save_diagnostics(page, f"success_{category.id}")
        self._clear_circuit()
        return rows

    def _open_context(self, playwright):
        launch_kwargs = {"headless": self.headless}
        if self.browser_channel:
            launch_kwargs["channel"] = self.browser_channel

        browser = None
        if self.profile_dir is not None:
            context: BrowserContext = playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                locale="es-MX",
                viewport={"width": 1440, "height": 1000},
                **launch_kwargs,
            )
            page = context.pages[0] if context.pages else context.new_page()
        else:
            browser = playwright.chromium.launch(**launch_kwargs)
            context = browser.new_context(
                locale="es-MX",
                viewport={"width": 1440, "height": 1000},
            )
            page = context.new_page()

        page.on("response", self._capture_grid_response)
        return browser, context, page

    def prepare_session(self, *, force_check: bool = False) -> dict[str, Any]:
        """Valida el homepage usando el perfil persistente sin abrir categorías."""
        remaining = self.circuit_remaining_seconds()
        if remaining > 0 and not force_check:
            return {
                "status": "DEFERRED",
                "remaining_seconds": remaining,
                "profile_dir": str(self.profile_dir) if self.profile_dir else None,
            }

        with sync_playwright() as p:
            browser, context, page = self._open_context(p)
            try:
                response = page.goto(
                    BASE_URL,
                    wait_until="domcontentloaded",
                    timeout=120_000,
                )
                status = response.status if response else None
                self._assert_not_blocked(page, status)
                page.wait_for_timeout(max(self.wait_ms, 1_500))
                self._clear_circuit()
                self._save_diagnostics(page, "session_ready")
                return {
                    "status": "READY",
                    "http_status": status,
                    "url": page.url,
                    "profile_dir": (
                        str(self.profile_dir) if self.profile_dir else None
                    ),
                }
            finally:
                context.close()
                if browser is not None:
                    browser.close()

    def scrape_category(
        self,
        category: Category,
        location: Location,
    ) -> list[dict[str, Any]]:
        self._assert_circuit_ready()

        with sync_playwright() as p:
            browser, context, page = self._open_context(p)
            try:
                return self._scrape_category_on_page(
                    page,
                    category,
                    location,
                    warmup=True,
                )
            finally:
                context.close()
                if browser is not None:
                    browser.close()

    def scrape_categories(
        self,
        categories: list[Category],
        location: Location,
        *,
        delay_seconds: int = 120,
        stop_after_block: bool = True,
    ) -> list[dict[str, Any]]:
        """Ejecuta varias categorías en una sola sesión Chrome.

        Cada resultado contiene category_id, status, rows y error. Después de
        un BLOCKED el circuito se abre y, por defecto, el resto de la tanda se
        marca DEFERRED sin nuevos requests al dominio.
        """
        results: list[dict[str, Any]] = []

        try:
            self._assert_circuit_ready()
        except SorianaDeferred as exc:
            for category in categories:
                results.append(
                    {
                        "category_id": category.id,
                        "status": "DEFERRED",
                        "rows": [],
                        "error": str(exc),
                    }
                )
            return results

        with sync_playwright() as p:
            browser, context, page = self._open_context(p)
            try:
                warmup = True
                blocked = False
                for index, category in enumerate(categories):
                    if blocked and stop_after_block:
                        results.append(
                            {
                                "category_id": category.id,
                                "status": "DEFERRED",
                                "rows": [],
                                "error": (
                                    "Diferido porque la sesión recibió BLOCKED "
                                    "en una categoría previa de la tanda."
                                ),
                            }
                        )
                        continue

                    try:
                        rows = self._scrape_category_on_page(
                            page,
                            category,
                            location,
                            warmup=warmup,
                        )
                        status = "SUCCESS" if rows else "EMPTY"
                        error = None
                    except SorianaBlocked as exc:
                        rows = []
                        status = "BLOCKED"
                        error = str(exc)
                        blocked = True
                    except Exception as exc:
                        rows = []
                        status = "ERROR"
                        error = f"{type(exc).__name__}: {exc}"

                    results.append(
                        {
                            "category_id": category.id,
                            "status": status,
                            "rows": rows,
                            "error": error,
                        }
                    )
                    warmup = False

                    if (
                        status == "SUCCESS"
                        and index < len(categories) - 1
                        and delay_seconds > 0
                    ):
                        page.wait_for_timeout(delay_seconds * 1000)
            finally:
                context.close()
                if browser is not None:
                    browser.close()

        return results
