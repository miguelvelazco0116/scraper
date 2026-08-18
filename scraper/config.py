from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    url: str
    subcategory: str | None = None
    sub_subcategory: str | None = None


@dataclass(frozen=True)
class Location:
    id: str
    city: str
    state: str
    country: str
    postal_code: str | None = None
    store: str | None = None


def _load_yaml(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_categories(path: str | Path = "config/soriana/categories.yaml") -> list[Category]:
    data = _load_yaml(path)
    return [
        Category(
            id=x["id"],
            name=x["name"],
            url=x["url"],
            subcategory=x.get("subcategory"),
            sub_subcategory=x.get("sub_subcategory"),
        )
        for x in data.get("categories", [])
        if x.get("enabled", True)
    ]


def load_locations(path: str | Path = "config/locations.yaml") -> list[Location]:
    data = _load_yaml(path)
    return [
        Location(
            id=x["id"],
            city=x["city"],
            state=x["state"],
            country=x.get("country", "México"),
            postal_code=x.get("postal_code"),
            store=x.get("store"),
        )
        for x in data.get("locations", [])
        if x.get("enabled", True)
    ]
