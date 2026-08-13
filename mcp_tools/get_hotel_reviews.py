"""get_hotel_reviews — agent + MCP tool.

Aggregated guest reviews + average rating for a single hotel
(GetHotelGuestReview). Backed by the Hotels-only static-content token.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_hotel_guest_review
from core import TripPlannerError
from mcp_tools.server import mcp
from parsers import parse_hotel_guest_review_response

logger = logging.getLogger(__name__)


def _impl(hotel_id: int, max_reviews: int = 10) -> dict[str, Any]:
    """Get aggregated guest reviews and average rating for a hotel."""
    if not hotel_id:
        return {
            "error": True,
            "message": "hotel_id is required",
            "error_type": "InvalidInput",
        }
    try:
        raw = call_hotel_guest_review(hotel_id=hotel_id)
        summary = parse_hotel_guest_review_response(raw)
        reviews = summary.get("reviews") or []
        summary["reviews"] = reviews[:max_reviews]
        summary["search_params"] = {"hotel_id": hotel_id}
        return summary
    except TripPlannerError as e:
        logger.warning("get_hotel_reviews error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("get_hotel_reviews unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


from mcp_tools.result_cache import cache_impl
get_hotel_reviews_tool = tool(cache_impl("get_hotel_reviews")(_impl))
get_hotel_reviews_tool.name = "get_hotel_reviews"
mcp.tool(name="get_hotel_reviews")(_impl)
