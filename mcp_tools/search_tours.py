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
) -> dict[str, Any]:
    """Search tours/activities. Calls both /toursearchlist and /toursearchlistrate."""
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
        options = parse_tour_response(list_raw, rate_raw, max_results=max_results)
        return {
            "options": [o.model_dump() for o in options],
            "cheapest_price_inr": (options[0].price_per_adult_inr if options else None),
            "total_results": len(options),
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


search_tours_tool = tool(_impl)
search_tours_tool.name = "search_tours"
mcp.tool(name="search_tours")(_impl)
