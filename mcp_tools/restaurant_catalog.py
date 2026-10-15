"""restaurant_catalog — everything we sell in a city, read in full.

Shared by `search_restaurants` and `get_restaurant_details` so the list and
the detail view cannot describe the same restaurant differently.

Why a search reads every restaurant's details
---------------------------------------------
The supplier's list call carries one primary cuisine and nothing else. The
facts a customer filters on -- every cuisine served, the feature tags ("Jain
Friendly", "Budget Friendly"), the menu, the per-day hours -- exist only on the
per-restaurant details call. Answering from the list alone is how Atithi Veg
was described as an Italian restaurant and how a Jain request was answered
from marketing copy instead of the supplier's own tag. Details are fetched in
parallel (bounded by RestaurantSettings), so the cost is one round trip.

Why `destination_city` can be an area
-------------------------------------
Asked for "restaurants in Dubai Marina" the model passes "Dubai Marina" as the
city. That resolved to nothing and the tool replied "we do not sell restaurants
in 'Dubai Marina' -- Dubai only", which is false: the Marina is in Dubai. An
unknown destination is now placed on the map first; if it lies in a city we
cover, the search runs in that city with the area as the place to measure from.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
from dataclasses import dataclass, field
from typing import Any

from core import (
    RestaurantDetailsFailed,
    RestaurantOption,
    RestaurantSearchFailed,
    format_inr,
)
from geo import Place, locate_place

logger = logging.getLogger(__name__)


@dataclass
class Destination:
    """Where a restaurant request points: a city we cover, and maybe a place in it."""

    city: dict[str, Any] | None
    #: The area/landmark to measure distances from, when one was named and found.
    place: Place | None = None
    #: The area/landmark as the customer named it ("" when none).
    near_asked: str = ""


@dataclass
class CityCatalog:
    restaurants: list[RestaurantOption]
    #: The supplier's own count for the city (`totalServices`).
    total_in_city: int
    #: Restaurants whose details could not be read; they carry list data only.
    not_detailed: list[str] = field(default_factory=list)


def resolve_destination(destination_city: str, near: str = "") -> Destination:
    """Turn the tool's city + optional place arguments into a Destination."""
    from reference_data_loader import resolve_city

    near = (near or "").strip()
    city = resolve_city(destination_city)
    if city is None or not city.get("city_id"):
        # Maybe an area of a city we cover ("Dubai Marina", "Palm Jumeirah").
        place = locate_place(destination_city)
        if place is None:
            return Destination(city=None, near_asked=near)
        city = resolve_city(place.city)
        if city is None or not city.get("city_id"):
            return Destination(city=None, near_asked=near)
        if not near:
            return Destination(city=city, place=place, near_asked=destination_city.strip())
    place = locate_place(near, str(city.get("name") or "")) if near else None
    return Destination(city=city, place=place, near_asked=near)


def _supplier_refusal(raw: Any) -> str | None:
    """The supplier's reason when it answered with an error body instead of data.

    The HTTP client returns body-level errors (`{"statusCode": 400, "error":
    {...}, "result": null}`) as ordinary bodies. Parsed as data they read as
    "no restaurants", so a past date came back to the customer as an empty
    city instead of "choose today or a later date".
    """
    if not isinstance(raw, dict) or raw.get("result") is not None:
        return None
    status = raw.get("statusCode")
    if not isinstance(status, int) or status < 400:
        return None
    err = raw.get("error") or {}
    reason = err.get("description") or err.get("code") if isinstance(err, dict) else err
    return str(reason or f"supplier status {status}").strip()


def _list_city(city_id: int, search_date: str, adults: int, children: int) -> tuple[list[RestaurantOption], int]:
    from booking_api import call_restaurant_search
    from booking_api.endpoints import RESTAURANT_LIST_PATH
    from parsers import parse_restaurant_response

    raw = call_restaurant_search(
        city_id=city_id, search_date=search_date, adults=adults, children=children
    )
    refusal = _supplier_refusal(raw)
    if refusal:
        raise RestaurantSearchFailed(refusal, endpoint=RESTAURANT_LIST_PATH)
    result = (raw or {}).get("result") if isinstance(raw, dict) else None
    total = int((result or {}).get("totalServices") or 0) if isinstance(result, dict) else 0
    listed = parse_restaurant_response(raw)
    return listed, max(total, len(listed))


def fetch_restaurant(
    restaurant_id: int, *, city_id: int, search_date: str, adults: int = 1, children: int = 0
) -> RestaurantOption | None:
    """One restaurant, fully described (cuisines, tags, menu, per-day hours)."""
    from booking_api import call_restaurant_details
    from parsers import parse_restaurant_response

    raw = call_restaurant_details(
        restaurant_id=restaurant_id,
        city_id=city_id,
        search_date=search_date,
        adults=adults,
        children=children,
    )
    refusal = _supplier_refusal(raw)
    if refusal:
        raise RestaurantDetailsFailed(refusal, endpoint=f"restaurant details {restaurant_id}")
    parsed = parse_restaurant_response(raw)
    return parsed[0] if parsed else None


def load_city_catalog(
    city: dict[str, Any], search_date: str, adults: int = 2, children: int = 0
) -> CityCatalog:
    """Every restaurant the supplier lists for the city, each read in full.

    Supplier list order is kept. A restaurant whose details call fails keeps
    its list data and is named in `not_detailed`, so the caller can say which
    ones are described from partial data instead of silently dropping them.
    """
    from settings import get_restaurant_settings

    cfg = get_restaurant_settings()
    city_id = int(city["city_id"])
    listed, total = _list_city(city_id, search_date, adults, children)
    if not listed:
        return CityCatalog(restaurants=[], total_in_city=total)

    to_read = listed[: cfg.detail_fanout]
    detailed: dict[int, RestaurantOption] = {}
    workers = max(1, min(cfg.detail_workers, len(to_read)))
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(
                fetch_restaurant,
                r.restaurant_id,
                city_id=city_id,
                search_date=search_date,
                adults=adults,
                children=children,
            ): r.restaurant_id
            for r in to_read
        }
        for fut in cf.as_completed(futures):
            rid = futures[fut]
            try:
                full = fut.result()
            except Exception as e:  # one bad detail must not sink the list
                logger.warning("restaurant details failed id=%s: %s", rid, e)
                continue
            if full is not None:
                detailed[rid] = full

    restaurants = [detailed.get(r.restaurant_id, r) for r in listed]
    not_detailed = [r.name for r in listed if r.restaurant_id not in detailed]
    return CityCatalog(restaurants=restaurants, total_in_city=total, not_detailed=not_detailed)


def _distinct(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        key = v.strip().lower()
        if v.strip() and key not in seen:
            seen.add(key)
            out.append(v.strip())
    return out


def coverage(catalog: CityCatalog, city_name: str) -> dict[str, Any]:
    """What we sell in the city, derived from the catalogue itself.

    This is what lets the agent answer "do you have fine dining in the
    Marina?" truthfully: if "fine dining" is not a feature and the Marina is
    not an area here, the answer is no -- instead of offering a search that
    can only come back with the same restaurants.
    """
    rs = catalog.restaurants
    prices = [r.price_per_adult_inr for r in rs if r.price_per_adult_inr]
    return {
        "city": city_name,
        "restaurants_in_city": catalog.total_in_city,
        # Every restaurant, compactly, with its id. A filtered search used to
        # carry names only: asked next for "Rangoli's menu" after a dosa search,
        # the agent said Rangoli was not found, and asked for "all of them" it
        # padded a table with blank rows. With the id and the headline facts
        # here, both are answered from data.
        "restaurants": [
            {
                "restaurant_id": r.restaurant_id,
                "name": r.name,
                "area": r.area,
                "rating_display": r.rating_display,
                "price_from_display": r.price_from_display,
                "tags": r.tags,
            }
            for r in rs
        ],
        "restaurant_types": _distinct([r.veg_type for r in rs]),
        "areas": _distinct([r.area for r in rs]),
        "cuisines": _distinct([c for r in rs for c in r.cuisines]),
        "features": _distinct([t for r in rs for t in r.tags]),
        "starting_prices_display": (
            f"{format_inr(min(prices))} – {format_inr(max(prices))} per person"
            if prices else ""
        ),
    }
