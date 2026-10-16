"""reference_data_loader — Loaders for cities + hotels JSON.

Pure I/O over the static files in reference_data/. Cached after first
read so we don't hit disk on every lookup.
"""

from __future__ import annotations

import difflib
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


@lru_cache(maxsize=1)
def _iata_index() -> dict[str, dict[str, Any]]:
    """Reverse map: IATA code (upper) -> city record. Lets us accept a raw code
    like "HYD" as input — LLMs frequently pass the airport code instead of the
    city name, which would otherwise fail to resolve."""
    idx: dict[str, dict[str, Any]] = {}
    for rec in (_load_cities().get("cities") or {}).values():
        code = (rec or {}).get("iata")
        if code:
            idx[str(code).upper()] = rec
    return idx


def resolve_city(name: str) -> dict[str, Any] | None:
    if not name:
        return None
    key = name.strip().lower().split(",")[0].strip()
    hit = (_load_cities().get("cities") or {}).get(key)
    if hit:
        return hit
    # Fall back: the input may be a 3-letter IATA code (e.g. "HYD", "DXB").
    token = name.strip().upper()
    if len(token) == 3 and token.isalpha():
        return _iata_index().get(token)
    # Customers misspell cities ("Ahemdabad"). An exact-only lookup turned the
    # typo into "unknown origin", and the corrected spelling was then refused
    # as a city the customer never named, so the search could not run at all.
    # Close spellings of long names only: short words stay exact so ordinary
    # words in a sentence never turn into a city.
    if len(key) >= FUZZY_MIN_LEN:
        close = difflib.get_close_matches(key, list((_load_cities().get("cities") or {})),
                                          n=1, cutoff=FUZZY_CUTOFF)
        if close:
            return (_load_cities().get("cities") or {}).get(close[0])
    return None


# Thresholds for misspelt place names: "ahemdabad" -> "ahmedabad" scores 0.89.
FUZZY_MIN_LEN = 6
FUZZY_CUTOFF = 0.85


def close_spelling(a: str, b: str) -> bool:
    """True when two long words are the same name with a typo."""
    a, b = a.strip().lower(), b.strip().lower()
    if a == b:
        return True
    if min(len(a), len(b)) < FUZZY_MIN_LEN:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= FUZZY_CUTOFF


def iter_cities() -> list[dict[str, Any]]:
    """Every city record we know, each with its name, ids and lat/lng."""
    return [rec for rec in (_load_cities().get("cities") or {}).values() if isinstance(rec, dict)]


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


def get_hotel_stars(city_key: str = "dubai") -> dict[str, float]:
    """Map hotel id -> star rating from reference data.

    The availability API does NOT return star ratings (it sends 0), so the
    star filter must fall back to these known ratings — otherwise any
    min_stars>=1 filter drops every hotel.
    """
    if city_key.lower() == "dubai":
        hotels = _load_dubai_hotels().get("hotels") or []
        return {
            str(h["id"]): float(h.get("stars") or 0)
            for h in hotels
            if isinstance(h, dict) and "id" in h
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


# ------------------------------------------------------------- Dubai areas
@lru_cache(maxsize=1)
def _load_dubai_areas() -> dict[str, Any]:
    with (REFERENCE_DATA_ROOT / "dubai_areas.json").open(encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


def _km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    import math

    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def resolve_dubai_area(
    address: str | None, lat: float | None = None, lng: float | None = None
) -> tuple[str, str]:
    """(area, district) for a Dubai hotel, or ("", "") when it cannot be told.

    The supplier gives no locality field, only a free-text address and
    coordinates, so "is it in Deira?" was left to the model, which guessed.
    The address wins when it names a locality (longest alias, so "bur dubai"
    beats "dubai"); otherwise the nearest neighbourhood centre within the
    radius decides. Beyond the radius we say nothing rather than guess.
    """
    data = _load_dubai_areas()
    areas = data.get("areas") or []
    text = f" {(address or '').lower()} "
    best: tuple[int, dict[str, Any]] | None = None
    for a in areas:
        for alias in a.get("aliases") or []:
            if f" {alias}" in text or f"{alias} " in text or f",{alias}" in text:
                if best is None or len(alias) > best[0]:
                    best = (len(alias), a)
    if best:
        return best[1]["area"], best[1]["district"]
    try:
        la, lo = float(lat), float(lng)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "", ""
    radius = float(data.get("radius_km") or 2.5)
    near = min(areas, key=lambda a: _km(la, lo, a["lat"], a["lng"]), default=None)
    if near and _km(la, lo, near["lat"], near["lng"]) <= radius:
        return near["area"], near["district"]
    return "", ""


def area_matches(requested: str, area: str, district: str) -> bool:
    """Does a hotel in (area, district) satisfy a customer's "in <requested>"?

    "Deira" accepts every Deira locality (Al Rigga, Port Saeed...); "Al Rigga"
    accepts only Al Rigga. Aliases count, so "Marina" finds Dubai Marina.
    """
    want = (requested or "").strip().lower()
    if not want:
        return True
    for a in _load_dubai_areas().get("areas") or []:
        names = {a["area"].lower(), *(x.lower() for x in a.get("aliases") or [])}
        if want in names:
            want = a["area"].lower()
            break
    return want in {(area or "").lower(), (district or "").lower()}
