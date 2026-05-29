"""search_restaurants — agent + MCP tool."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_restaurant_search
from core import TripPlannerError
from mcp_tools.server import mcp
from parsers import parse_restaurant_response
from reference_data_loader import resolve_city

logger = logging.getLogger(__name__)


def _impl(
    destination_city: str,
    search_date: str,
    adults: int = 2,
    children: int = 0,
    max_results: int = 5,
) -> dict[str, Any]:
    """Search restaurants in the destination city."""
    try:
        city = resolve_city(destination_city)
        if city is None or not city.get("city_id"):
            return {
                "error": True,
                "message": f"Unsupported destination: {destination_city!r}",
                "error_type": "UnsupportedRoute",
            }
        raw = call_restaurant_search(
            city_id=int(city["city_id"]),
            search_date=search_date,
            adults=adults,
            children=children,
        )
        options = parse_restaurant_response(raw, max_results=max_results)
        return {
            "options": [o.model_dump() for o in options],
            "cheapest_price_inr": (options[0].price_per_adult_inr if options else None),
            "total_results": len(options),
            "search_params": {
                "destination": destination_city,
                "search_date": search_date,
                "adults": adults,
                "children": children,
            },
        }
    except TripPlannerError as e:
        logger.warning("search_restaurants error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("search_restaurants unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


search_restaurants_tool = tool(_impl)
search_restaurants_tool.name = "search_restaurants"
mcp.tool(name="search_restaurants")(_impl)
