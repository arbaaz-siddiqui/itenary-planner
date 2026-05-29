"""get_flight_details — agent + MCP tool.

After search_flights, the agent can drill into one option for full
fare-rules, baggage, terminal info, etc.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_flight_details
from core import TripPlannerError
from mcp_tools.server import mcp

logger = logging.getLogger(__name__)


def _impl(
    fare_source_code: str,
    itinerary_source_code: str = "",
    conversation_id: str = "",
) -> dict[str, Any]:
    """Get full details for a specific flight option.

    Args:
        fare_source_code: From search_flights result (`fare_source_code` field).
        itinerary_source_code: Same as fare_source_code if not provided separately.
        conversation_id: Optional session correlation ID from the search.

    Returns the raw API response so the agent can read every available
    field directly (no schema enforcement on details — they vary per
    supplier).
    """
    try:
        raw = call_flight_details(
            fare_source_code=fare_source_code,
            itinerary_source_code=itinerary_source_code or fare_source_code,
            conversation_id=conversation_id,
        )
        return {"data": raw, "fare_source_code": fare_source_code}
    except TripPlannerError as e:
        logger.warning("get_flight_details error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("get_flight_details unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


get_flight_details_tool = tool(_impl)
get_flight_details_tool.name = "get_flight_details"
mcp.tool(name="get_flight_details")(_impl)
