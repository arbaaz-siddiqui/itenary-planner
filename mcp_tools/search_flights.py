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

        # The flight API returns the FULL-party total AND a per-passenger
        # breakdown. Indian travel agents quote per-person, so we surface both.
        # Prefer the supplier's REAL per-adult fare (from the breakdown); only
        # fall back to total ÷ pax when the breakdown is absent. This keeps the
        # per-person number tool-sourced, never guessed.
        searched_pax = max(1, adults + children)

        def _per_adult(o: Any) -> float:
            if o.price_per_adult_inr is not None and o.price_per_adult_inr > 0:
                return round(o.price_per_adult_inr, 2)
            pax = o.pax_count or searched_pax
            return round(o.price_inr / pax, 2) if pax else o.price_inr

        opts_out = []
        for o in options:
            d = o.model_dump()
            d["price_total_inr"] = o.price_inr
            d["price_per_adult_inr"] = _per_adult(o)
            d["pax_count"] = o.pax_count or searched_pax
            opts_out.append(d)

        return {
            "options": opts_out,
            "cheapest_price_inr": options[0].price_inr if options else None,
            "cheapest_price_per_adult_inr": _per_adult(options[0]) if options else None,
            "total_results": len(options),
            "pricing_note": "price_total_inr is for the whole party; price_per_adult_inr is per adult.",
            "search_params": {
                "origin": origin_city,
                "destination": destination_city,
                "departure_date": departure_date,
                "return_date": return_date,
                "adults": adults,
                "children": children,
                "pax_count": searched_pax,
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
