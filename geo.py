"""geo — where is the place the customer named, and how far is it?

Customers ask for restaurants "in Dubai Marina", "near Dubai Frame", "in
Deira". The restaurant search has no area filter, so without a location for
the place the agent could only recite the city list and guess the distance
-- which is how a Karama restaurant was presented as being "in Deira" and a
21 km trip as "nearby".

Places are resolved through the supplier's own place search (the entity
autocomplete), never a hand-kept coordinate table: it already knows
neighbourhoods ("Dubai Marina", "Al Karama"), points of interest ("Zabeel
Park") and attractions ("Dubai Frame"). Its rows need three guards, each
learned from a live response:

1. It searches worldwide -- "Deira" alone returns Madeira, "Karama" returns
   Karaman (Turkey). Queries are city-qualified, and a row only counts inside
   the city's radius.
2. Property rows carry a placeholder position: every Dubai hotel returned
   25.2706, 55.3004. A hotel's real position is read from the hotel static
   data instead (the same source airport transfers use), and among other
   rows a coordinate shared by two different names is a placeholder.
3. A row must actually be the place asked for. A neighbourhood or point of
   interest must carry the query's own words. A product row (a hotel, a tour)
   must BEGIN with the query: product names borrow landmarks, and "Dubai
   Mall" otherwise resolved to "... JVC near Circle Mall", 15 km away.

Distances are straight-line (great-circle). They are reported as such: we
have no routing source, so a travel time is never derived from them.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass
from typing import Any

from text_match import matches, starts_with, without_words

logger = logging.getLogger(__name__)

_EARTH_RADIUS_KM = 6371.0088

#: Supplier row types that name a place rather than a product. Ranked ahead
#: of product rows (a tour, a property), whose position is a venue at best.
_PLACE_TYPES = ("neighborhood", "point_of_interest", "landmark", "airport")

#: Row types that cover a whole administrative area, or a product category.
#: Their position is a centroid or nothing, so they cannot anchor a distance.
_AREA_TYPES = {"city", "province_state", "country", "high_level_region", "tourtype"}

#: Entity-search services that return places, in the order they are asked.
#: "hotels" carries the neighbourhood and point-of-interest rows; "tours"
#: carries attractions (Dubai Frame) that the hotel index does not.
_PLACE_SERVICES = ("hotels", "tours")


@dataclass(frozen=True)
class Place:
    name: str
    latitude: float
    longitude: float
    #: The supplier's row type ("neighborhood", "Tour", ...).
    source_type: str
    #: The city it lies in, as named in our reference data.
    city: str


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points, in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _coord(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f and math.isfinite(f) else None


def nearest_city(lat: float, lng: float, *, max_km: float) -> dict[str, Any] | None:
    """The reference city closest to a point, if one lies within `max_km`."""
    from reference_data_loader import iter_cities

    best: tuple[float, dict[str, Any]] | None = None
    for rec in iter_cities():
        clat, clng = _coord(rec.get("lat")), _coord(rec.get("lng"))
        if clat is None or clng is None:
            continue
        d = haversine_km(lat, lng, clat, clng)
        if d <= max_km and (best is None or d < best[0]):
            best = (d, rec)
    return best[1] if best else None


def _hotel_position(hotel_id: Any) -> tuple[float, float] | None:
    """A property's real position, from the hotel static data."""
    try:
        hid = int(hotel_id)
    except (TypeError, ValueError):
        return None
    from booking_api import call_hotel_property_info
    from parsers import parse_hotel_static_data_response

    try:
        hotels = parse_hotel_static_data_response(call_hotel_property_info(hotel_ids=[hid]))
    except Exception as e:  # no position is an answer, not a failure
        logger.warning("hotel position lookup failed id=%s: %s", hid, e)
        return None
    for h in hotels:
        lat, lng = _coord(h.get("latitude")), _coord(h.get("longitude"))
        if lat is not None and lng is not None:
            return lat, lng
    return None


def _decimals(value: Any) -> int:
    text = str(value)
    return len(text.split(".", 1)[1]) if "." in text else 0


def _is_city_centre(row: dict[str, Any], centre: tuple[float | None, float | None]) -> bool:
    """A low-precision position that is just the city's centre, rounded.

    Tour rows with no real location carry the city centre at two decimals:
    "Burj Khalifa Tickets" and "Dubai City Tour from Abu Dhabi" both sit at
    25.2, 55.27 -- Dubai's centre (25.2048, 55.2708) rounded. That says "in
    Dubai", not where the place is, so it cannot anchor a distance.
    """
    clat, clng = centre
    if clat is None or clng is None:
        return False
    places = min(_decimals(row.get("latitude")), _decimals(row.get("longitude")))
    if places > 2:
        return False
    return (
        round(float(row["latitude"]), places) == round(clat, places)
        and round(float(row["longitude"]), places) == round(clng, places)
    )


def _names_the_place(row: dict[str, Any], query: str, name_query: str) -> bool:
    """Guard 3: is this row the place asked for, not something named after it?"""
    name = str(row.get("name_text") or "")
    if str(row.get("type") or "").lower() in _PLACE_TYPES:
        return matches(name_query, name)
    return starts_with(name, query)


def _candidates(
    rows: list[dict[str, Any]], query: str, name_query: str
) -> list[dict[str, Any]]:
    """Rows that name the place asked about, best first.

    Each carries `_lat`/`_lng`. A hotel row's are None: its entity position is
    the placeholder, so the caller reads the real one only for the row it
    actually uses.
    """
    named = [
        r for r in rows
        if isinstance(r, dict)
        and str(r.get("type") or "").lower() not in _AREA_TYPES
        and _names_the_place(r, query, name_query)
    ]

    # Guard 2: among non-hotel rows, a position shared by different names is a
    # placeholder. Counted over the whole response, not just the named rows.
    seen: dict[tuple[float, float], set[str]] = {}
    for r in rows:
        if not isinstance(r, dict) or str(r.get("type") or "").lower() == "hotel":
            continue
        lat, lng = _coord(r.get("latitude")), _coord(r.get("longitude"))
        if lat is not None and lng is not None:
            seen.setdefault((round(lat, 4), round(lng, 4)), set()).add(str(r.get("name_text")))

    out: list[tuple[int, int, dict[str, Any]]] = []
    for i, row in enumerate(named):
        rtype = str(row.get("type") or "").lower()
        lat = lng = None
        if rtype != "hotel":
            lat, lng = _coord(row.get("latitude")), _coord(row.get("longitude"))
            if lat is None or lng is None or len(seen[(round(lat, 4), round(lng, 4))]) > 1:
                continue
        rank = _PLACE_TYPES.index(rtype) if rtype in _PLACE_TYPES else len(_PLACE_TYPES)
        out.append((rank, i, {**row, "_lat": lat, "_lng": lng}))
    return [row for _rank, _i, row in sorted(out, key=lambda t: (t[0], t[1]))]


def _locate(query: str, city: str) -> Place | None:
    from booking_api import call_entity_search
    from reference_data_loader import resolve_city
    from settings import get_geo_settings

    radius = get_geo_settings().place_radius_km
    city_rec = resolve_city(city) if city else None
    centre = (
        (_coord(city_rec.get("lat")), _coord(city_rec.get("lng"))) if city_rec else (None, None)
    )

    # The words that identify the place itself: "Dubai Marina" in Dubai is
    # identified by "marina". If the query IS the city name, keep it whole.
    name_query = without_words(query, city) if city else query
    if not name_query:
        name_query = query

    # Guard 1: qualify the query with the city first; the bare name second.
    queries = [query]
    if city and city.lower() not in query.lower():
        queries = [f"{query} {city}", query]

    for q in queries:
        for service in _PLACE_SERVICES:
            try:
                rows = (call_entity_search(service=service, query=q, size=10) or {}).get("data") or []
            except Exception as e:  # a lookup miss is not an error
                logger.warning("locate_place lookup failed q=%r service=%s: %s", q, service, e)
                continue
            for row in _candidates(rows, query, name_query):
                lat, lng = row["_lat"], row["_lng"]
                if lat is None or lng is None:
                    pos = _hotel_position(row.get("location_id"))
                    if pos is None:
                        continue
                    lat, lng = pos
                elif _is_city_centre(row, centre):
                    continue
                if centre[0] is not None and centre[1] is not None:
                    if haversine_km(lat, lng, centre[0], centre[1]) > radius:
                        continue
                    home = city_rec
                else:
                    home = nearest_city(lat, lng, max_km=radius)
                    if home is None:
                        continue
                return Place(
                    name=str(row.get("name_text") or query).strip(),
                    latitude=lat,
                    longitude=lng,
                    source_type=str(row.get("type") or ""),
                    city=str((home or {}).get("name") or city),
                )
    return None


def locate_place(query: str, city: str = "") -> Place | None:
    """Resolve an area, landmark or venue name to a position.

    `city` scopes the search and is strongly preferred; without it the place
    must lie within the radius of SOME reference city, which becomes its
    `city`. Returns None when the supplier has no usable position for it --
    callers must then say so rather than estimate.

    Successful lookups are cached for the static-content TTL: a neighbourhood
    does not move. Misses are not cached, so a transient supplier failure is
    retried on the next request.
    """
    query = (query or "").strip()
    if not query:
        return None
    from mcp_tools.result_cache import _STATIC_TTL, cached_or_call

    found = cached_or_call(
        "locate_place",
        {"query": query.lower(), "city": (city or "").strip().lower()},
        lambda: {"place": asdict(p)} if (p := _locate(query, (city or "").strip())) else {},
        ttl=_STATIC_TTL,
    )
    data = (found or {}).get("place")
    return Place(**data) if isinstance(data, dict) else None
