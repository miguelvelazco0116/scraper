from __future__ import annotations

import argparse
import sys
from copy import copy
from pathlib import Path
from urllib.parse import quote, urljoin

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from playwright.sync_api import sync_playwright

from main import COLUMNS, CONSOLIDATED_PATH, update_consolidated_output
from scraper.config import load_categories, load_locations
from scraper.retailers.farmacias_san_pablo import (
    FarmaciasSanPabloBlocked,
    FarmaciasSanPabloNetworkUnavailable,
    FarmaciasSanPabloScraper,
)


OUTPUT_PATH = ROOT / "output" / "farmacias_san_pablo_test.xlsx"


class _ElementProxy:
    def __init__(self, locator):
        self._locator = locator

    @property
    def text(self) -> str:
        try:
            return self._locator.inner_text(timeout=8_000)
        except Exception:
            return ""


class _SwitchToProxy:
    def window(self, _handle) -> None:
        return None


class _PlaywrightDriverAdapter:
    """Small Selenium-compatible facade over one Playwright Page.

    FarmaciasSanPabloScraper keeps its already-worked OCC/DOM extraction
    methods. Only the browser transport changes to the same CDP connection
    pattern used by Bodega Aurrera and Walmart.
    """

    def __init__(self, page):
        self.page = page
        self.switch_to = _SwitchToProxy()
        self.occ_fallback_reason: str | None = None
        self._occ_responses = []
        self.page.on("response", self._capture_occ_response)

    def _capture_occ_response(self, response) -> None:
        url = str(getattr(response, "url", "") or "")
        if "/rest/v2/fsp/products/search-sponsored" in url:
            self._occ_responses.append(response)

    def read_occ_payload(self) -> tuple[dict, str] | None:
        for response in reversed(self._occ_responses):
            try:
                payload = response.json()
            except Exception:
                continue
            if isinstance(payload, dict):
                return payload, str(response.url or "")
        return None

    @property
    def current_url(self) -> str:
        return self.page.url or ""

    @property
    def title(self) -> str:
        try:
            return self.page.title()
        except Exception:
            return ""

    @property
    def window_handles(self) -> list[str]:
        return ["current"]

    def get(self, url: str) -> None:
        self._occ_responses = []
        self.page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        # OCC is loaded asynchronously after the storefront DOM.
        self.page.wait_for_timeout(1_500)

    def find_element(self, by, value):
        by_text = str(by or "").casefold()
        if "tag name" in by_text:
            locator = self.page.locator(str(value)).first
        else:
            locator = self.page.locator(str(value)).first
        locator.wait_for(state="attached", timeout=30_000)
        return _ElementProxy(locator)

    def execute_script(self, script: str):
        wrapped = "() => {\n" + str(script) + "\n}"
        return self.page.evaluate(wrapped)

    def fetch_json_in_browser(self, url: str) -> dict:
        """Fetch cross-origin OCC data through the user-owned Chrome page.

        This keeps the request inside Chrome's network stack instead of
        replaying it with urllib, which can be rejected by Akamai even when
        the same browser session can access the storefront.
        """
        return self.page.evaluate(
            """
            async (url) => {
              try {
                const response = await fetch(url, {
                  method: 'GET',
                  credentials: 'include',
                  headers: {
                    'Accept': 'application/json, text/plain, */*'
                  },
                  redirect: 'follow'
                });

                const text = await response.text();
                let data = null;
                try {
                  data = JSON.parse(text);
                } catch (_) {}

                return {
                  ok: response.ok,
                  status: response.status,
                  statusText: response.statusText || '',
                  url: response.url || url,
                  data,
                  textPreview: text.slice(0, 1200),
                  error: ''
                };
              } catch (error) {
                return {
                  ok: false,
                  status: 0,
                  statusText: '',
                  url,
                  data: null,
                  textPreview: '',
                  error: String(error && (error.stack || error.message) || error)
                };
              }
            }
            """,
            url,
        ) or {}

    def get_cookies(self) -> list[dict]:
        try:
            return self.page.context.cookies()
        except Exception:
            return []

    def save_screenshot(self, path: str) -> bool:
        try:
            self.page.screenshot(path=path, full_page=True)
            return True
        except Exception:
            return False


class _BrowserOccSanPabloScraper(FarmaciasSanPabloScraper):
    """Read the OCC payload already loaded by the real Chrome storefront.

    No second HTTP client is used. Each storefront page is opened normally
    and the JSON response generated by that navigation is read from the
    Playwright/CDP response object. This preserves the validated OCC parser
    while avoiding Akamai 403 and DOM recommendation contamination.
    """

    @classmethod
    def _discover_occ_products(
        cls,
        driver,
        category,
        max_pages: int,
    ):
        products: dict[str, dict] = {}
        pages: list[dict] = []
        target: int | None = None
        total_pages: int | None = None
        search_fallback_url = None
        active_storefront_url = category.url

        for page_index in range(max_pages):
            storefront_url = cls._page_url(active_storefront_url, page_index)
            cls._goto(driver, storefront_url)
            captured = driver.read_occ_payload()

            if captured is None and page_index == 0:
                query = (
                    clean_text(category.subcategory)
                    or clean_text(category.name)
                    or clean_text(category.id)
                    or ""
                )
                if query:
                    search_fallback_url = urljoin(
                        "https://www.farmaciasanpablo.com.mx/",
                        "search/" + quote(query, safe=""),
                    )
                    active_storefront_url = search_fallback_url
                    cls._goto(driver, search_fallback_url)
                    captured = driver.read_occ_payload()

            if captured is None:
                driver.occ_fallback_reason = (
                    f"No se capturó respuesta OCC en {storefront_url}"
                )
                print(
                    f"OCC_CAPTURE_FALLBACK [{category.id}]: "
                    f"{driver.occ_fallback_reason}"
                )
                return None

            payload, api_url = captured
            pagination = payload.get("pagination")
            if isinstance(pagination, dict):
                try:
                    raw_target = pagination.get("totalResults")
                    if raw_target is not None:
                        target = int(raw_target)
                except (TypeError, ValueError):
                    pass
                try:
                    raw_pages = pagination.get("totalPages")
                    if raw_pages is not None:
                        total_pages = int(raw_pages)
                except (TypeError, ValueError):
                    pass

            page_products = payload.get("products")
            if not isinstance(page_products, list):
                page_products = []

            before = len(products)
            for product in page_products:
                if not isinstance(product, dict):
                    continue
                code = clean_text(str(product.get("code") or ""))
                href = clean_text(str(product.get("url") or ""))
                name = clean_text(str(product.get("name") or ""))
                key = code or href or name
                if key:
                    products[key] = product

            pages.append(
                {
                    "page": page_index + 1,
                    "currentPage": page_index,
                    "storefront_url": storefront_url,
                    "api_url": api_url,
                    "products_on_page": len(page_products),
                    "new_products": len(products) - before,
                    "cumulative_products": len(products),
                    "target_products": target,
                    "total_pages": total_pages,
                }
            )

            print(
                f"San Pablo OCC [{category.id}] page={page_index + 1}: "
                f"products={len(page_products)}, cumulative={len(products)}, "
                f"target={target}, total_pages={total_pages}"
            )

            if total_pages is not None and page_index + 1 >= total_pages:
                break
            if target is not None and len(products) >= target:
                break
            if not page_products:
                break

        return list(products.values()), {
            "source": "occ_search_sponsored",
            "category_url": category.url,
            "search_fallback_url": search_fallback_url,
            "target_products": target,
            "total_pages": total_pages,
            "products_discovered": len(products),
            "pages": pages,
            "occ_transport": "captured_storefront_response",
        }

    def scrape_category_on_driver(
        self,
        driver,
        category,
        location,
    ) -> list[dict]:
        driver.occ_fallback_reason = None
        rows = super().scrape_category_on_driver(
            driver,
            category,
            location,
        )

        meta = dict(self.last_meta or {})
        fallback_reason = getattr(driver, "occ_fallback_reason", None)

        if fallback_reason:
            meta["occ_transport"] = "dom_fallback_after_occ_capture"
            meta["occ_fallback_reason"] = fallback_reason
            meta["engine"] = "Playwright CDP + DOM fallback"
        elif meta.get("source") == "occ_search_sponsored":
            meta["occ_transport"] = "captured_storefront_response"
            meta["engine"] = "Playwright CDP + captured OCC response"
        else:
            meta["occ_transport"] = "dom_no_occ_resource"
            meta["engine"] = "Playwright CDP + DOM"

        self.last_meta = meta
        self._write_diagnostics(driver, category, meta)
        return rows

def _find_san_pablo_page(browser):
    candidates = []
    for context in browser.contexts:
        for page in context.pages:
            if "farmaciasanpablo.com.mx" in (page.url or "").casefold():
                candidates.append(page)
    return candidates[-1] if candidates else None


def _write_test_output(
    rows: list[dict],
    summaries: list[dict],
    output_path: Path = OUTPUT_PATH,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(rows, columns=COLUMNS)
    summary = pd.DataFrame(summaries)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Concentrado")
        summary.to_excel(writer, index=False, sheet_name="Resumen")

        workbook = writer.book
        for sheet_name in ("Concentrado", "Resumen"):
            ws = workbook[sheet_name]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                font = copy(cell.font)
                font.bold = True
                cell.font = font

            for col_cells in ws.columns:
                values = [
                    str(cell.value) if cell.value is not None else ""
                    for cell in col_cells[:200]
                ]
                width = min(
                    max(max((len(value) for value in values), default=0) + 2, 10),
                    42,
                )
                ws.column_dimensions[col_cells[0].column_letter].width = width


def _safe_for_global(df: pd.DataFrame) -> pd.DataFrame:
    """Conserva productos útiles para pricing aunque no expongan SKU/URL."""
    if df.empty:
        return df
    has_product = df["product"].fillna("").astype(str).str.strip().ne("")
    has_price = df["price_current"].notna()
    return df.loc[has_product & has_price].copy()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test completo Farmacias San Pablo con Chrome"
    )
    parser.add_argument("--category", default="all")
    parser.add_argument("--location", default="san-pablo-online")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument(
        "--cdp-url",
        required=True,
        help="WebSocket CDP del Chrome existente.",
    )
    args = parser.parse_args()

    categories = load_categories(
        ROOT / "config" / "farmacias-san-pablo" / "categories.yaml"
    )
    if args.category != "all":
        categories = [x for x in categories if x.id == args.category]
    if not categories:
        raise SystemExit(f"Categoría no encontrada: {args.category}")

    locations = {x.id: x for x in load_locations(ROOT / "config" / "locations.yaml")}
    location = locations.get(args.location)
    if location is None:
        raise SystemExit(f"Ubicación no encontrada: {args.location}")

    all_rows: list[dict] = []
    summaries: list[dict] = []

    scraper = _BrowserOccSanPabloScraper(
        headless=False,
        max_pages=args.max_pages,
    )

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.connect_over_cdp(args.cdp_url)
    except Exception as exc:
        playwright.stop()
        print("ERROR: no fue posible conectarse al Chrome abierto por CDP.")
        print(f"{type(exc).__name__}: {exc}")
        return 10

    page = _find_san_pablo_page(browser)
    if page is None:
        playwright.stop()
        print(
            "ERROR: abre una pestaña de Farmacias San Pablo "
            "antes de ejecutar el test."
        )
        return 11

    driver = _PlaywrightDriverAdapter(page)

    print("=" * 68)
    print("FARMACIAS SAN PABLO - TEST COMPLETO")
    print("=" * 68)
    print(f"Categorías : {len(categories)}")
    print(f"Salida     : {OUTPUT_PATH}")
    print(f"Consolidado: {CONSOLIDATED_PATH}")
    print("Chrome     : SESION EXISTENTE POR CDP")
    print(f"CDP        : {args.cdp_url}")
    print("OCC        : CAPTURA DE RESPUESTA REAL DEL STOREFRONT + FALLBACK DOM")
    print("El script NO abre ni cierra Chrome.")
    print("")

    for index, category in enumerate(categories, start=1):
        print("-" * 68)
        print(f"[{index}/{len(categories)}] {category.id}")
        print(category.url)
        print("-" * 68)

        status = "SUCCESS"
        error = None
        rows: list[dict] = []

        try:
            rows = scraper.scrape_category_on_driver(
                driver,
                category,
                location,
            )
            meta = scraper.last_meta or {}
            status = str(meta.get("status") or ("SUCCESS" if rows else "EMPTY"))
        except FarmaciasSanPabloBlocked as exc:
            meta = scraper.last_meta or {}
            status = "BLOCKED"
            error = str(exc)
            print(f"BLOCKED [{category.id}]: {exc}")
        except FarmaciasSanPabloNetworkUnavailable as exc:
            meta = scraper.last_meta or {}
            status = "NETWORK_UNAVAILABLE"
            error = str(exc)
            print(f"NETWORK_UNAVAILABLE [{category.id}]: {exc}")
        except Exception as exc:
            meta = scraper.last_meta or {}
            status = "ERROR"
            error = f"{type(exc).__name__}: {exc}"
            print(f"ERROR [{category.id}]: {error}")

        for row in rows:
            row["department"] = category.department
            row["category"] = category.name
            row["subcategory"] = category.subcategory
            row["sub_subcategory"] = category.sub_subcategory
            row["category_id"] = category.id

        df = pd.DataFrame(rows, columns=COLUMNS)
        if not df.empty:
            df = df.drop_duplicates(
                subset=["category_id", "sku", "url", "product", "price_current"],
                keep="last",
            ).reset_index(drop=True)
            all_rows.extend(df.to_dict("records"))

            safe = _safe_for_global(df)
            if not safe.empty:
                update_consolidated_output(safe, ROOT / CONSOLIDATED_PATH)

        target = meta.get("target_products")
        products = len(df)

        unique_skus = int(
            df.loc[df["sku"].notna(), "sku"].astype(str).nunique()
        ) if not df.empty else 0
        unique_urls = int(
            df.loc[df["url"].notna(), "url"].astype(str).nunique()
        ) if not df.empty else 0
        price_complete = int(df["price_current"].notna().sum()) if not df.empty else 0
        missing_identifier = int(
            (
                (df["sku"].isna() | df["sku"].astype(str).str.strip().eq(""))
                & (df["url"].isna() | df["url"].astype(str).str.strip().eq(""))
            ).sum()
        ) if not df.empty else 0

        sku_complete = int(
            df["sku"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not df.empty else 0
        url_complete = int(
            df["url"].fillna("").astype(str).str.strip().ne("").sum()
        ) if not df.empty else 0

        # Preserve real network/blocking errors. Only derive data-quality status
        # when navigation/extraction itself completed.
        terminal_failure = status in {
            "BLOCKED",
            "NETWORK_UNAVAILABLE",
            "ERROR",
        }
        if not terminal_failure:
            # Para San Pablo, el objetivo operativo es pricing/promoción.
            # SKU y URL se reportan como métricas informativas, no bloqueantes.
            if products == 0:
                status = "EMPTY"
            elif price_complete < products:
                status = "PARTIAL"
            elif target is not None and products != int(target):
                # Under- and over-shoot are both failures when San Pablo
                # publishes an authoritative category total.
                status = "PARTIAL"
            else:
                status = "SUCCESS"

        summaries.append(
            {
                "category_id": category.id,
                "target_products": target,
                "products_extracted": products,
                "unique_skus": unique_skus,
                "unique_urls": unique_urls,
                "sku_complete": sku_complete,
                "url_complete": url_complete,
                "price_complete": price_complete,
                "missing_identifier": missing_identifier,
                "source": meta.get("source"),
                "engine": meta.get("engine"),
                "occ_transport": meta.get("occ_transport"),
                "occ_fallback_reason": meta.get("occ_fallback_reason"),
                "status": status,
                "error": error,
            }
        )

        _write_test_output(all_rows, summaries)

        print(
            f"RESULT [{category.id}]: status={status} "
            f"target={target} products={products} "
            f"sku={sku_complete}/{products} url={url_complete}/{products} "
            f"price={price_complete}/{products} "
            f"missing_id={missing_identifier} "
            f"transport={meta.get('occ_transport') or '-'}"
        )
        print(f"Output actualizado: {OUTPUT_PATH}")
        print("")

    print("=" * 68)
    print("RESUMEN FARMACIAS SAN PABLO")
    print("=" * 68)
    for item in summaries:
        print(
            f"{item['category_id']}: {item['status']} "
            f"({item['products_extracted']}/{item['target_products']}) "
            f"transport={item.get('occ_transport') or '-'}"
        )

    print("")
    print(f"Archivo de prueba : {OUTPUT_PATH}")
    print(f"Consolidado global: {ROOT / CONSOLIDATED_PATH}")

    successful = all(
        item["status"] == "SUCCESS"
        and int(item["price_complete"]) == int(item["products_extracted"])
        for item in summaries
    )

    # We only stop the Playwright client. We never close the user-owned Chrome.
    playwright.stop()
    return 0 if successful else 2


if __name__ == "__main__":
    raise SystemExit(main())
