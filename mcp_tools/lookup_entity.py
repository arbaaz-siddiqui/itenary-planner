"""lookup_entity — public autocomplete search for hotels, tours, restaurants, airlines.

No auth token required. Used by the agent to resolve a user-typed name
("Atlantis", "Desert Safari", "Khandani Rajdhani") to the numeric ID that all
booking endpoints expect. The `location_id` returned here IS the hotel_id /
tour_id / restaurant_id used everywhere else in the system.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_entity_search
from mcp_tools.server import mcp

logger = logging.getLogger(__name__)

# Supported service types
_VALID_SERVICES = {"hotels", "tours", "restaurants", "airlines"}


def _impl(
    service: str,
    query: str,
    size: int = 5,
    city: str = "",
) -> dict[str, Any]:
    """Look up a hotel, tour, restaurant, or airline by name.

    Use this BEFORE booking calls when the user names a specific property —
    e.g. "Atlantis", "Desert Safari", "Khandani Rajdhani", "Emirates".
    Returns the `location_id` which is the numeric ID (hotel_id / tour_id /
    restaurant_id) accepted by all other search and detail tools.

    WARNING — this endpoint searches WORLDWIDE. "Howard Johnson" returns
    properties in the US and China, not Dubai. ALWAYS pass `city` when you know
    the destination, and never tell a customer a hotel doesn't exist based on
    this tool alone. For a named hotel on a known trip,
    `search_hotels(destination_city=..., hotel_name=...)` is the correct tool —
    it is city-scoped and resolves against bookable local inventory.

    Args:
        service:  What to search — "hotels", "tours", "restaurants", or "airlines"
        query:    Free-text name typed by the user, e.g. "Atlantis Dubai"
        size:     Max results to return (default 5, max 20)
        city:     Destination city to scope to, e.g. "Dubai". In-city matches are
                  ranked first and `scoped_note` warns when nothing matches.

    Returns a list of matches, each with:
        - id (int)              ← use this as hotel_id / tour_id / restaurant_id
        - name (str)            ← exact supplier name
        - type (str)            ← "hotel", "city", "Tour", "Restaurant", "Airline"
        - city (str)
        - country (str)
        - city_id (int)         ← cityId for that entity
        - country_id (int)      ← countryId
        - latitude / longitude  ← location (0,0 if unknown)
        - iata_code (str)       ← airlines only, empty string for others
        - total_services (int)  ← how many bookable services this entity has

    Usage examples:
        lookup_entity(service="hotels", query="Atlantis Dubai")
          → [{id: 823, name: "Atlantis The Royal", type: "hotel", city: "Dubai", ...}]

        lookup_entity(service="tours", query="desert safari dubai")
          → [{id: 244, name: "Desert Safari In Faqa", type: "Tour", city: "Dubai", ...}]

        lookup_entity(service="restaurants", query="khandani rajdhani")
          → [{id: 4, name: "Khandani Rajdhani - Indian Restaurant in Karama", ...}]

        lookup_entity(service="airlines", query="emirates")
          → [{id: 176, name: "Emirates", iata_code: "EK", ...}]

    Tip: filter results by type=="hotel" (not "city") when you want only
    bookable hotel properties, not city-level suggestions.
    """
    svc = service.lower().strip()
    if svc not in _VALID_SERVICES:
        return {
            "error": True,
            "message": f"Invalid service '{service}'. Must be one of: {', '.join(sorted(_VALID_SERVICES))}",
        }
    if not query or not query.strip():
        return {"error": True, "message": "query cannot be empty"}

    size = min(max(1, size), 20)

    try:
        raw = call_entity_search(service=svc, query=query.strip(), size=size)
    except Exception as e:
        logger.warning("lookup_entity call failed: %s", e)
        return {"error": True, "message": str(e), "results": []}

    items = raw.get("data") or []
    if not isinstance(items, list):
        return {"error": True, "message": "Unexpected response format", "results": []}

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        loc_id = item.get("location_id", 0)
        results.append({
            "id": int(loc_id) if loc_id else 0,
            "name": item.get("name_text", ""),
            "type": item.get("type", ""),
            "city": item.get("city", ""),
            "country": item.get("country", ""),
            "city_id": item.get("cityId", 0),
            "country_id": item.get("countryId", 0),
            "latitude": item.get("latitude", 0),
            "longitude": item.get("longitude", 0),
            "iata_code": item.get("iataCode", ""),
            "total_services": item.get("totalAvailableServices", 0),
        })

    # Filter out city/region rows (id==0 or type not matching service) when
    # caller searches hotels — city rows don't have a bookable hotel_id.
    hotel_results = [r for r in results if r["id"] > 0 and r["type"].lower() not in ("city", "high_level_region", "neighborhood")]

    # City scoping. This endpoint searches GLOBALLY: "Howard Johnson" returns
    # Bakersfield / Changsha / Yibin and NOT the Dubai property, which is how a
    # customer asking about a Dubai hotel was told it only exists in the US and
    # China. When the caller knows the destination, keep in-city matches first
    # and report how many were dropped so the agent can't silently read out a
    # foreign list.
    city_filter = (city or "").strip().lower()
    out_of_city = 0
    if city_filter and hotel_results:
        in_city = [r for r in hotel_results if city_filter in str(r.get("city", "")).lower()]
        out_of_city = len(hotel_results) - len(in_city)
        if in_city:
            # Rank in-city first; keep the rest so the agent can still see them.
            hotel_results = in_city + [r for r in hotel_results if r not in in_city]

    scoped_note = ""
    if city_filter:
        if out_of_city and hotel_results and city_filter in str(hotel_results[0].get("city", "")).lower():
            scoped_note = (
                f"Ranked {len(hotel_results) - out_of_city} match(es) in {city} first; "
                f"{out_of_city} result(s) are in other cities — do NOT offer those."
            )
        elif out_of_city == len(hotel_results):
            scoped_note = (
                f"NONE of these results are in {city} — this endpoint searches worldwide. "
                f"Do NOT tell the customer the hotel doesn't exist. "
                f"Call search_hotels(destination_city='{city}', hotel_name=...) instead, "
                f"which resolves the property against local inventory."
            )

    return {
        "results": hotel_results,
        "all_results": results,       # includes city suggestions if caller needs them
        "total": len(hotel_results),
        "service": svc,
        "query": query.strip(),
        "city_filter": city or "",
        "scoped_note": scoped_note,
        "usage_hint": (
            "This search is GLOBAL — always check each result's 'city' before showing it. "
            "For a named hotel in a known destination, prefer "
            "search_hotels(destination_city=..., hotel_name=...) which is city-scoped. "
            "Otherwise: pass results[0]['id'] as hotel_ids=[id] in get_hotel_info, "
            "or results[0]['name'] as hotel_name in search_hotels. "
            "Also capture results[0]['latitude'] and results[0]['longitude'] — "
            "you will need these as hotel_lat/hotel_lng for search_airport_transfer_dubai."
            if svc == "hotels" else
            f"Pass results[0]['id'] as the {svc[:-1]}_id parameter in your next search_{svc[:-1]} call."
        ),
    }


from mcp_tools.result_cache import cache_impl
lookup_entity_tool = tool(cache_impl("lookup_entity")(_impl))
lookup_entity_tool.name = "lookup_entity"
mcp.tool(name="lookup_entity")(_impl)
