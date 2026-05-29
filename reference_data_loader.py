"""reference_data_loader — Loaders for cities + hotels JSON.

Pure I/O over the static files in reference_data/. Cached after first
read so we don't hit disk on every lookup.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

REFERENCE_DATA_ROOT = Path(__file__).resolve().parent / "reference_data"


@lru_cache(maxsize=1)
def _load_cities() -> dict[str, Any]:
    with (REFERENCE_DATA_ROOT / "cities.json").open(encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


@lru_cache(maxsize=1)
def _load_dubai_hotels() -> dict[str, Any]:
    with (REFERENCE_DATA_ROOT / "hotels" / "dubai.json").open(encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


def resolve_city(name: str) -> dict[str, Any] | None:
    if not name:
        return None
    key = name.strip().lower().split(",")[0].strip()
    return (_load_cities().get("cities") or {}).get(key)


def resolve_iata(name: str) -> str | None:
    city = resolve_city(name)
    return city.get("iata") if city else None


def resolve_country_id(name: str) -> int | None:
    return (_load_cities().get("country_aliases") or {}).get(name)


def get_default_dubai_airport() -> dict[str, Any]:
    return dict(_load_cities().get("default_dubai_airport") or {})


def get_hotel_ids_for_city(city_key: str = "dubai") -> list[int]:
    if city_key.lower() == "dubai":
        hotels = _load_dubai_hotels().get("hotels") or []
        return [h["id"] for h in hotels if isinstance(h, dict) and "id" in h]
    return []


def get_hotel_names(city_key: str = "dubai") -> dict[str, str]:
    if city_key.lower() == "dubai":
        hotels = _load_dubai_hotels().get("hotels") or []
        return {
            str(h["id"]): h["name"]
            for h in hotels
            if isinstance(h, dict) and "id" in h and "name" in h
        }
    return {}


def get_hotel_areas(city_key: str = "dubai") -> dict[str, str]:
    if city_key.lower() == "dubai":
        hotels = _load_dubai_hotels().get("hotels") or []
        return {
            str(h["id"]): h.get("area", "") for h in hotels if isinstance(h, dict) and "id" in h
        }
    return {}


def list_indian_origins() -> list[str]:
    cities = _load_cities().get("cities") or {}
    return [
        c["name"]
        for c in cities.values()
        if isinstance(c, dict) and c.get("country_name") == "India"
    ]


def list_destinations() -> list[str]:
    cities = _load_cities().get("cities") or {}
    return [
        c["name"]
        for c in cities.values()
        if isinstance(c, dict) and c.get("country_name") != "India"
    ]
