"""get_visa_info — agent + MCP tool.

NOTE: ActivityLinker returns ₹0 prices for our account (pricing permission
not enabled). The `pricing_available` flag tells UIs to show "On Request"
instead of misleading ₹0 values.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_visa_info
from core import TripPlannerError
from mcp_tools.server import mcp
from parsers import parse_visa_response
from reference_data_loader import resolve_country_id

logger = logging.getLogger(__name__)


def _impl(
    destination_country: str,
    nationality_country: str,
    travel_date: str,
    adults: int = 1,
    children: int = 0,
) -> dict[str, Any]:
    """Get visa requirements and (where available) pricing."""
    try:
        country_id = resolve_country_id(destination_country)
        nationality_id = resolve_country_id(nationality_country)
        if country_id is None:
            return {
                "error": True,
                "message": f"Unknown destination country: {destination_country!r}",
                "error_type": "UnsupportedRoute",
            }
        if nationality_id is None:
            return {
                "error": True,
                "message": f"Unknown nationality: {nationality_country!r}",
                "error_type": "UnsupportedRoute",
            }
        raw = call_visa_info(
            country_id=country_id,
            nationality_id=nationality_id,
            travel_date=travel_date,
            adults=adults,
            children=children,
        )
        options = parse_visa_response(raw)
        return {
            "options": [o.model_dump() for o in options],
            "total_results": len(options),
            "pricing_available": any(o.pricing_available for o in options),
            "search_params": {
                "destination": destination_country,
                "nationality": nationality_country,
                "travel_date": travel_date,
                "adults": adults,
                "children": children,
            },
        }
    except TripPlannerError as e:
        logger.warning("get_visa_info error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("get_visa_info unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


get_visa_info_tool = tool(_impl)
get_visa_info_tool.name = "get_visa_info"
mcp.tool(name="get_visa_info")(_impl)
