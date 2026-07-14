"""list_visa_countries — agent + MCP tool.

Returns the 4 destinations for which visa inventory exists on this platform:
UAE, Oman, Egypt, Singapore. Use this to resolve a destination name to the
countryId required by get_visa_info.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_visa_countries
from core import TripPlannerError
from mcp_tools.server import mcp

logger = logging.getLogger(__name__)


def _impl() -> dict[str, Any]:
    """List countries for which visa information is available.

    Returns country names, IDs, visa types, and processing times.
    Use countryId from this response as input to get_visa_info.

    Example response:
        {
          "countries": [
            {"countryId": 213, "countryName": "United Arab Emirates", "countryCode": "AE",
             "visaType": "Tourist Visa", "processingTime": "3-4 Working Days"},
            ...
          ],
          "total": 4
        }
    """
    try:
        raw = call_visa_countries()
        countries = raw.get("result") or []
        simplified = [
            {
                "countryId": c.get("countryId"),
                "countryName": c.get("countryName"),
                "countryCode": c.get("countryCode"),
                "visaType": c.get("visaType"),
                "visaCategoryType": c.get("visaCategoryType"),
                "processingTime": c.get("processingTime"),
            }
            for c in countries
            if isinstance(c, dict)
        ]
        return {
            "countries": simplified,
            "total": len(simplified),
        }
    except TripPlannerError as e:
        logger.warning("list_visa_countries error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("list_visa_countries unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


from mcp_tools.result_cache import cache_impl
list_visa_countries_tool = tool(cache_impl("list_visa_countries")(_impl))
list_visa_countries_tool.name = "list_visa_countries"
mcp.tool(name="list_visa_countries")(_impl)
