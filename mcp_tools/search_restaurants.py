"""search_restaurants — agent + MCP tool.

Answers every restaurant question from the supplier's own data: which ones
we sell in a city, which serve a cuisine or a dish, which carry a feature tag
(Jain friendly, budget, family), which are closest to a named area or
landmark, and -- just as important -- that we sell nothing matching.

What used to go wrong, and where it is handled now
--------------------------------------------------
- Only the 5 cheapest were returned and reported as `total_results: 5`, so
  the highest-rated restaurants (4.7, 4.8) were never shown and the agent
  believed the city had five. -> every match is returned up to `max_results`,
  and `coverage` states the full catalogue.
- No way to ask by area. "Restaurants in Deira" got the Karama list, labelled
  "in Deira". -> `near` places the area and ranks by straight-line distance.
- No way to ask by cuisine, dish or feature, so the agent filled the gap from
  memory. -> `cuisine`, `dish`, `feature` filter on supplier fields only.
- A named restaurant we do not sell was described from general knowledge.
  -> `restaurant_name` answers "do you have X" against our catalogue.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from langchain_core.tools import tool

from core import RestaurantOption, TripPlannerError, supplier_today
from mcp_tools.restaurant_catalog import coverage, load_city_catalog, resolve_destination
from mcp_tools.server import mcp
from text_match import matches, matches_any

logger = logging.getLogger(__name__)

_SORTS = ("price", "rating", "distance")


def unsupported_destination(destination_city: str) -> dict[str, Any]:
    return {
        "error": True,
        "message": (
            f"We do not sell restaurants in {destination_city!r} — the "
            f"supplier's inventory for this service is Dubai only."
        ),
        "error_type": "UnsupportedRoute",
        # A bare "Unsupported destination" got paraphrased to the
        # customer as "I don't have that in my database", which reads
        # as OUR system being broken rather than us not selling it.
        "agent_instructions": (
            "Say plainly that we do not offer restaurants in that city — we "
            "cover Dubai for this service. Never say the data is "
            "missing, unavailable, or not in your database, and never "
            "imply a technical problem. Offer the Dubai equivalent "
            "instead, and do not ask the customer for more details "
            "first — the city is the blocker, not their input."
        ),
    }


#: Dish photos carried on each search row, for the card's photo strip. The
#: full menu (every dish, every photo) comes from get_restaurant_details.
SEARCH_DISH_PHOTOS = 4

#: Matched menu items listed per restaurant for a dish or budget search.
MATCHING_DISHES_SHOWN = 8


def dish_photos(
    o: RestaurantOption, *, limit: int | None = None, first: list[str] | None = None
) -> list[dict[str, Any]]:
    """Dishes that have a photo, as {name, price_display, image_url}.

    `first` puts the named dishes (the ones a dish search matched) ahead of
    the rest, so a "where can I get dosa" card shows the dosa.
    """
    lead = {n.lower() for n in first or []}
    ordered = sorted(o.dishes, key=lambda d: d.name.lower() not in lead)
    out = [
        {
            "name": d.name,
            "cuisine": d.cuisine,
            "price_display": d.price_display,
            "child_price_display": d.child_price_display,
            "image_url": d.image_urls[0],
        }
        for d in ordered if d.image_urls
    ]
    return out if limit is None else out[:limit]


def restaurant_row(
    o: RestaurantOption,
    *,
    on: date | None = None,
    photo_limit: int | None = SEARCH_DISH_PHOTOS,
    lead_dishes: list[str] | None = None,
) -> dict[str, Any]:
    """One restaurant as the agent and the card see it.

    model_dump() drops @property values, so every display string the agent is
    told to quote is attached explicitly. The menu itself stays out of search
    rows (30+ dishes each); get_restaurant_details carries it. `dish_photos`
    is what the card shows of the food.
    """
    d = o.model_dump(exclude={"dishes"})
    d["dish_photos"] = dish_photos(o, limit=photo_limit, first=lead_dishes)
    d["price_display"] = o.price_display
    d["price_from_display"] = o.price_from_display
    d["menu_price_range_display"] = o.menu_price_range_display
    d["menu_items"] = len(o.dishes)
    d["rating_display"] = o.rating_display
    d["meal_timings_display"] = o.meal_timings_display
    d["opening_hours_display"] = o.opening_hours_display
    if on is not None:
        d["hours_on_date"] = o.schedule_on(on)
    return d


def _parse_day(search_date: str) -> date | None:
    try:
        return date.fromisoformat(str(search_date)[:10])
    except ValueError:
        return None


def _apply_filters(
    rows: list[RestaurantOption],
    *,
    restaurant_name: str,
    cuisine: str,
    dish: str,
    feature: str,
    max_price_inr: float | None = None,
) -> tuple[list[RestaurantOption], dict[int, list[str]]]:
    """Restaurants matching every filter given, and the dishes that matched.

    Filters read supplier fields only: the name, `cuisines`, the menu, and the
    feature `tags` plus the restaurant type. Never the marketing description:
    Rangoli's says "Jain diet" while the supplier does not tag it Jain
    Friendly, and repeating the description is how it was recommended as
    Jain-friendly above five restaurants that are tagged so.

    `dish` and `max_price_inr` both select menu items; given together, an
    item must satisfy both. The matched items come back cheapest first.
    """
    dish_hits: dict[int, list[str]] = {}
    out: list[RestaurantOption] = []
    for r in rows:
        if restaurant_name and not matches(restaurant_name, r.name):
            continue
        if cuisine and not matches_any(cuisine, r.cuisines):
            continue
        if feature and not matches_any(feature, [*r.tags, r.veg_type]):
            continue
        if dish or max_price_inr is not None:
            items = [
                d for d in r.dishes
                if (not dish or matches(dish, d.name))
                and (
                    max_price_inr is None
                    or (d.adult_price_inr is not None and d.adult_price_inr <= max_price_inr)
                )
            ]
            if not items:
                continue
            items.sort(key=lambda d: d.adult_price_inr or 0.0)
            dish_hits[r.restaurant_id] = [d.name for d in items]
        out.append(r)
    return out, dish_hits


def _sort(rows: list[RestaurantOption], sort_by: str) -> list[RestaurantOption]:
    if sort_by == "rating":
        return sorted(rows, key=lambda r: (-r.rating, r.price_per_adult_inr))
    if sort_by == "distance":
        return sorted(
            rows,
            key=lambda r: (r.distance_km is None, r.distance_km or 0.0, r.price_per_adult_inr),
        )
    # The website's own default order: starting price, low to high.
    return sorted(rows, key=lambda r: r.price_per_adult_inr)


def _instructions(
    *,
    city_name: str,
    total: int,
    filters: dict[str, str],
    shown: int,
    near_asked: str,
    place_name: str | None,
    not_detailed: list[str],
) -> str:
    asked_for = ", ".join(f"{k}={v!r}" for k, v in filters.items())
    if near_asked:
        asked_for = ", ".join(x for x in (asked_for, f"near={near_asked!r}") if x)
    parts = [
        f"This result answers ONLY: {asked_for or 'all restaurants, no filter'}. "
        "When the customer next asks with a different area, cuisine, dish, "
        "feature, budget or name, call search_restaurants again with that "
        "argument — do not re-filter, re-rank or estimate distances from these "
        "rows yourself; they cannot answer it.",
        f"These come from the {total} restaurant(s) we sell in {city_name}; "
        "`coverage` lists every one of them with every area, cuisine and "
        "feature tag they have. Recommend only these, and never offer a search "
        "for a cuisine, feature or area that `coverage` does not contain.",
        "Show `rating_display` for every restaurant. `price_from_display` is "
        "the cheapest item on the menu, not the cost of a meal — say it as a "
        "'from' price; `menu_price_range_display` is the range PER MENU ITEM, "
        "never label it per person. Describe a restaurant with its `tags` in "
        "the supplier's own words ('Premium Dining', 'Jain Friendly'); never "
        "upgrade them ('fine dining', 'luxury', 'café' are not tags we have) "
        "and never offer a search for a kind of restaurant no tag names. Tags "
        "and descriptions say nothing about allergens: never say or imply a "
        "restaurant, tag or dish is safe for an allergy (Jain, Pure Veg and "
        "vegan do not mean nut-free) — offer the menu and tell them to confirm "
        "with the restaurant.",
        "`opening_hours_display`, `meal_timings_display` and `hours_on_date` "
        "are the restaurant's published hours. They are not a table "
        "availability check — the supplier publishes none — so never say a "
        "restaurant is available or 'open for dinner on <date>' as a "
        "confirmation; say 'its published dinner hours that day are ...'.",
        "Never invent a restaurant, hour, rating, price, dish or distance. For "
        "the menu, dish prices or a follow-up about one restaurant, pass that "
        "row's `restaurant_id` to get_restaurant_details and quote what it "
        "returns. A restaurant in `coverage.restaurants` but not in `options` "
        "was filtered out by this search, not missing: use its "
        "`restaurant_id` from there, and to list them all quote "
        "`coverage.restaurants` — never a row of blanks.",
    ]
    if "max_price_inr" in filters:
        parts.append(
            "`matching_dishes` are the menu items at or under the customer's "
            "budget, cheapest first. Quote those items with their prices; they "
            "are single items, so never present them or a 'from' price as the "
            "cost of a full meal."
        )
    if place_name:
        parts.append(
            f"`distance_km` is the straight-line distance from {place_name} "
            f"(asked: {near_asked!r}). Quote it as 'about N km from "
            f"{near_asked}'. We have no route data: never turn it into a travel "
            "time, and never call a restaurant 'in' or 'near' the place when "
            "its distance says otherwise — name its `area` instead."
        )
    elif near_asked:
        parts.append(
            f"We could not place {near_asked!r} on the map. Do not say how far "
            "any restaurant is from it or that one is nearby; give each "
            "restaurant's `area` instead."
        )
    else:
        parts.append(
            "No `near` was given, so nothing here is measured from any place. "
            "If the customer's message names an area, landmark or hotel (\"in "
            "Deira\", \"near Dubai Frame\", \"staying at Atlantis\"), call "
            "search_restaurants again NOW with near=<that place> and answer "
            "from that result — never describe these rows as in or near it."
        )
    if filters and not shown:
        asked = ", ".join(f"{k} '{v}'" for k, v in filters.items())
        parts.append(
            f"No restaurant we sell matches {asked}. Say so plainly in one "
            "sentence — do not describe any restaurant of that kind from "
            "general knowledge — then offer what `coverage` does have."
        )
    if not_detailed:
        parts.append(
            "Full details could not be read for: " + ", ".join(not_detailed)
            + ". Their cuisines, tags and menu may be incomplete; do not "
            "rule them out of a filter on that basis."
        )
    return " ".join(parts)


def _impl(
    destination_city: str,
    search_date: str = "",
    adults: int = 2,
    children: int = 0,
    max_results: int = 10,
    force_refresh: bool = False,
    near: str = "",
    cuisine: str = "",
    dish: str = "",
    feature: str = "",
    restaurant_name: str = "",
    sort_by: str = "",
    max_price_inr: float | None = None,
) -> dict[str, Any]:
    """Search the restaurants we sell in a city.

    ALWAYS pass `near` when the customer's message mentions any place — an
    area ("in Deira"), a landmark ("near Dubai Frame") or a hotel ("staying at
    Atlantis"). Use the place they want to eat in or near; when they name two
    ("staying in the Marina, dinner in Deira") that is the dining one. When
    they name no place, leave `near` out and search the whole city at once —
    never ask them for an area first.

    Every filter is optional and matches the supplier's own data, so use the
    customer's words:

    Args:
        destination_city: City, e.g. "Dubai". An area of a city we cover
            ("Dubai Marina") also works and is used as `near`.
        search_date: Dining date, YYYY-MM-DD. Optional — never ask for it;
            leave it out and today is used.
        near: Area, landmark or hotel to measure from ("Deira", "Dubai
            Frame", "Atlantis The Palm"). Adds `distance_km` and sorts nearest
            first. Use it for every "in / near / around <place>" request.
        cuisine: e.g. "South Indian", "Chinese", "Gujarati".
        dish: A dish the customer wants, e.g. "dosa", "thali", "pav bhaji".
        feature: A supplier feature tag, e.g. "Jain", "Budget", "Family",
            "Premium Dining", "Unlimited Thali", "Pure Veg".
        restaurant_name: To answer "do you have <name>?" against our list.
        max_price_inr: A per-person budget in INR. Keeps restaurants with at
            least one menu item at or under it and lists those items, priced,
            in `matching_dishes`.
        sort_by: "price" (default without `near`), "rating", or "distance"
            (default with `near`).
        max_results: Rows returned (default 10). `coverage` always describes
            the whole city regardless.
    """
    # A restaurant search needs no date from the customer: the menu and the
    # catalogue do not change by day, and asking for one cost a turn before a
    # simple "where can we eat?" was answered.
    search_date = (search_date or "").strip() or supplier_today().isoformat()
    try:
        dest = resolve_destination(destination_city, near)
        if dest.city is None:
            return unsupported_destination(destination_city)
        city = dest.city
        city_name = str(city.get("name") or destination_city)

        catalog = load_city_catalog(city, search_date, adults, children)
        on = _parse_day(search_date)
        filters = {
            k: v.strip() for k, v in (
                ("restaurant_name", restaurant_name),
                ("cuisine", cuisine),
                ("dish", dish),
                ("feature", feature),
            ) if v and v.strip()
        }
        budget = float(max_price_inr) if max_price_inr else None
        if budget is not None:
            filters["max_price_inr"] = f"{budget:g}"
        matched, dish_hits = _apply_filters(
            catalog.restaurants,
            restaurant_name=filters.get("restaurant_name", ""),
            cuisine=filters.get("cuisine", ""),
            dish=filters.get("dish", ""),
            feature=filters.get("feature", ""),
            max_price_inr=budget,
        )

        place = dest.place
        if place is not None:
            from geo import haversine_km

            for r in matched:
                if r.latitude is not None and r.longitude is not None:
                    r.distance_km = round(
                        haversine_km(place.latitude, place.longitude, r.latitude, r.longitude), 1
                    )

        order = sort_by.strip().lower() if sort_by else ""
        if order not in _SORTS:
            order = "distance" if place is not None else "price"
        shown = _sort(matched, order)[: max(1, int(max_results or 10))]

        options: list[dict[str, Any]] = []
        for r in shown:
            hits = dish_hits.get(r.restaurant_id, [])
            row = restaurant_row(r, on=on, lead_dishes=hits)
            if place is not None and r.distance_km is not None:
                row["distance_from"] = dest.near_asked
            if hits:
                by_name = {d.name: d for d in r.dishes}
                row["matching_dishes"] = [
                    {
                        "name": n,
                        "price_display": by_name[n].price_display,
                        "child_price_display": by_name[n].child_price_display,
                        "image_url": (by_name[n].image_urls or [""])[0],
                    }
                    # Enough to answer from; the card and get_restaurant_details
                    # carry the rest of the menu.
                    for n in hits[:MATCHING_DISHES_SHOWN]
                ]
            options.append(row)

        distances = [r.distance_km for r in matched if r.distance_km is not None]
        return {
            "agent_instructions": _instructions(
                city_name=city_name,
                total=catalog.total_in_city,
                filters=filters,
                shown=len(options),
                near_asked=dest.near_asked,
                place_name=place.name if place else None,
                not_detailed=catalog.not_detailed,
            ),
            "options": options,
            "cheapest_price_inr": (
                min(r.price_per_adult_inr for r in shown) if shown else None
            ),
            # Rows in THIS reply. `total_matching` and `coverage` say how many
            # exist; this number alone was read as "the city has five".
            "total_results": len(options),
            "total_matching": len(matched),
            "coverage": coverage(catalog, city_name),
            "near": (
                {
                    "asked": dest.near_asked,
                    "resolved_to": place.name if place else None,
                    "nearest_km": min(distances) if distances else None,
                }
                if dest.near_asked else None
            ),
            "sorted_by": order,
            "search_params": {
                "destination": destination_city,
                "search_date": search_date,
                "adults": adults,
                "children": children,
                **filters,
                **({"near": dest.near_asked} if dest.near_asked else {}),
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
