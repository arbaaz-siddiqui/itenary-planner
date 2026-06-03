"""list_city_hotels — agent + MCP tool.

List the supplier's hotel IDs (and any static records) for a city or location
(GetStaticDataByCity). Useful for discovering which hotels exist in a city
before fetching availability / static info. Backed by the Hotels-only
static-content token.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_hotel_static_by_city
from core import TripPlannerError
from mcp_tools.server import mcp
from parsers import parse_hotel_static_data_response

logger = logging.getLogger(__name__)


def _impl(
    city_id: int,
    location_id: str = "",
    lookup_type: str = "city",
    max_results: int = 50,
) -> dict[str, Any]:
    """List hotels for a city (or location).

    Args:
        city_id: Supplier CityID (from lookup_hotel_city).
        location_id: Optional location filter (used when lookup_type="location").
        lookup_type: "city" or "location".
        max_results: Cap on hotels returned.
    """
    if not city_id and not location_id:
        return {
            "error": True,
            "message": "city_id (or location_id) is required",
            "error_type": "InvalidInput",
        }
    try:
        raw = call_hotel_static_by_city(
            city_id=city_id, location_id=location_id, lookup_type=lookup_type
        )
        hotels = parse_hotel_static_data_response(raw, max_results=max_results)
        return {
            "hotels": hotels,
            "hotel_ids": [h["hotel_id"] for h in hotels],
            "total_results": len(hotels),
            "search_params": {
                "city_id": city_id,
                "location_id": location_id,
                "lookup_type": lookup_type,
            },
        }
    except TripPlannerError as e:
        logger.warning("list_city_hotels error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("list_city_hotels unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


list_city_hotels_tool = tool(_impl)
list_city_hotels_tool.name = "list_city_hotels"
mcp.tool(name="list_city_hotels")(_impl)
