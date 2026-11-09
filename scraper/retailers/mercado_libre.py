"""Adaptador de datos de Mercado Libre México (API autorizada, sin scraping HTML).

Las consultas a la API deben configurarse por separado según los permisos del
Programa de Desarrolladores de Mercado Libre. Este módulo NO extrae páginas web.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

RETAILER = "Mercado Libre"
SITE_ID = "MLM"


class MercadoLibreDataError(ValueError):
    """La respuesta no satisface el contrato mínimo de calidad."""


def _mxn_price(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        raise MercadoLibreDataError("Precio ausente")
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MercadoLibreDataError("Precio inválido") from exc
    if not price.is_finite() or price <= 0:
        raise MercadoLibreDataError("Precio debe ser positivo y finito")
    return float(price)


def normalize_api_item(
    item: dict[str, Any],
    *,
    category: str,
    category_id: str,
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Normaliza un item de API al contrato de columnas de main.COLUMNS.

    No infiere promociones, disponibilidad por CP, precio regular ni marca.
    Rechaza publicaciones sin ID, título, precio MXN o enlace válido.
    """
    if not isinstance(item, dict):
        raise MercadoLibreDataError("Item inválido")
    sku = str(item.get("id") or "").strip()
    title = str(item.get("title") or "").strip()
    currency = str(item.get("currency_id") or "").upper()
    url = str(item.get("permalink") or "").strip()
    parsed = urlparse(url)
    if not sku.startswith("MLM") or not sku[3:].isdigit():
        raise MercadoLibreDataError("ID de publicación MLM inválido")
    if not title or currency != "MXN":
        raise MercadoLibreDataError("Título ausente o moneda distinta de MXN")
    if parsed.scheme != "https" or not parsed.hostname or not (
        parsed.hostname == "mercadolibre.com.mx"
        or parsed.hostname.endswith(".mercadolibre.com.mx")
    ):
        raise MercadoLibreDataError("Enlace de publicación no válido")
    price = _mxn_price(item.get("price"))
    if not category.strip() or not category_id.strip():
        raise MercadoLibreDataError("Categoría requerida")
    attrs = item.get("attributes") or []
    brand = None
    if isinstance(attrs, list):
        for attr in attrs:
            if isinstance(attr, dict) and str(attr.get("id", "")).upper() == "BRAND":
                brand = attr.get("value_name") or None
                break

    # Solo disponibilidad de publicación reportada por la API; no inventario por CP.
    status = str(item.get("status") or "").lower()
    available = bool(item.get("available_quantity", 0) > 0) if (
        isinstance(item.get("available_quantity"), int)
        and not isinstance(item.get("available_quantity"), bool)
    ) else None
    return {
        "scrape_timestamp": captured_at or datetime.now(timezone.utc).isoformat(),
        "retailer": RETAILER,
        "city": None, "state": None, "postal_code": None,
        "store": None, "store_id": None,
        "department": None, "category": category,
        "subcategory": None, "sub_subcategory": None,
        "category_id": category_id, "sku": sku,
        "brand": brand, "product": title,
        "price_current": price, "price_regular": None, "promotion": None,
        "package_type": None, "units_per_package": None,
        "is_single_item": None, "price_per_unit": None,
        "single_item_presentation": None, "single_item_price": None,
        "availability_status": status or None, "is_available": available,
        "availability_raw": None, "pickup_available": None,
        "store_context_verified": False,
        "store_context_method": "API_PUBLICATION_NO_POSTAL_CONTEXT",
        "url": url, "price_raw": str(item["price"]),
    }


def normalize_api_results(
    items: list[dict[str, Any]], *, category: str, category_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Devuelve filas válidas y rechazadas; no declara la captura COMPLETE."""
    valid, rejected, seen = [], [], set()
    for index, item in enumerate(items):
        try:
            row = normalize_api_item(item, category=category, category_id=category_id)
            if row["sku"] in seen:
                raise MercadoLibreDataError("ID duplicado en la muestra")
            seen.add(row["sku"])
            valid.append(row)
        except (MercadoLibreDataError, TypeError) as exc:
            rejected.append({"index": str(index), "reason": str(exc)})
    return valid, rejected
