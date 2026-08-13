"""lookup_hotel_city — agent + MCP tool.

Resolve a free-text city name to the supplier's bookable city records
(incl. the numeric CityID that hotel search / static-data endpoints need).
Backed by the Hotels-only static-content account token.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_hotel_cities
from core import TripPlannerError
from mcp_tools.server import mcp
from parsers import parse_hotel_cities_response

logger = logging.getLogger(__name__)


def _impl(city_name: str, max_results: int = 10) -> dict[str, Any]:
    """Look up bookable cities by name. Returns city records with CityID."""
    if not city_name or not city_name.strip():
        return {
            "error": True,
            "message": "city_name is required",
            "error_type": "InvalidInput",
        }
    try:
        raw = call_hotel_cities(city_name=city_name.strip())
        cities = parse_hotel_cities_response(raw, max_results=max_results)
        return {
            "cities": cities,
            "total_results": len(cities),
            "search_params": {"city_name": city_name},
        }
    except TripPlannerError as e:
        logger.warning("lookup_hotel_city error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("lookup_hotel_city unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


from mcp_tools.result_cache import cache_impl
lookup_hotel_city_tool = tool(cache_impl("lookup_hotel_city")(_impl))
lookup_hotel_city_tool.name = "lookup_hotel_city"
mcp.tool(name="lookup_hotel_city")(_impl)
