"""get_tour_options — the bookable variants of one tour."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from mcp_tools.tour_pricing import tour_options

from .result_cache import cache_impl

mcp: Any = None


def _priced(
    tour_id: int, travel_date: str, adults: int, child_ages: list[int], tour_name: str
) -> dict[str, Any]:
    return tour_options(tour_id, travel_date, adults, child_ages, tour_name=tour_name)


# Cached BELOW the date check: the cache is shared across conversations, so a
# hit must never answer a customer who has not named a day yet.
_cached = cache_impl("get_tour_options")(_priced)


def get_tour_options(
    tour_id: int,
    travel_date: str,
    adults: int,
    child_ages: list[int] | None = None,
    tour_name: str = "",
    assume_missing: bool = False,
) -> dict[str, Any]:
    """Bookable variants of one tour, each with the party's FINAL totals.

    Every row's `prices` holds one grand total per transfer type (Sharing,
    Private, Without) for the whole party: ticket, transfer and vehicles
    included, identical to gujjutours.com. Quote those figures as they are.

    Args:
        tour_id: from search_tours.
        travel_date: ISO yyyy-mm-dd, the day the CUSTOMER named.
        adults: how many adults are travelling.
        child_ages: one age per child, e.g. [5, 8]. Children change the
            total, so pass them whenever the customer mentions any.
        tour_name: the tour's name from search_tours, shown beside each
            option name the way the website lists them.
        assume_missing: only when the CUSTOMER said to go ahead without a
            date ("any date", "just a rough price"); ignored otherwise.
    """
    from rules import customer_gave_date, customer_waived_field, needs_input_error

    waived = assume_missing and customer_waived_field()
    if not waived and (not travel_date or not customer_gave_date()):
        return needs_input_error(["travel_date"])
    result = _cached(
        tour_id=int(tour_id),
        travel_date=travel_date,
        adults=int(adults),
        child_ages=[int(a) for a in child_ages or []],
        tour_name=tour_name,
    )
    if _none_priced(result):
        alt = _priced_listing_for(int(tour_id), tour_name, travel_date, int(adults),
                                  [int(a) for a in child_ages or []])
        if alt:
            return alt
    return result


def _none_priced(result: Any) -> bool:
    opts = (result or {}).get("options") if isinstance(result, dict) else None
    if not opts:
        return False
    return not any(o.get("prices") or (o.get("price_per_adult_inr") or 0) > 0 for o in opts)


def _priced_listing_for(tour_id: int, tour_name: str, travel_date: str, adults: int,
                        child_ages: list[int]) -> dict[str, Any] | None:
    """The website's priced listing of the same attraction.

    Autocomplete (lookup_entity) also returns listings the website does not
    sell: Global Village came back as Klook tour 46045, whose options all
    answer the rate call with HTTP 500, so the customer read "On request"
    while gujjutours.com sells "Global Village Dubai Tickets by Gujjutours"
    at Rs 590.13. The tour search is the website's own list, so the same
    name searched there gives the listings that can actually be booked.
    """
    from booking_api.endpoints import call_tour_details
    from mcp_tools.search_tours import _impl as search_tours

    name, city = tour_name, ""
    try:
        details = (call_tour_details(tour_id=tour_id) or {}).get("result") or {}
        name = name or str(details.get("tourName") or "")
        city = str(details.get("cityName") or "")
    except Exception:  # noqa: BLE001 -- without a city there is nothing to search
        return None
    if not name or not city:
        return None
    found = search_tours(destination_city=city, travel_date=travel_date, adults=adults,
                         query=name, child_ages=child_ages or None)
    priced = [o for o in (found or {}).get("options") or []
              if int(o.get("tour_id") or 0) != tour_id and (o.get("price_per_adult_inr") or 0) > 0]
    if not priced:
        return None
    return {
        **found,
        "options": priced,
        "replaced_tour_id": tour_id,
        "agent_instructions": (
            f"'{name}' (tour {tour_id}) has no bookable price. These are the priced "
            "listings for the same attraction, as gujjutours.com sells them: show "
            "these and quote their prices; do not call the first one 'on request'."
        ),
    }


get_tour_options_tool = tool(get_tour_options)
get_tour_options_tool.name = "get_tour_options"
