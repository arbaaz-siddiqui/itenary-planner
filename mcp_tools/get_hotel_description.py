"""get_hotel_description — agent + MCP tool.

Long-form property descriptions for one or more hotels
(GetPropertyDescriptions). Backed by the Hotels-only static-content token.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_hotel_descriptions
from core import TripPlannerError
from mcp_tools.server import mcp
from parsers import parse_hotel_descriptions_response

logger = logging.getLogger(__name__)


def _impl(hotel_ids: list[int], city_id: int = 0, max_results: int = 20) -> dict[str, Any]:
    """Get long-form property descriptions for hotels."""
    if not hotel_ids:
        return {
            "error": True,
            "message": "hotel_ids is required (at least one supplier hotel ID)",
            "error_type": "InvalidInput",
        }
    try:
        raw = call_hotel_descriptions(hotel_ids=hotel_ids, city_id=city_id)
        descriptions = parse_hotel_descriptions_response(raw, max_results=max_results)
        return {
            "descriptions": descriptions,
            "total_results": len(descriptions),
            "search_params": {"hotel_ids": hotel_ids, "city_id": city_id},
        }
    except TripPlannerError as e:
        logger.warning("get_hotel_description error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("get_hotel_description unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


get_hotel_description_tool = tool(_impl)
get_hotel_description_tool.name = "get_hotel_description"
mcp.tool(name="get_hotel_description")(_impl)
