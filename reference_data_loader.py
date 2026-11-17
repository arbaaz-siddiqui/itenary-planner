"""reference_data_loader — Loaders for cities, airports and hotels JSON.

Mostly pure I/O over the static files in reference_data/, cached after first
read. One exception: `resolve_city` falls back to the supplier's public
autocomplete for a city that is not in cities.json (the supplier sells
worldwide; cities.json only curates Dubai and the Indian origins). That call
is imported lazily, like geo.py does, and can be switched off with
CITY_SUPPLIER_LOOKUP=0 (the test suite does).
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import threading
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

REFERENCE_DATA_ROOT = Path(__file__).resolve().parent / "reference_data"

logger = logging.getLogger(__name__)


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


def _city_key(name: str) -> str:
    return name.strip().lower().split(",")[0].strip()


def _resolve_local(name: str) -> dict[str, Any] | None:
    """cities.json only: exact key, a raw IATA code of a known city, or a close
    spelling of a long name."""
    if not name:
        return None
    key = _city_key(name)
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


def resolve_city(name: str, *, supplier: bool = True) -> dict[str, Any] | None:
    """A city record: {name, city_id, country_id, country_name, lat, lng, iata}.

    cities.json first (curated, offline). Anything else the supplier sells is
    found through its public autocomplete (`supplier_city`), so London,
    Bangkok or Bali resolve with the supplier's own ids. A curated city with no
    supplier id (the Indian origins) gets the supplier's ids added.
    `supplier=False` keeps the lookup offline: use it when scanning arbitrary
    words, which must never cost a network call.
    """
    if not name or not name.strip():
        return None
    local = _resolve_local(name)
    if local is not None:
        if local.get("city_id") or not supplier:
            return local
        found = supplier_city(str(local.get("name") or name))
        if found and found.get("city_id"):
            return {**local, "city_id": found["city_id"], "country_id": found["country_id"],
                    "country_name": local.get("country_name") or found.get("country_name")}
        return local
    if not supplier:
        return None
    token = name.strip().upper()
    if len(token) == 3 and token.isalpha() and token in _load_airports().get("airports", {}):
        rec = airport(token) or {}
        found = supplier_city(str(rec.get("city") or ""))
        return {**found, "iata": token} if found else None
    return supplier_city(name)


def _plain(text: str) -> str:
    """Lower case, accents and punctuation dropped: "Malé" -> "male"."""
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split())


def supplier_lookup_enabled() -> bool:
    return os.environ.get("CITY_SUPPLIER_LOOKUP", "1").strip().lower() not in ("0", "false", "no", "off")


_SUPPLIER_CITIES: dict[str, dict[str, Any] | None] = {}
_SUPPLIER_LOCK = threading.Lock()
# The services whose autocomplete lists cities; hotels covers the most places,
# tours lists the cities that only sell tours (Bali). Same city id space.
_CITY_SERVICES = ("hotels", "tours")


@lru_cache(maxsize=1)
def _supplier_spellings() -> dict[str, str]:
    """Plain name -> the supplier's spelling (reference_data/city_spellings.json)."""
    path = REFERENCE_DATA_ROOT / "city_spellings.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {_plain(k): str(v) for k, v in raw.items() if not k.startswith("_")}


def supplier_city(name: str) -> dict[str, Any] | None:
    """The supplier's own record of a city, from its public autocomplete.

    Only a row of type city whose name IS the query (or a close spelling of a
    long one) counts, so a word like "desert" never becomes "Palm Desert".
    Answers are cached for the process; a failed call is not, so a supplier
    blip is retried next time.
    """
    key = _plain(_city_key(name or ""))
    if len(key) < 3 or not supplier_lookup_enabled():
        return None
    # The supplier's search does not fold accents: "Zurich" finds no city,
    # "Zürich" does. Ask in its spelling; rows are matched accent-free.
    query = _supplier_spellings().get(key) or _city_key(name)
    want = _plain(query)
    with _SUPPLIER_LOCK:
        if key in _SUPPLIER_CITIES:
            return _SUPPLIER_CITIES[key]
    try:
        from booking_api.endpoints import call_entity_search
    except Exception:  # noqa: BLE001 - no client means no supplier answer
        return None
    found: dict[str, Any] | None = None
    complete = True
    for service in _CITY_SERVICES:
        try:
            raw = call_entity_search(service=service, query=query, size=10) or {}
        except Exception as e:  # noqa: BLE001
            logger.warning("supplier city lookup failed q=%r: %s", name, e)
            complete = False
            continue
        if raw.get("error"):
            complete = False
        found = _pick_city_row(raw.get("data") or [], want)
        if found:
            break
    if found or complete:
        with _SUPPLIER_LOCK:
            _SUPPLIER_CITIES[key] = found
            while len(_SUPPLIER_CITIES) > 2000:
                _SUPPLIER_CITIES.pop(next(iter(_SUPPLIER_CITIES)))
    return found


def _pick_city_row(rows: list[Any], key: str) -> dict[str, Any] | None:
    cities = [r for r in rows if isinstance(r, dict) and str(r.get("type") or "").lower() == "city"
              and r.get("cityId")]

    def named(r: dict[str, Any]) -> str:
        return _plain(str(r.get("name_text") or r.get("city") or ""))

    row = next((r for r in cities if named(r) == key), None)
    if row is None and len(key) >= FUZZY_MIN_LEN:
        row = next((r for r in cities if close_spelling(named(r), key)), None)
    if row is None:
        return None
    lat, lng = _num(row.get("latitude")), _num(row.get("longitude"))
    name = str(row.get("name_text") or row.get("city") or "").strip()
    rec: dict[str, Any] = {
        "name": name,
        "city_id": int(row["cityId"]),
        "country_id": int(row["countryId"]) if row.get("countryId") else None,
        "country_name": str(row.get("country") or "").strip(),
        "lat": lat,
        "lng": lng,
        "source": "supplier",
    }
    main = city_airport(name, lat=lat, lng=lng)
    if main:
        rec["iata"] = main["iata"]
    return rec


def _num(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f else None


# ------------------------------------------------------------------ airports
@lru_cache(maxsize=1)
def _load_airports() -> dict[str, Any]:
    """reference_data/airports.json, built by scripts/build_airports.py."""
    path = REFERENCE_DATA_ROOT / "airports.json"
    if not path.exists():
        return {"airports": {}, "cities": {}, "countries": {}}
    with path.open(encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


def airport(code: str) -> dict[str, Any] | None:
    """{iata, name, city, country, country_code, lat, lng, size} for a code."""
    code = (code or "").strip().upper()
    data = _load_airports()
    rec = (data.get("airports") or {}).get(code)
    if not rec:
        return None
    return {
        "iata": code,
        "name": rec.get("n"),
        "city": rec.get("c"),
        "country": (data.get("countries") or {}).get(rec.get("cc"), rec.get("cc")),
        "country_code": rec.get("cc"),
        "lat": rec.get("lat"),
        "lng": rec.get("lng"),
        "size": "large" if rec.get("s") == 2 else "medium",
    }


def city_airport(name: str, *, lat: float | None = None, lng: float | None = None) -> dict[str, Any] | None:
    """The main airport of a city: by name from airports.json, else the
    nearest large (then medium) airport within reach of the city's position."""
    key = _plain(_city_key(name or ""))
    codes = (_load_airports().get("cities") or {}).get(key) or []
    if codes:
        return airport(codes[0])
    if lat is None or lng is None:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for code, rec in (_load_airports().get("airports") or {}).items():
        d = _km(lat, lng, float(rec["lat"]), float(rec["lng"]))
        # A medium airport must be much closer to win over a large one.
        limit = 120.0 if rec.get("s") == 2 else 60.0
        score = d if rec.get("s") == 2 else d * 2
        if d <= limit and (best is None or score < best[0]):
            best = (score, {"code": code})
    return airport(best[1]["code"]) if best else None


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
    """The airport code to fly to or from a place.

    cities.json first (Dubai and the Indian origins keep their codes), then a
    raw 3-letter airport code, then the city's main airport in airports.json,
    then the supplier's record of the city and the airport nearest to it.
    """
    if not name or not name.strip():
        return None
    city = _resolve_local(name)
    if city and city.get("iata"):
        return str(city["iata"])
    token = name.strip().upper()
    if len(token) == 3 and token.isalpha() and airport(token):
        return token
    main = city_airport(name)
    if main:
        return str(main["iata"])
    found = resolve_city(name)
    return str(found["iata"]) if found and found.get("iata") else None


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
