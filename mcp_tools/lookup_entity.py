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

# "hotels" deliberately excluded: this endpoint is worldwide, so "Howard
# Johnson" resolved to US/China properties. Hotels go via city-scoped
# search_hotels(hotel_name=...) instead.
_VALID_SERVICES = {"tours", "restaurants", "airlines"}
_REDIRECTED_SERVICES = {
    # Deliberately free of "not found" / "does not exist" wording: a model
    # skimming this could echo the phrase to the customer, which is the exact
    # failure being prevented. This is a ROUTING instruction, not a result.
    "hotels": (
        "Wrong tool for hotels — this search is worldwide and would return "
        "same-brand properties in other countries. Retry with "
        "search_hotels(destination_city=<city>, hotel_name=<name as the customer "
        "said it>): it is city-scoped, resolves the property against bookable "
        "inventory, and returns live availability and pricing in one step. "
        "This message says nothing about whether the property is available — "
        "only search_hotels can tell you that."
    ),
}


# What to do with the id this lookup returns. The hint used to be built as
# f"search_{svc[:-1]}" — "search_tour", "search_restaurant" — tools that do not
# exist, so the model hit a dead end and answered from memory instead of
# fetching. Asked about desert safari add-ons it reported "no add-ons listed"
# while the supplier had a drinks package and two Majlis options.
_NEXT_STEP: dict[str, str] = {
    "tours": (
        "Pass results[0]['id'] as tour_id to get_tour_options for this tour's "
        "bookable variants and add-ons (drinks packages, VIP Majlis, upgrades), "
        "each with its own price. Use get_tour_details only for prose, and "
        "search_tours(query=...) to list other tours. NEVER say a tour has no "
        "add-ons without calling get_tour_options first."
    ),
    "restaurants": (
        "Pass results[0]['id'] as restaurant_id to get_restaurant_details for "
        "the rating, per-meal timings and dishes."
    ),
    "airlines": "Use results[0]['name'] as airline_filter in search_flights.",
}
_DEFAULT_NEXT_STEP = (
    "Use results[0]['id'] / results[0]['name'] in the matching search or "
    "detail tool — do not answer from memory."
)


def _impl(
    service: str,
    query: str,
    size: int = 5,
    city: str = "",
) -> dict[str, Any]:
    """Resolve a TOUR, RESTAURANT or AIRLINE name to its numeric ID.

    NOT FOR HOTELS. For any named hotel use
    `search_hotels(destination_city=..., hotel_name=...)` — it is city-scoped
    and returns live availability and pricing in one call. This tool searches
    worldwide and will happily return a same-brand hotel on another continent.

    Use before booking/detail calls when the user names a specific tour,
    restaurant or airline — e.g. "Desert Safari", "Khandani Rajdhani",
    "Emirates". Returns the `location_id` accepted by the other tools.

    Args:
        service:  "tours", "restaurants", or "airlines" (NOT "hotels")
        query:    Free-text name typed by the user
        size:     Max results to return (default 5, max 20)
        city:     Destination city to scope to, e.g. "Dubai". ALWAYS pass this
                  when you know the destination — results are worldwide
                  otherwise. In-city matches rank first and `scoped_note` warns
                  when nothing matches.

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

    Usage examples (note: every one passes `city`):
        lookup_entity(service="tours", query="desert safari", city="Dubai")
          → [{id: 244, name: "Desert Safari In Faqa", type: "Tour", city: "Dubai", ...}]

        lookup_entity(service="restaurants", query="khandani rajdhani", city="Dubai")
          → [{id: 4, name: "Khandani Rajdhani - Indian Restaurant in Karama", ...}]

        lookup_entity(service="airlines", query="emirates")
          → [{id: 176, name: "Emirates", iata_code: "EK", ...}]

        # For a hotel, do NOT use this tool:
        search_hotels(destination_city="Dubai", hotel_name="Howard Johnson", ...)
    """
    svc = service.lower().strip()
    # Recoverable redirect: tell the model exactly which tool to call instead,
    # rather than a bare "invalid service" it might report to the customer.
    if svc in _REDIRECTED_SERVICES:
        return {
            "error": True,
            "error_type": "WrongTool",
            "message": _REDIRECTED_SERVICES[svc],
            "use_instead": "search_hotels",
            "retry_with": {
                "tool": "search_hotels",
                "destination_city": city or "<the trip destination>",
                "hotel_name": query.strip(),
            },
        }
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
            _NEXT_STEP.get(svc, _DEFAULT_NEXT_STEP)
        ),
    }


from mcp_tools.result_cache import cache_impl
lookup_entity_tool = tool(cache_impl("lookup_entity")(_impl))
lookup_entity_tool.name = "lookup_entity"
mcp.tool(name="lookup_entity")(_impl)
