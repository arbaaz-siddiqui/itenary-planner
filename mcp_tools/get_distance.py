"""get_distance: how far one place is from another, with no dates needed.

Asked "how far is Atlantis The Palm from Dubai airport?", the agent reached
for the airport-transfer search, which needs a travel date, and answered with
"When do you need the transfer?". Another time it gave "about 1.5 km" from
nothing. This tool answers the question as asked: it places both points
(airports from airports.json, hotels, landmarks and areas through the
supplier's autocomplete, as `geo.locate_place` does) and measures the
straight-line distance between them. A drive time is given only as a range
and labelled an estimate; the supplier has no route data for us.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.tools import tool

from geo import haversine_km, locate_place
from mcp_tools.server import mcp
from reference_data_loader import airport, city_airport, resolve_city

logger = logging.getLogger(__name__)

# Roads are longer than the straight line: ~1.3x is the usual detour factor in
# cities. Speeds give a range, from free-flowing to busy traffic: cross-town
# trips run mostly on motorways, short ones on city streets.
ROAD_FACTOR = 1.3
SPEEDS_KMH = {"motorway": (75.0, 45.0), "city": (40.0, 22.0)}
MOTORWAY_FROM_KM = 12.0

_AIRPORT_WORDS = re.compile(r"\b(international\s+)?(airport|aeroporto|aeropuerto|terminal\s*\d*)\b", re.I)


def _airport_point(text: str, city: str) -> dict[str, Any] | None:
    """An airport named by code ("DXB"), "<city> airport" or "<name> airport"."""
    raw = (text or "").strip()
    code = re.search(r"\(([A-Za-z]{3})\)|\b([A-Z]{3})\b", raw)
    if code:
        rec = airport((code.group(1) or code.group(2)).upper())
        if rec:
            return rec
    if not _AIRPORT_WORDS.search(raw):
        return None
    rest = _AIRPORT_WORDS.sub(" ", raw).strip(" ,-")
    for name in (rest, city):
        if not name:
            continue
        rec = city_airport(name)
        if rec:
            return rec
        found = resolve_city(name)
        if found and found.get("iata"):
            return airport(str(found["iata"]))
    return None


def _point(text: str, city: str) -> dict[str, Any] | None:
    ap = _airport_point(text, city)
    if ap and ap.get("lat") is not None:
        return {"name": f"{ap['name']} ({ap['iata']})", "lat": float(ap["lat"]),
                "lng": float(ap["lng"]), "kind": "airport", "city": ap.get("city") or city}
    place = locate_place(text, city)
    if place is None:
        # Landmarks the autocomplete does not place (Burj Khalifa) are in our
        # curated Dubai attraction table.
        from mcp_tools.plan_pricing import place_of

        known = place_of(text, city or "Dubai")
        if known is None:
            return None
        return {"name": text.strip(), "lat": known["lat"], "lng": known["lng"],
                "kind": "attraction", "city": city or "Dubai"}
    return {"name": place.name, "lat": place.latitude, "lng": place.longitude,
            "kind": place.source_type or "place", "city": place.city}


def _impl(from_place: str, to_place: str, city: str = "") -> dict[str, Any]:
    """How far apart two places are (hotel, airport, landmark, area).

    Use this for ANY "how far is X from Y", "distance from X to Y" or "how
    long to get from X to Y" question. It needs no travel date, party or
    budget: answer straight away. Ask for dates only if the customer then
    wants a priced transfer or a booking.

    Args:
        from_place: e.g. "Dubai airport", "DXB", "Atlantis The Palm".
        to_place: e.g. "Burj Khalifa", "Dubai Marina", "Changi airport".
        city: the city both places are in ("Dubai", "Singapore"); strongly
            preferred, it keeps same-named places in other cities out.
    """
    city = (city or "").strip()
    a = _point(from_place, city)
    b = _point(to_place, city)
    missing = [name for name, p in ((from_place, a), (to_place, b)) if p is None]
    if missing:
        return {
            "error": True,
            "error_type": "PlaceNotFound",
            "message": f"Could not place {', '.join(repr(m) for m in missing)} on the map.",
            "agent_instructions": (
                "Say we could not find that place and ask for its exact name or "
                "area. Do not estimate a distance, and do not ask for travel dates."
            ),
        }
    km = haversine_km(a["lat"], a["lng"], b["lat"], b["lng"])
    road = km * ROAD_FACTOR
    quick, busy = SPEEDS_KMH["motorway" if road >= MOTORWAY_FROM_KM else "city"]
    fast = max(1, round(road / quick * 60))
    slow = max(fast, round(road / busy * 60))
    return {
        "from": a["name"],
        "to": b["name"],
        "straight_line_km": round(km, 1),
        "approx_road_km": round(road, 1),
        "approx_drive_minutes": [fast, slow],
        "summary": (
            f"{a['name']} to {b['name']}: {km:.1f} km in a straight line, "
            f"roughly {road:.0f} km by road, about {fast}-{slow} minutes by car "
            "depending on traffic."
        ),
        "agent_instructions": (
            "Answer the distance question now with `summary`: say the straight-line "
            "distance and that the road distance and drive time are estimates. Do "
            "not ask for travel dates, party size or budget for this. Then offer, in "
            "one short line, to price an airport transfer or a tour if useful; ask "
            "for dates only if they say yes."
        ),
    }


from mcp_tools.result_cache import cache_impl  # noqa: E402

get_distance_tool = tool(cache_impl("get_distance")(_impl))
get_distance_tool.name = "get_distance"
mcp.tool(name="get_distance")(_impl)
