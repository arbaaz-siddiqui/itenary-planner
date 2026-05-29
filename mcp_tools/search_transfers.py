"""search_transfers — agent + MCP tool.

Defaults to the airport→hotel pattern for Dubai. The agent passes hotel
coordinates; we use the city's default airport from reference_data.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_transfer_search
from core import TripPlannerError
from mcp_tools.server import mcp
from parsers import parse_transfer_response
from reference_data_loader import get_default_dubai_airport

logger = logging.getLogger(__name__)


def _impl(
    hotel_lat: float,
    hotel_lng: float,
    arrival_date: str,
    arrival_time: str = "12:00",
    return_date: str | None = None,
    return_time: str = "12:00",
    adults: int = 2,
    max_results: int = 5,
) -> dict[str, Any]:
    """Search Dubai airport → hotel transfers (round trip by default)."""
    try:
        airport = get_default_dubai_airport()
        if not airport:
            return {
                "error": True,
                "message": "Dubai airport coordinates not configured",
                "error_type": "MissingReferenceData",
            }
        raw = call_transfer_search(
            from_lat=float(airport["lat"]),
            from_lng=float(airport["lng"]),
            to_lat=hotel_lat,
            to_lng=hotel_lng,
            from_place_id=str(airport.get("place_id", "DXB")),
            to_place_id="HOTEL",
            departure_date=arrival_date,
            departure_time=arrival_time,
            return_date=return_date,
            return_time=return_time,
            is_round_trip=return_date is not None,
            from_type="A",
            to_type="O",
            adults=adults,
        )
        options = parse_transfer_response(raw, max_results=max_results)
        return {
            "options": [o.model_dump() for o in options],
            "cheapest_price_inr": options[0].price_inr if options else None,
            "total_results": len(options),
            "search_params": {
                "hotel_lat": hotel_lat,
                "hotel_lng": hotel_lng,
                "arrival_date": arrival_date,
                "return_date": return_date,
                "adults": adults,
            },
        }
    except TripPlannerError as e:
        logger.warning("search_transfers error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("search_transfers unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


search_transfers_tool = tool(_impl)
search_transfers_tool.name = "search_airport_transfer_dubai"
mcp.tool(name="search_airport_transfer_dubai")(_impl)
