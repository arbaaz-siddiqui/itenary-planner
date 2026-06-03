"""search_hotels — agent + MCP tool."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_hotel_availability
from core import TripPlannerError, nights_between
from mcp_tools.server import mcp
from parsers import parse_hotel_response
from reference_data_loader import (
    get_hotel_areas,
    get_hotel_ids_for_city,
    get_hotel_names,
    resolve_city,
)

logger = logging.getLogger(__name__)


def _impl(
    destination_city: str,
    check_in: str,
    check_out: str,
    adults: int = 2,
    children: int = 0,
    child_ages: list[int] | None = None,
    rooms: list[dict[str, Any]] | None = None,
    nationality: str = "India",
    min_stars: float = 0,
    max_stars: float = 5,
    max_results: int = 5,
) -> dict[str, Any]:
    """Search hotels in the destination city. Returns options + per-night pricing.

    Occupancy — two ways to specify:
    - For a single room, pass flat `adults` / `children` / `child_ages`.
    - For multiple rooms (e.g. a family of 4 split as 2+2, or 3+1), pass
      `rooms` as a list of per-room dicts, each with `adults`, `children`,
      and `child_ages`. Example for 4 adults + 2 kids (ages 5, 8) in 2 rooms:
        rooms=[{"adults": 2, "children": 1, "child_ages": [5]},
               {"adults": 2, "children": 1, "child_ages": [8]}]
      When `rooms` is given it takes precedence over the flat args. ALWAYS
      confirm the room split with the customer before searching — never guess
      how many rooms 4+ guests want.
    """
    try:
        city = resolve_city(destination_city)
        if city is None or not city.get("city_id"):
            return {
                "error": True,
                "message": f"Unsupported hotel destination: {destination_city!r}",
                "error_type": "UnsupportedRoute",
            }
        city_id = int(city["city_id"])
        city_key = city["name"].lower()
        hotel_ids = get_hotel_ids_for_city(city_key)
        if not hotel_ids:
            return {
                "error": True,
                "message": (
                    f"No hotel IDs configured for {city['name']}. "
                    "Update reference_data/hotels/<city>.json."
                ),
                "error_type": "MissingReferenceData",
            }
        nights = nights_between(check_in, check_out)
        room_count = len(rooms) if rooms else 1
        raw = call_hotel_availability(
            hotel_ids=hotel_ids,
            city_id=city_id,
            check_in=check_in,
            check_out=check_out,
            rooms=rooms,
            adults=adults,
            children=children,
            child_ages=child_ages,
            nationality=nationality,
            star_min=int(min_stars) if min_stars > 0 else 1,
            star_max=int(max_stars) if max_stars > 0 else 5,
        )
        options = parse_hotel_response(
            raw,
            nights=nights,
            hotel_names=get_hotel_names(city_key),
            hotel_areas=get_hotel_areas(city_key),
            max_results=max_results * 2,
        )
        options = [o for o in options if min_stars <= o.stars <= max_stars][:max_results]
        return {
            "options": [o.model_dump() for o in options],
            "cheapest_price_inr": options[0].price_inr if options else None,
            "nights": nights,
            "per_night_inr": options[0].per_night_inr if options else None,
            "room_count": room_count,
            "total_results": len(options),
            "search_params": {
                "destination": destination_city,
                "check_in": check_in,
                "check_out": check_out,
                "adults": adults,
                "children": children,
                "rooms": rooms,
                "room_count": room_count,
            },
        }
    except TripPlannerError as e:
        logger.warning("search_hotels error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("search_hotels unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


search_hotels_tool = tool(_impl)
search_hotels_tool.name = "search_hotels"
mcp.tool(name="search_hotels")(_impl)
