"""Adaptación MX del scraper de PabloAlaniz/MercadoLibre-Scraper.

Implementa la misma separación entre listado, detalle y paginación HTML,
sin dependencias gráficas, sólo para México. No ejecuta bypass de bloqueos.
Antes de efectuar peticiones se debe confirmar permiso de acceso y uso.
Fuente de diseño: https://github.com/PabloAlaniz/MercadoLibre-Scraper
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup

BASE_URL = "https://listado.mercadolibre.com.mx/"
PAGE_INCREMENT = 50


class MercadoLibrePageError(ValueError):
    pass


@dataclass(frozen=True)
class ProductListing:
    title: str
    price: Decimal
    post_link: str
    image_link: str | None = None


@dataclass(frozen=True)
class ProductDetail:
    title: str | None
    price: Decimal | None
    author: str | None
    link: str | None
    shipping: str | None


def parse_mxn(text: str | None) -> Decimal | None:
    """Parse $1,234.50 or 1.234,50 using explicit decimal separator rules."""
    if not text:
        return None
    clean = re.sub(r"[^0-9,.]", "", text)
    if not clean:
        return None
    if "." in clean and "," in clean:
        decimal_sep = "." if clean.rfind(".") > clean.rfind(",") else ","
        group_sep = "," if decimal_sep == "." else "."
        clean = clean.replace(group_sep, "").replace(decimal_sep, ".")
    elif "," in clean:
        parts = clean.split(",")
        clean = clean.replace(",", ".") if len(parts[-1]) in (1, 2) else clean.replace(",", "")
    elif "." in clean:
        parts = clean.split(".")
        clean = clean.replace(".", "") if len(parts) > 1 and len(parts[-1]) == 3 else clean
    try:
        value = Decimal(clean)
    except InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None


def _text(node) -> str | None:
    return node.get_text(" ", strip=True) if node else None


def _valid_link(href: str | None) -> str | None:
    if not href:
        return None
    url = urljoin(BASE_URL, href)
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    if parsed.hostname != "mercadolibre.com.mx" and not parsed.hostname.endswith(".mercadolibre.com.mx"):
        return None
    return url


class MercadoLibreScraper:
    """Interfaz inspirada en Pablo Alaniz; sin UI ni acceso automático a red."""

    base_url = BASE_URL
    page_increment = PAGE_INCREMENT

    @staticmethod
    def search_url(product_name: str, page_number: int = 1) -> str:
        if not product_name.strip() or page_number < 1:
            raise MercadoLibrePageError("Consulta o página inválida")
        slug = quote("-".join(product_name.strip().split()), safe="-")
        base = BASE_URL + slug
        return base if page_number == 1 else f"{base}_Desde_{(page_number - 1) * PAGE_INCREMENT + 1}_NoIndex_True"

    @staticmethod
    def get_total_results(soup: BeautifulSoup) -> int | None:
        el = soup.select_one(".ui-search-search-result__quantity-results")
        if not el:
            return None
        raw = re.search(r"[\d.,]+", _text(el) or "")
        if not raw:
            return None
        return int(re.sub(r"[^0-9]", "", raw.group()))

    @staticmethod
    def scrape_page_results(soup: BeautifulSoup) -> list[ProductListing]:
        cards = soup.select("li.ui-search-layout__item")
        if not cards:
            # No inventar una captura completa cuando cambió el HTML.
            raise MercadoLibrePageError("Sin tarjetas; revisar respuesta, selectores o bloqueo")
        result = []
        for card in cards:
            title = _text(card.select_one("h2"))
            link = card.select_one("a[href]")
            fraction = _text(card.select_one(".andes-money-amount__fraction"))
            cents = _text(card.select_one(".andes-money-amount__cents"))
            href = _valid_link(link.get("href") if link else None)
            price = parse_mxn(f"{fraction}.{cents}" if fraction and cents else fraction)
            img = card.select_one("img")
            image = (img.get("data-src") or img.get("src")) if img else None
            if title and price is not None and href:
                result.append(ProductListing(title, price, href, image))
        if not result:
            raise MercadoLibrePageError("Tarjetas presentes pero sin registros completos")
        return result

    @staticmethod
    def scrape_product_details(soup: BeautifulSoup) -> ProductDetail:
        canonical = soup.select_one("link[rel='canonical']")
        price = _text(soup.select_one(".andes-money-amount__fraction"))
        cents = _text(soup.select_one(".andes-money-amount__cents"))
        return ProductDetail(
            title=_text(soup.select_one("h1.ui-pdp-title")),
            price=parse_mxn(f"{price}.{cents}" if price and cents else price),
            author=_text(soup.select_one(".ui-pdp-seller-validated")),
            link=_valid_link(canonical.get("href") if canonical else None),
            shipping=_text(soup.find(string=re.compile("Llega"))) if soup.find(string=re.compile("Llega")) else None,
        )

    @classmethod
    def parse_pages(cls, html_pages: list[str], *, requested_limit: int) -> list[ProductListing]:
        """Consume páginas YA OBTENIDAS por un medio autorizado."""
        if requested_limit < 1:
            raise MercadoLibrePageError("Límite inválido")
        products: list[ProductListing] = []
        seen: set[str] = set()
        for html in html_pages:
            page = BeautifulSoup(html, "html.parser")
            for row in cls.scrape_page_results(page):
                if row.post_link not in seen:
                    seen.add(row.post_link)
                    products.append(row)
                if len(products) >= requested_limit:
                    return products
        return products
