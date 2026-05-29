"""get_package_details — agent + MCP tool.

Fetch static (non-pricing) data for a package: itinerary, inclusions,
images, terms.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_package_static_data
from core import TripPlannerError
from mcp_tools.server import mcp

logger = logging.getLogger(__name__)


def _impl(package_id: int) -> dict[str, Any]:
    """Get static package data (no rates)."""
    try:
        raw = call_package_static_data(package_id=package_id)
        return {"data": raw, "package_id": package_id}
    except TripPlannerError as e:
        logger.warning("get_package_details error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("get_package_details unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


get_package_details_tool = tool(_impl)
get_package_details_tool.name = "get_package_details"
mcp.tool(name="get_package_details")(_impl)
