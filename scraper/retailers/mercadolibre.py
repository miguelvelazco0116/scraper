"""Public Mercado Libre Mexico search-listing parser (Phase 7 pilot).

Independent implementation informed by general architectural ideas from
PabloAlaniz/MercadoLibre-Scraper. No upstream code or dashboard copied.
Does not bypass access controls, log in, fetch private APIs, or infer SKU.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

SEARCH_HOST = "listado.mercadolibre.com.mx"
PUBLIC_HOST_SUFFIX = ".mercadolibre.com.mx"
BASE_URL = "https://" + SEARCH_HOST + "/"
CARD_SELECTORS = (
    "li.ui-search-layout__item, li.poly-card, div.poly-card, "
    "li.ui-search-result__wrapper, div.ui-search-result__wrapper"
)
PRICE_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?")
PUBLICATION_RE = re.compile(r"(?:^|[/_-])(MLM-?\d{6,})(?:[/_-]|$)", re.I)
CATALOG_RE = re.compile(r"/p/(MLM\d{6,})(?:/|$)", re.I)
TOTAL_RE = re.compile(r"([\d,.]+)\s+resultados?", re.I)
BLOCK_TERMS = ("captcha", "access denied", "verify you are human")


class MercadoLibreCaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchPage:
    listings: list[dict]
    next_url: str | None
    reported_total: int | None
    explicit_end: bool


def public_host(host: str | None) -> bool:
    host = str(host or "").lower()
    return host == "mercadolibre.com.mx" or host.endswith(PUBLIC_HOST_SUFFIX)


def clean_link(link: str, base: str) -> str | None:
    """Return a public, HTTPS marketplace URL stripped of tracking fragments.

    Do not replace the publication URL with a title/slug inference.
    """
    try:
        parsed = urlparse(urljoin(base, link))
    except ValueError:
        return None
    if parsed.scheme != "https" or not public_host(parsed.hostname):
        return None
    if not parsed.path or parsed.path == "/":
        return None
    return urlunparse((
        parsed.scheme, parsed.netloc.lower(),
        parsed.path.rstrip("/"), "", "", "",
    ))


def listing_identity(url: str) -> tuple[str | None, str | None]:
    """A publication MLM ID is NOT a product SKU or catalog product ID."""
    parsed = urlparse(url)
    catalog = CATALOG_RE.search(parsed.path)
    if catalog:
        return None, catalog.group(1).upper()
    publication = PUBLICATION_RE.search(parsed.path)
    if publication:
        return publication.group(1).upper().replace("-", ""), None
    return None, None


def parse_mxn(text: str) -> float | None:
    """Parse displayed Mexican amounts, e.g. $1,299.50 or 250.

    Receives text from one price element, not a whole card with multiple
    unrelated numeric quantities. No currency conversion.
    """
    value = str(text or "").replace("\xa0", " ").strip()
    match = PRICE_RE.search(value)
    if not match:
        return None
    try:
        amount = Decimal(match.group(1).replace(",", ""))
        if match.group(2):
            amount += Decimal("0." + match.group(2).ljust(2, "0"))
        return float(amount) if amount > 0 else None
    except InvalidOperation:
        return None


def displayed_money(container) -> float | None:
    """Get amount from one Andes money widget, including its cents."""
    if container is None:
        return None
    fraction = container.select_one(".andes-money-amount__fraction")
    if fraction is None and "andes-money-amount__fraction" in (
        container.get("class") or []
    ):
        fraction = container
    if fraction is None:
        return None
    value = parse_mxn(fraction.get_text(" ", strip=True))
    if value is None:
        return None
    cents = container.select_one(".andes-money-amount__cents")
    if cents is not None:
        token = re.search(r"\d{1,2}", cents.get_text(strip=True))
        if token:
            value = int(value) + int(token.group().ljust(2, "0")) / 100
    return value


def extract_card(card, page_url: str) -> dict | None:
    title_node = card.select_one(
        ".poly-component__title, .poly-component__title-wrapper, "
        "h2.poly-box, h2, h3"
    )
    if title_node is None:
        return None
    title = title_node.get_text(" ", strip=True)
    if not title:
        return None
    anchors = card.select("a[href]")
    link = None
    for anchor in anchors:
        href = clean_link(anchor.get("href", ""), page_url)
        if href and (
            "poly-component__title" in " ".join(anchor.get("class", []))
            or title_node in anchor.descendants
            or anchor in title_node.parents
        ):
            link = href
            break
    if link is None:
        for anchor in anchors:
            href = clean_link(anchor.get("href", ""), page_url)
            if href and (
                "/p/MLM" in href or "MLM-" in href.upper()
                or "articulo.mercadolibre.com.mx" in href
            ):
                link = href
                break
    if link is None:
        return None

    current_box = card.select_one(
        ".poly-price__current, .ui-search-price__part--medium, "
        ".price-tag-fraction"
    )
    current = displayed_money(current_box)
    if current is None:
        # A legacy listing might not wrap the current price in
        # poly-price__current. Skip any Andes money widget inside a
        # strikethrough/original-price container; never mislabel the
        # previous price as current.
        for widget in card.select(".andes-money-amount"):
            if (widget.find_parent(class_="poly-price__original")
                    or widget.find_parent(
                        class_="ui-search-price__original-value"
                    )):
                continue
            current = displayed_money(widget)
            if current is not None:
                break
    if current is None:
        return None
    previous = displayed_money(card.select_one(
        ".poly-price__original, .ui-search-price__original-value"
    ))
    if previous is not None and previous <= current:
        previous = None
    discount_node = card.select_one(
        ".poly-price__discount, .ui-search-price__discount"
    )
    discount = discount_node.get_text(" ", strip=True) if discount_node else ""
    publication_id, catalog_product_id = listing_identity(link)
    sponsor = bool(re.search(
        r"\b(patrocinad[oa]|sponsored)\b",
        card.get_text(" ", strip=True), re.I
    ))
    seller = card.select_one(
        ".poly-component__seller, .ui-search-official-store-label"
    )
    return {
        "publication_id": publication_id,
        "catalog_product_id": catalog_product_id,
        "product": title,
        "price_current": current,
        "price_regular": previous,
        "promotion": discount or ("Precio promocional" if previous else None),
        "seller": seller.get_text(" ", strip=True) if seller else None,
        "sponsored": sponsor,
        "currency": "MXN",
        "url": link,
    }


def extract_next_link(soup: BeautifulSoup, page_url: str) -> tuple[str | None, bool]:
    buttons = soup.select(
        "li.andes-pagination__button--next, "
        ".andes-pagination__button--next, [data-testid='pagination-next']"
    )
    for button in buttons:
        classes = " ".join(button.get("class", []))
        if ("disabled" in classes or button.get("aria-disabled") == "true"
                or button.select_one("button[disabled],a[aria-disabled='true']")):
            return None, True
        anchor = button.select_one("a[href]")
        if anchor:
            candidate = urljoin(page_url, anchor["href"])
            parsed = urlparse(candidate)
            if parsed.scheme == "https" and parsed.hostname == SEARCH_HOST:
                return candidate, False
            raise MercadoLibreCaptureError(
                "Siguiente página redirige fuera de listado Mercado Libre México"
            )
    anchor = soup.select_one("a[rel='next'][href]")
    if anchor:
        candidate = urljoin(page_url, anchor["href"])
        parsed = urlparse(candidate)
        if parsed.scheme == "https" and parsed.hostname == SEARCH_HOST:
            return candidate, False
        raise MercadoLibreCaptureError("Paginación ajena a listado de México")
    # No next link is NOT proof that the published inventory is complete.
    return None, False


def parse_search_page(html: str, page_url: str) -> SearchPage:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(" ", strip=True) if soup.title else "").lower()
    if any(marker in title for marker in BLOCK_TERMS):
        raise MercadoLibreCaptureError("Página de bloqueo/captcha detectada")
    cards = soup.select(CARD_SELECTORS)
    unique: dict[str, dict] = {}
    for card in cards:
        item = extract_card(card, page_url)
        if item is not None:
            key = item["publication_id"] or item["catalog_product_id"] or item["url"]
            unique.setdefault(key, item)
    result_count = soup.select_one(
        ".ui-search-search-result__quantity-results, "
        ".poly-search__results-count, [data-testid='results-count']"
    )
    text_count = result_count.get_text(" ", strip=True) if result_count else ""
    match = TOTAL_RE.search(text_count)
    if match is None:
        match = re.search(r"\b([\d,]+)\s+productos?\b", text_count, re.I)
    reported = (
        int(match.group(1).replace(",", "").replace(".", ""))
        if match else None
    )
    next_url, explicit_end = extract_next_link(soup, page_url)
    return SearchPage(
        listings=list(unique.values()), next_url=next_url,
        reported_total=reported, explicit_end=explicit_end,
    )


def search_url(query: str) -> str:
    query = " ".join(str(query).strip().split())
    if not query or len(query) > 120:
        raise ValueError("La búsqueda debe contener de 1 a 120 caracteres")
    # Reference project uses searchable title paths; no private endpoints.
    return BASE_URL + quote(query.replace(" ", "-"), safe="-")


def run_search(
    query: str, *, max_pages: int = 3, max_results: int = 100,
    delay_seconds: float = 1.5, session: requests.Session | None = None,
) -> tuple[list[dict], dict]:
    """Sequential public HTML search. No bypass or automated rate evasion.

    An HTTP error/403/429, inaccessible page, duplicate page or missing
    verifiable next link stops the run as PARTIAL, never complete.
    """
    if not 1 <= max_pages <= 50:
        raise ValueError("max_pages debe estar entre 1 y 50")
    if not 1 <= max_results <= 2000:
        raise ValueError("max_results debe estar entre 1 y 2000")
    if not 1.0 <= delay_seconds <= 30:
        raise ValueError("delay_seconds debe estar entre 1 y 30")
    client = session if session is not None else requests.Session()
    url = search_url(query)
    visited: set[str] = set()
    items: dict[str, dict] = {}
    page_stats: list[dict] = []
    reported_total: int | None = None
    status, reason = "PARTIAL", "not_started"
    try:
        for index in range(1, max_pages + 1):
            if url in visited:
                reason = "repeated_pagination_url"
                break
            visited.add(url)
            try:
                response = client.get(url, timeout=30)
                response.raise_for_status()
                final = urlparse(response.url)
                if final.scheme != "https" or final.hostname != SEARCH_HOST:
                    raise MercadoLibreCaptureError(
                        "Redirección fuera del buscador público mexicano"
                    )
                page = parse_search_page(response.text, response.url)
            except (requests.RequestException, MercadoLibreCaptureError) as exc:
                reason = "http_or_page_error"
                page_stats.append({
                    "page": index, "url": url,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:180],
                })
                break
            if reported_total is not None and page.reported_total not in (
                None, reported_total
            ):
                reason = "published_total_changed"
                break
            reported_total = reported_total or page.reported_total
            new_count = 0
            for item in page.listings:
                key = (
                    item["publication_id"] or item["catalog_product_id"]
                    or item["url"]
                )
                if key not in items:
                    items[key] = item
                    new_count += 1
                    if len(items) >= max_results:
                        break
            page_stats.append({
                "page": index, "url": url,
                "listed": len(page.listings), "new": new_count,
                "cumulative": len(items),
                "has_next": bool(page.next_url),
                "explicit_end": page.explicit_end,
            })
            if new_count == 0:
                reason = "no_new_products"
                break
            if len(items) >= max_results:
                reason = "user_limit_reached"
                break
            if page.explicit_end:
                status, reason = "COMPLETE", "verified_last_page"
                break
            if page.next_url is None:
                reason = "next_page_not_verifiable"
                break
            url = page.next_url
            if index == max_pages:
                reason = "max_pages_reached"
            else:
                time.sleep(delay_seconds)
        else:
            reason = "max_pages_reached"
    finally:
        if session is None:
            client.close()
    if reported_total is not None and len(items) != reported_total:
        # The displayed search count is informational: it might include
        # ads or changing rankings, so never invent missing listings.
        if status == "COMPLETE":
            status, reason = "PARTIAL", "listed_count_differs_from_total"
    if reported_total is None and status == "COMPLETE":
        status, reason = "PARTIAL", "published_total_unknown"
    return list(items.values()), {
        "query": query, "status": status, "reason": reason,
        "collected": len(items), "reported_total": reported_total,
        "pages_checked": len(page_stats), "pages": page_stats,
        "source": "mercadolibre_mx_public_search_html",
        "location_verified": False,
        "sku_verified": False,
    }
