"""get_flight_details — agent + MCP tool.

After search_flights, the agent can drill into one option for its fare rules
(refund/change penalties), baggage, terminals and live price.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_flight_details
from core import TripPlannerError
from flight_cache import find_option
from mcp_tools.server import mcp

logger = logging.getLogger(__name__)


def _impl(
    fare_source_code: str,
    itinerary_source_code: str = "",
    conversation_id: str = "",
) -> dict[str, Any]:
    """Get full details for a specific flight option.

    Args:
        fare_source_code: The option's `fare_ref` from search_flights (or a full
            fare_source_code).
        itinerary_source_code: Only needed with a full fare_source_code that is
            not in the recent search cache.
        conversation_id: Only needed with a full fare_source_code that is not
            in the recent search cache.

    Returns the option re-priced by the supplier with its penalties (refund and
    change rules) and per-leg baggage, or an error saying why it could not be
    fetched. Quote refund terms only from `penalties`; when that list is empty
    the airline has not published them.
    """
    try:
        cached = find_option(fare_source_code)
        if cached is not None:
            fare_source_code = str(cached.get("fare_source_code") or fare_source_code)
            itinerary_source_code = (
                str(cached.get("itinerary_source_code") or "") or itinerary_source_code
            )
            conversation_id = str(cached.get("conversation_id") or "") or conversation_id
        if not conversation_id:
            # The supplier answers flightdetails with an empty body unless the
            # fare is tied to the search that produced it.
            return {
                "error": True,
                "error_type": "FareExpired",
                "message": "That fare is no longer in the recent search results.",
                "agent_instructions": (
                    "Run search_flights again with force_refresh=True and use the "
                    "new fare_ref; fares expire within minutes."
                ),
            }

        raw = call_flight_details(
            fare_source_code=fare_source_code,
            itinerary_source_code=itinerary_source_code or fare_source_code,
            conversation_id=conversation_id,
        )
        option = _parse_details(raw, cached)
        if option is None:
            return {
                "error": True,
                "error_type": "FareExpired",
                "message": "The airline no longer offers this fare.",
                "agent_instructions": (
                    "Tell the customer this fare has gone and run search_flights "
                    "again with force_refresh=True."
                ),
            }
        return {"option": option, "fare_source_code": fare_source_code}
    except TripPlannerError as e:
        logger.warning("get_flight_details error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("get_flight_details unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


def _parse_details(raw: dict[str, Any], cached: dict[str, Any] | None) -> dict[str, Any] | None:
    """The details response has the search shape; reuse the search parsing."""
    from mcp_tools.search_flights import _slim_for_model, _to_dict
    from parsers import parse_flight_response

    options = parse_flight_response(raw)
    if not options:
        return None
    pax = int((cached or {}).get("pax_count") or options[0].pax_count or 1)
    detail = _to_dict(options[0], pax)
    slim = _slim_for_model(detail)
    # The penalties are the point of this call; the search copy drops them.
    slim["penalties"] = detail.get("penalties") or []
    return slim


from mcp_tools.result_cache import cache_impl
get_flight_details_tool = tool(cache_impl("get_flight_details")(_impl))
get_flight_details_tool.name = "get_flight_details"
mcp.tool(name="get_flight_details")(_impl)
