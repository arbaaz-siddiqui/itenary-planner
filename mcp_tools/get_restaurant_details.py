"""get_restaurant_details — agent + MCP tool.

One restaurant in full: rating, cuisines, feature tags, the priced menu
(adult and child price per item), opening hours per weekday, meal windows,
and the hours that apply on the requested date.

Two things this deliberately does NOT claim:
- Table availability. The supplier publishes none (the website books on date,
  guests and dishes only). "Is it available on the 21st?" is answered with the
  published hours for that weekday, said as such -- never as a confirmation.
- A meal price. `price_from_display` is the cheapest menu item; meal costs are
  read from `dishes`.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from langchain_core.tools import tool

from core import RestaurantDish, TripPlannerError, supplier_today
from mcp_tools.restaurant_catalog import fetch_restaurant, resolve_destination
from mcp_tools.search_restaurants import restaurant_row, unsupported_destination
from mcp_tools.server import mcp

logger = logging.getLogger(__name__)


def _dish_row(d: RestaurantDish) -> dict[str, Any]:
    # Keys up to `images` are the original shape and stay as they were;
    # `price_inr` now keeps paise (1624.75), matching the website.
    return {
        "dish_id": d.dish_id,
        "name": d.name,
        "description": d.description,
        "cuisine": d.cuisine,
        "price_inr": d.adult_price_inr,
        "rate_key": d.rate_key or None,
        "pax_rate_key": d.pax_rate_key or None,
        "images": d.image_urls,
        "child_price_inr": d.child_price_inr,
        "price_display": d.price_display,
        "child_price_display": d.child_price_display,
    }


def _impl(
    restaurant_id: int,
    destination_city: str,
    search_date: str = "",
    adults: int = 1,
    children: int = 0,
) -> dict[str, Any]:
    """Get one restaurant's full details: menu with prices, rating, cuisines,
    feature tags, opening hours per day and the hours on `search_date`
    (YYYY-MM-DD, optional: today when left out). `restaurant_id` comes
    from search_restaurants."""
    search_date = (search_date or "").strip() or supplier_today().isoformat()
    try:
        dest = resolve_destination(destination_city)
        if dest.city is None:
            return unsupported_destination(destination_city)
        found = fetch_restaurant(
            int(restaurant_id),
            city_id=int(dest.city["city_id"]),
            search_date=search_date,
            adults=adults,
            children=children,
        )
        if found is None:
            return {
                "error": True,
                "error_type": "NotFound",
                "message": (
                    f"We do not sell a restaurant with id {restaurant_id} in "
                    f"{dest.city.get('name') or destination_city}."
                ),
                "agent_instructions": (
                    "Do not describe this restaurant from memory. Call "
                    "search_restaurants for the city and use an id it returns."
                ),
            }
        try:
            on: date | None = date.fromisoformat(str(search_date)[:10])
        except ValueError:
            on = None
        # The card gets every dish photo; the agent's `restaurant` view keeps
        # them out because `dishes` already lists each dish with its images.
        card = restaurant_row(found, on=on, photo_limit=None)
        restaurant = {k: v for k, v in card.items() if k != "dish_photos"}
        return {
            "agent_instructions": (
                "Quote `rating_display` (number + the supplier's verdict) and "
                "`meal_timings_display` verbatim. The meal windows are per meal "
                "— Breakfast / Lunch / Dinner, each with its own hours — so show "
                "them as separate lines or a small table when the customer asks "
                "about timings. `meal_timings` has the weekdays each window "
                "applies to; `hours_on_date` is what applies on the requested "
                "date, and `opening_hours_display` is the week. An empty value "
                "means the supplier published none: say so, never guess an "
                "hour. These are published hours, not a table availability "
                "check — the supplier publishes no availability — so say 'its "
                "published dinner hours on <day> are ...', never that it is "
                "available or open for you. Prices: quote `dishes[].price_display` "
                "(and `child_price_display` when a child price exists); "
                "`price_from_display` is the cheapest item, not a meal. Describe "
                "cuisines from `cuisines` and features only from `tags`."
            ),
            "restaurant": restaurant,
            "dishes": [_dish_row(d) for d in found.dishes],
            "restaurant_id": found.restaurant_id,
            # The same restaurant as a card, with its full photo menu. The chat
            # API renders a tool result's `options` as cards.
            "options": [card],
        }
    except TripPlannerError as e:
        logger.warning("get_restaurant_details error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("get_restaurant_details unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


from mcp_tools.result_cache import cache_impl
get_restaurant_details_tool = tool(cache_impl("get_restaurant_details")(_impl))
get_restaurant_details_tool.name = "get_restaurant_details"
mcp.tool(name="get_restaurant_details")(_impl)
