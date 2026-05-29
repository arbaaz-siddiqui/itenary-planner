"""get_tour_details — agent + MCP tool.

Fetch full description, itinerary, inclusions, exclusions for one tour.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_tour_details
from core import TripPlannerError
from mcp_tools.server import mcp

logger = logging.getLogger(__name__)


def _impl(tour_id: int) -> dict[str, Any]:
    """Get full details for a tour by its tour_id."""
    try:
        raw = call_tour_details(tour_id=tour_id)
        return {"data": raw, "tour_id": tour_id}
    except TripPlannerError as e:
        logger.warning("get_tour_details error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("get_tour_details unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


get_tour_details_tool = tool(_impl)
get_tour_details_tool.name = "get_tour_details"
mcp.tool(name="get_tour_details")(_impl)
