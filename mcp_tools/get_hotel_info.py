"""get_hotel_info — agent + MCP tool.

Static hotel content (name, star rating, address, coordinates, facilities,
images, aggregate rating) for one or more hotels. Wraps the supplier's
GetHotelStaticDataOptimize / property-info endpoints, which carry addresses
and ratings. Backed by the Hotels-only static-content account token.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_hotel_property_info, call_hotel_static_data
from core import TripPlannerError
from mcp_tools.server import mcp
from parsers import parse_hotel_static_data_response

logger = logging.getLogger(__name__)


def _impl(
    hotel_ids: list[int],
    city_id: int = 0,
    include_address: bool = True,
    max_results: int = 20,
) -> dict[str, Any]:
    """Get static info (rating, address, facilities, images) for hotels.

    Args:
        hotel_ids: Supplier hotel IDs to fetch.
        city_id: Optional city filter (0 = ignore).
        include_address: When True, also fetch the address-rich property-info
            endpoint and merge addresses/coords into the result.
        max_results: Cap on hotels returned.
    """
    if not hotel_ids:
        return {
            "error": True,
            "message": "hotel_ids is required (at least one supplier hotel ID)",
            "error_type": "InvalidInput",
        }
    try:
        raw = call_hotel_static_data(hotel_ids=hotel_ids, city_id=city_id)
        hotels = parse_hotel_static_data_response(raw, max_results=max_results)

        if include_address:
            try:
                addr_raw = call_hotel_property_info(hotel_ids=hotel_ids, city_id=city_id)
                addr = parse_hotel_static_data_response(addr_raw)
                _merge_addresses(hotels, addr)
            except TripPlannerError as e:
                # Address enrichment is best-effort — don't fail the whole call.
                logger.warning("get_hotel_info address enrichment failed: %s", e)

        return {
            "hotels": hotels,
            "total_results": len(hotels),
            "search_params": {"hotel_ids": hotel_ids, "city_id": city_id},
        }
    except TripPlannerError as e:
        logger.warning("get_hotel_info error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("get_hotel_info unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


def _merge_addresses(base: list[dict[str, Any]], addr: list[dict[str, Any]]) -> None:
    """Fill in address/coordinate fields from the property-info response."""
    by_id = {a["hotel_id"]: a for a in addr if a.get("hotel_id") is not None}
    for h in base:
        a = by_id.get(h.get("hotel_id"))
        if not a:
            continue
        for field in ("full_address", "city", "country", "latitude", "longitude", "phone"):
            if not h.get(field) and a.get(field):
                h[field] = a[field]


from mcp_tools.result_cache import cache_impl
get_hotel_info_tool = tool(cache_impl("get_hotel_info")(_impl))
get_hotel_info_tool.name = "get_hotel_info"
mcp.tool(name="get_hotel_info")(_impl)
