"""get_tour_options — agent + MCP tool.

List the bookable options/variants for a specific tour (e.g. "with transfer",
"ticket only"), each with its own optionId and rate. Backed by the B2C
/api/tours/options endpoint. Use after the customer picks a tour from
search_tours to show what variants are available.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_tour_options
from core import TripPlannerError
from mcp_tools.server import mcp

logger = logging.getLogger(__name__)


def _impl(tour_id: int, travel_date: str) -> dict[str, Any]:
    """Get the available options/variants for a tour.

    Args:
        tour_id: The tour's id (from search_tours).
        travel_date: ISO 'yyyy-mm-dd'.

    Returns the raw option list (each carries optionId, name, rate, supplierId)
    or an error dict.
    """
    try:
        raw = call_tour_options(tour_id=tour_id, travel_date=travel_date)
        return {"data": raw, "tour_id": tour_id, "travel_date": travel_date}
    except TripPlannerError as e:
        logger.warning("get_tour_options error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("get_tour_options unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


get_tour_options_tool = tool(_impl)
get_tour_options_tool.name = "get_tour_options"
mcp.tool(name="get_tour_options")(_impl)
