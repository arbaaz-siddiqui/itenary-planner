"""search_flights — agent + MCP tool."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_flight_search
from core import TripPlannerError
from mcp_tools.server import mcp
from parsers import parse_flight_response
from reference_data_loader import resolve_iata

logger = logging.getLogger(__name__)


def _impl(
    origin_city: str,
    destination_city: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    children: int = 0,
    child_ages: list[int] | None = None,
    cabin: str = "Y",
    max_stops: int = 2,
    max_results: int = 5,
) -> dict[str, Any]:
    """Search for flights.

    Args:
        origin_city: Indian source city name (e.g., "Delhi").
        destination_city: Destination (typically "Dubai").
        departure_date: ISO 'yyyy-mm-dd'.
        return_date: ISO; omit for one-way.
        adults: Adult passenger count.
        children: Child passenger count.
        child_ages: Length must match `children`.
        cabin: 'Y' economy / 'S' premium / 'C' business / 'F' first.
        max_stops: Max layovers.
        max_results: Cap on options returned.

    Returns:
        {options, cheapest_price_inr, total_results, search_params}
        Or {error: True, message: ...} on failure.
    """
    try:
        origin_iata = resolve_iata(origin_city)
        dest_iata = resolve_iata(destination_city)
        if not origin_iata:
            return {
                "error": True,
                "message": f"Unknown origin city: {origin_city!r}",
                "error_type": "UnsupportedRoute",
            }
        if not dest_iata:
            return {
                "error": True,
                "message": f"Unknown destination city: {destination_city!r}",
                "error_type": "UnsupportedRoute",
            }
        raw = call_flight_search(
            origin_iata=origin_iata,
            destination_iata=dest_iata,
            departure_date=departure_date,
            return_date=return_date,
            adults=adults,
            children=children,
            child_ages=child_ages,
            cabin=cabin,
            max_stops=max_stops,
        )
        options = parse_flight_response(
            raw,
            expected_origin=origin_iata,
            expected_destination=dest_iata,
            max_results=max_results,
        )
        return {
            "options": [o.model_dump() for o in options],
            "cheapest_price_inr": options[0].price_inr if options else None,
            "total_results": len(options),
            "search_params": {
                "origin": origin_city,
                "destination": destination_city,
                "departure_date": departure_date,
                "return_date": return_date,
                "adults": adults,
                "children": children,
            },
        }
    except TripPlannerError as e:
        logger.warning("search_flights error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("search_flights unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


search_flights_tool = tool(_impl)
search_flights_tool.name = "search_flights"
mcp.tool(name="search_flights")(_impl)
