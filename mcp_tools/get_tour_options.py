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
    return _cached(
        tour_id=int(tour_id),
        travel_date=travel_date,
        adults=int(adults),
        child_ages=[int(a) for a in child_ages or []],
        tour_name=tour_name,
    )


get_tour_options_tool = tool(get_tour_options)
get_tour_options_tool.name = "get_tour_options"
