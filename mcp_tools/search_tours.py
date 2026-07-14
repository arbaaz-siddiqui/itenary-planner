"""search_tours — agent + MCP tool."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_tour_rates, call_tour_search
from core import TripPlannerError
from mcp_tools.server import mcp
from parsers import parse_tour_response
from reference_data_loader import resolve_city

logger = logging.getLogger(__name__)


def _impl(
    destination_city: str,
    travel_date: str,
    tour_category_id: int = 1,
    max_results: int = 5,
    query: str = "",
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Search tours/activities. Calls both /toursearchlist and /toursearchlistrate.

    query: when the customer names a SPECIFIC tour ("desert safari", "dhow
        cruise", "burj khalifa"), pass it here. Results are then filtered to
        names matching that keyword BEFORE the top-N cut — otherwise a specific
        tour can be missed because the default list shows only the 5 cheapest.
    force_refresh: set True to SKIP the cache and re-fetch live from the supplier
        — use when the customer says "check again" / "is it still available" or
        just before booking. Normal repeats leave this False (served from cache).
    """
    _ = force_refresh  # consumed by the cache layer; ignored here
    try:
        city = resolve_city(destination_city)
        if city is None or not city.get("city_id"):
            return {
                "error": True,
                "message": f"Unsupported destination: {destination_city!r}",
                "error_type": "UnsupportedRoute",
            }
        list_raw = call_tour_search(
            country_id=int(city["country_id"]),
            city_id=int(city["city_id"]),
            travel_date=travel_date,
            tour_category_id=tour_category_id,
        )
        rate_raw = call_tour_rates(
            country_id=int(city["country_id"]),
            city_id=int(city["city_id"]),
            travel_date=travel_date,
            tour_category_id=tour_category_id,
        )
        # Parse the FULL list first (no cap) so a keyword filter can reach tours
        # beyond the 5 cheapest. Only cap after filtering.
        all_options = parse_tour_response(list_raw, rate_raw, max_results=None)
        q = query.strip().lower()
        if q:
            terms = [w for w in q.split() if len(w) > 2]
            matched = [
                o for o in all_options
                if all(term in o.name.lower() for term in terms)
            ] or [
                # looser fallback: ANY term matches (e.g. "safari" hits "Desert Safari")
                o for o in all_options
                if any(term in o.name.lower() for term in terms)
            ]
            options = matched[:max_results]
        else:
            options = all_options[:max_results]
        return {
            "options": [o.model_dump() for o in options],
            "cheapest_price_inr": (options[0].price_per_adult_inr if options else None),
            "total_results": len(options),
            "query": query or None,
            "search_params": {
                "destination": destination_city,
                "travel_date": travel_date,
                "tour_category_id": tour_category_id,
            },
        }
    except TripPlannerError as e:
        logger.warning("search_tours error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("search_tours unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


from mcp_tools.result_cache import cache_impl

search_tours_tool = tool(cache_impl("search_tours")(_impl))
search_tours_tool.name = "search_tours"
mcp.tool(name="search_tours")(_impl)
