"""get_restaurant_details — agent + MCP tool."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_restaurant_details
from core import TripPlannerError
from mcp_tools.server import mcp
from reference_data_loader import resolve_city

logger = logging.getLogger(__name__)


def _impl(
    restaurant_id: int,
    destination_city: str,
    search_date: str,
    adults: int = 1,
    children: int = 0,
) -> dict[str, Any]:
    """Get details for a specific restaurant (id from search_restaurants result)."""
    try:
        city = resolve_city(destination_city)
        if city is None or not city.get("city_id"):
            return {
                "error": True,
                "message": f"Unsupported destination: {destination_city!r}",
                "error_type": "UnsupportedRoute",
            }
        raw = call_restaurant_details(
            restaurant_id=restaurant_id,
            city_id=int(city["city_id"]),
            search_date=search_date,
            adults=adults,
            children=children,
        )
        return {"data": raw, "restaurant_id": restaurant_id}
    except TripPlannerError as e:
        logger.warning("get_restaurant_details error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("get_restaurant_details unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


get_restaurant_details_tool = tool(_impl)
get_restaurant_details_tool.name = "get_restaurant_details"
mcp.tool(name="get_restaurant_details")(_impl)
