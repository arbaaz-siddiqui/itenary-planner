"""get_transfer_details — agent + MCP tool.

After search_transfers, pull full details for the selected unique_key.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_transfer_details
from core import TripPlannerError
from mcp_tools.server import mcp

logger = logging.getLogger(__name__)


def _impl(
    unique_key: str,
    from_lat: float,
    from_lng: float,
    to_lat: float,
    to_lng: float,
    from_place_id: str,
    to_place_id: str,
    departure_date: str,
    departure_time: str = "07:00:00",
    return_date: str | None = None,
    return_time: str = "07:00:00",
    is_round_trip: bool = False,
    adults: int = 1,
) -> dict[str, Any]:
    """Get details for a specific transfer (uniqueKey comes from search_transfers result)."""
    try:
        raw = call_transfer_details(
            from_lat=from_lat,
            from_lng=from_lng,
            to_lat=to_lat,
            to_lng=to_lng,
            from_place_id=from_place_id,
            to_place_id=to_place_id,
            departure_date=departure_date,
            departure_time=departure_time,
            return_date=return_date,
            return_time=return_time,
            is_round_trip=is_round_trip,
            adults=adults,
            unique_key=unique_key,
        )
        return {"data": raw, "unique_key": unique_key}
    except TripPlannerError as e:
        logger.warning("get_transfer_details error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("get_transfer_details unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


get_transfer_details_tool = tool(_impl)
get_transfer_details_tool.name = "get_transfer_details"
mcp.tool(name="get_transfer_details")(_impl)
