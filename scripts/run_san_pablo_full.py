from __future__ import annotations

import argparse
import sys
from copy import copy
from pathlib import Path

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
        self.page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=120_000,
        )

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
    """San Pablo OCC hardening for an existing Chrome/CDP session.

    The catalog parser remains unchanged. Only the OCC transport is replaced
    with an in-browser fetch. If OCC cannot be read, the existing DOM catalog
    extractor is used instead of ending the category as EMPTY.
    """

    @staticmethod
    def _fetch_occ_json(driver, url: str) -> dict:
        fetcher = getattr(driver, "fetch_json_in_browser", None)
        if not callable(fetcher):
            return FarmaciasSanPabloScraper._fetch_occ_json(driver, url)

        result = fetcher(url) or {}
        status = int(result.get("status") or 0)
        payload = result.get("data")

        if 200 <= status < 300 and isinstance(payload, dict):
            return payload

        detail = (
            str(result.get("error") or "").strip()
            or str(result.get("textPreview") or "").strip()
            or str(result.get("statusText") or "").strip()
            or "sin detalle"
        )
        raise FarmaciasSanPabloNetworkUnavailable(
            "OCC search-sponsored falló dentro de Chrome "
            f"(HTTP {status or 'n/a'}): {detail[:500]}"
        )

    @classmethod
    def _discover_occ_products(
        cls,
        driver,
        category,
        max_pages: int,
    ):
        try:
            result = super()._discover_occ_products(
                driver,
                category,
                max_pages,
            )
        except FarmaciasSanPabloNetworkUnavailable as exc:
            driver.occ_fallback_reason = str(exc)
            print(
                f"OCC_BROWSER_FALLBACK [{category.id}]: "
                "se usará el catálogo DOM de la misma pestaña. "
                f"Motivo: {exc}"
            )
            return None

        if result is not None:
            products, _meta = result
            if not products:
                driver.occ_fallback_reason = (
                    "OCC respondió sin productos; se usa fallback DOM."
                )
                print(
                    f"OCC_BROWSER_FALLBACK [{category.id}]: "
                    "OCC respondió 0 productos; se usará catálogo DOM."
                )
                return None

        return result

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
            meta["occ_transport"] = "dom_fallback_after_browser_occ"
            meta["occ_fallback_reason"] = fallback_reason
            meta["engine"] = "Playwright CDP + DOM fallback"
        elif meta.get("source") == "occ_search_sponsored":
            meta["occ_transport"] = "chrome_page_fetch"
            meta["engine"] = "Playwright CDP + OCC browser fetch"
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
    print("OCC        : FETCH DENTRO DE CHROME + FALLBACK DOM")
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
            elif target is not None and products < int(target):
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
