"""get_tour_options — the bookable variants of one tour."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from mcp_tools.tour_pricing import tour_options

from .result_cache import cache_impl

mcp: Any = None


def get_tour_options(tour_id: int, travel_date: str, adults: int) -> dict[str, Any]:
    """Bookable variants of one tour, with ticket and transfer prices.

    Args:
        tour_id: from search_tours.
        travel_date: ISO yyyy-mm-dd.
        adults: how many adults are travelling.
    """
    return tour_options(int(tour_id), travel_date, int(adults))


get_tour_options_tool = tool(cache_impl("get_tour_options")(get_tour_options))
get_tour_options_tool.name = "get_tour_options"
