"""get_restaurant_details — agent + MCP tool."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_restaurant_details
from core import TripPlannerError
from mcp_tools.server import mcp
from reference_data_loader import resolve_city

logger = logging.getLogger(__name__)


def _impl(
    restaurant_id: int,
    destination_city: str,
    search_date: str,
    adults: int = 1,
    children: int = 0,
) -> dict[str, Any]:
    """Get details for a specific restaurant (id from search_restaurants result)."""
    try:
        city = resolve_city(destination_city)
        if city is None or not city.get("city_id"):
            return {
                "error": True,
                "message": f"Unsupported destination: {destination_city!r}",
                "error_type": "UnsupportedRoute",
            }
        from parsers import parse_restaurant_response
        from fx import live_rate_map
        from core import to_inr
        raw = call_restaurant_details(
            restaurant_id=restaurant_id,
            city_id=int(city["city_id"]),
            search_date=search_date,
            adults=adults,
            children=children,
        )
        options = parse_restaurant_response(raw)
        restaurant = options[0].model_dump() if options else None
        # Also extract dish rates with INR prices for easy agent access
        dishes: list[dict] = []
        result = raw.get("result") or {}
        detail_list = result.get("detail") or [] if isinstance(result, dict) else []
        detail = detail_list[0] if detail_list else {}
        rates = live_rate_map()
        image_base = "https://stagingapi.gujjutours.com"
        for dish in detail.get("dishAndBuffetRates") or []:
            if not isinstance(dish, dict):
                continue
            fare_infos = dish.get("fareInfo") or []
            price_inr = None
            rate_key = None
            pax_rate_key = None
            for fi in fare_infos:
                if isinstance(fi, dict) and fi.get("paxType", "").lower() == "adult":
                    try:
                        currency = (fi.get("currency") or "AED").strip()
                        price_inr = to_inr(float(fi["price"]), currency, rates=rates)
                        rate_key = dish.get("rateKey")
                        pax_rate_key = fi.get("paxRateKey")
                    except Exception:
                        pass
                    break
            dish_images = [
                f"{image_base}/{img['imagePath']}"
                for img in dish.get("dishImages") or []
                if isinstance(img, dict) and img.get("imagePath")
            ]
            dishes.append({
                "dish_id": dish.get("dishId"),
                "name": dish.get("dishName"),
                "description": dish.get("description", ""),
                "cuisine": dish.get("cuisineName"),
                "price_inr": round(price_inr) if price_inr else None,
                "rate_key": rate_key,
                "pax_rate_key": pax_rate_key,
                "images": dish_images,
            })
        return {
            "restaurant": restaurant,
            "dishes": dishes,
            "restaurant_id": restaurant_id,
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
