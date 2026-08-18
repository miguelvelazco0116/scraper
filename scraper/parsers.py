from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

DEFAULT_BASE_URL = "https://www.soriana.com/"


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def parse_money(value: str | None) -> float | None:
    if not value:
        return None
    normalized = value.replace(",", "")
    match = re.search(r"-?\d+(?:\.\d{1,2})?", normalized)
    return float(match.group()) if match else None


def extract_sku(url: str | None, data_pid: str | None = None) -> str | None:
    if data_pid:
        return clean_text(data_pid)
    if not url:
        return None
    path = urlparse(url).path

    # Soriana product URLs end in /<sku>.html
    match = re.search(r"/(\d+)\.html/?$", path)
    if match:
        return match.group(1)

    # Walmart Mexico product URLs end in /ip/<slug>/<numeric-id>
    match = re.search(r"/(\d{8,20})/?$", path)
    return match.group(1) if match else None


def absolute_url(url: str | None, base_url: str = DEFAULT_BASE_URL) -> str | None:
    if not url:
        return None
    return urljoin(base_url, url)
