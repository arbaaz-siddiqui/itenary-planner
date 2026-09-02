"""search_restaurants — agent + MCP tool."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_restaurant_search
from core import TripPlannerError
from mcp_tools.server import mcp
from parsers import parse_restaurant_response
from reference_data_loader import resolve_city

logger = logging.getLogger(__name__)


def _impl(
    destination_city: str,
    search_date: str,
    adults: int = 2,
    children: int = 0,
    max_results: int = 5,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Search restaurants in the destination city."""
    try:
        city = resolve_city(destination_city)
        if city is None or not city.get("city_id"):
            return {
                "error": True,
                "message": f"Unsupported destination: {destination_city!r}",
                "error_type": "UnsupportedRoute",
            }
        raw = call_restaurant_search(
            city_id=int(city["city_id"]),
            search_date=search_date,
            adults=adults,
            children=children,
        )
        options = parse_restaurant_response(raw, max_results=max_results)

        def _row(o: Any) -> dict[str, Any]:
            # model_dump() drops @property values, so the display strings the
            # agent is told to quote have to be attached explicitly.
            d = o.model_dump()
            d["price_display"] = o.price_display
            d["rating_display"] = o.rating_display
            d["meal_timings_display"] = o.meal_timings_display
            return d

        return {
            "agent_instructions": (
                "Show `rating_display` for every restaurant — it carries the "
                "supplier's own verdict alongside the number (\"4.4 (Very "
                "Good)\"); never quote the number alone when a label exists. "
                "`meal_timings_display` gives the Breakfast / Lunch / Dinner "
                "service windows: show it whenever the customer asks about "
                "timings, meals or when a place is open. It is only populated "
                "by get_restaurant_details, so call that for a restaurant they "
                "are interested in rather than saying the hours are unknown. "
                "Never invent an hour or a rating, and never answer a rating or "
                "timing question from an earlier reply — pass that row's "
                "`restaurant_id` to get_restaurant_details and quote what comes "
                "back. Answering from recall reported a restaurant as 4 when the "
                "supplier says 4.4."
            ),
            "options": [_row(o) for o in options],
            "cheapest_price_inr": (options[0].price_per_adult_inr if options else None),
            "total_results": len(options),
            "search_params": {
                "destination": destination_city,
                "search_date": search_date,
                "adults": adults,
                "children": children,
            },
        }
    except TripPlannerError as e:
        logger.warning("search_restaurants error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("search_restaurants unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


from mcp_tools.result_cache import cache_impl
search_restaurants_tool = tool(cache_impl("search_restaurants")(_impl))
search_restaurants_tool.name = "search_restaurants"
mcp.tool(name="search_restaurants")(_impl)
