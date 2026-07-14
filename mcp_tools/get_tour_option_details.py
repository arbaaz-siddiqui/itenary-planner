"""get_tour_option_details — agent + MCP tool.

Full detail for a specific tour OPTION: pricing, inclusions, cancellation policy,
and booking rules. Backed by the B2C /api/tours/option-details endpoint. Use
after get_tour_options when the customer wants the specifics of one variant.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_tour_option_details
from core import TripPlannerError
from mcp_tools.server import mcp

logger = logging.getLogger(__name__)


def _impl(tour_id: int, tour_option_id: str, supplier_id: int) -> dict[str, Any]:
    """Get full detail (price, inclusions, cancellation) for a tour option.

    Args:
        tour_id: The tour's id (from search_tours).
        tour_option_id: The option id (from get_tour_options).
        supplier_id: The supplier id (from search_tours / get_tour_options).

    Returns the raw option detail or an error dict.
    """
    try:
        raw = call_tour_option_details(
            tour_id=tour_id, tour_option_id=tour_option_id, supplier_id=supplier_id
        )
        return {"data": raw, "tour_id": tour_id, "tour_option_id": tour_option_id}
    except TripPlannerError as e:
        logger.warning("get_tour_option_details error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("get_tour_option_details unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


from mcp_tools.result_cache import cache_impl
get_tour_option_details_tool = tool(cache_impl("get_tour_option_details")(_impl))
get_tour_option_details_tool.name = "get_tour_option_details"
mcp.tool(name="get_tour_option_details")(_impl)
