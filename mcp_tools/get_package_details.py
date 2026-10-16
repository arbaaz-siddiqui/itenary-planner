"""get_package_details — agent + MCP tool.

A package's itinerary and images (packagestaticdata) and, when dates and a
party are given, its live price with every part: the hotel and each tour and
transfer, priced the way gujjutours.com prices them (mcp_tools.package_pricing).
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_package_static_data
from core import TripPlannerError
from mcp_tools.server import mcp

logger = logging.getLogger(__name__)


def _days(raw: dict[str, Any]) -> list[dict[str, Any]]:
    from parsers import _strip_html

    res = (raw or {}).get("result") or {}
    out = []
    for d in res.get("staticItineraryInfo") or []:
        if isinstance(d, dict):
            out.append({"day": d.get("itineraryDay"), "title": d.get("itineraryName"),
                        "description": _strip_html(d.get("description"))})
    return out


def _impl(
    package_id: int,
    check_in: str = "",
    adults: int = 0,
    child_ages: list[int] | None = None,
    rooms: int = 1,
    nationality: str = "India",
) -> dict[str, Any]:
    """A package's day-by-day plan, and its live price and parts.

    Args:
        package_id: from list_packages.
        check_in: ISO yyyy-mm-dd, the customer's arrival day. With `adults`
            it returns the live total, the hotel and every tour and transfer.
        adults: adults travelling.
        child_ages: one age per child.
        rooms: hotel rooms the party needs.
        nationality: country name, e.g. 'India'.
    """
    try:
        raw = call_package_static_data(package_id=package_id)
        res = (raw or {}).get("result") or {}
        info = (res.get("packageInfo") or [{}])[0] or {}
        name = str(info.get("packageName") or "")
        out: dict[str, Any] = {
            "package_id": int(package_id),
            "name": name,
            "nights": info.get("noOfNights"),
            "days": _days(raw),
            "data": raw,
        }
        if check_in and adults:
            from mcp_tools.package_pricing import price_package
            from reference_data_loader import resolve_city

            city = resolve_city(str(info.get("startCity") or "")) or {}
            priced = price_package(
                package_id=int(package_id), name=name,
                country=str(info.get("startCountry") or city.get("country_name") or ""),
                country_id=int(info.get("startCountryId") or city.get("country_id") or 0),
                city=str(info.get("startCity") or city.get("name") or ""),
                city_id=int(info.get("startCityId") or city.get("city_id") or 0),
                check_in=check_in, nights=int(info.get("noOfNights") or 0),
                adults=int(adults), child_ages=list(child_ages or []), rooms=rooms,
                nationality=nationality,
            ) if city or info.get("startCityId") else None
            if priced:
                out.update(priced)
                out["agent_instructions"] = (
                    "`price_display` is the package TOTAL for this party and stay and "
                    "`price_per_person_display` the per-person figure, priced as "
                    "gujjutours.com prices it. Quote them as they are. List the parts "
                    "from `lines` (hotel, then each day's tours and transfers) and say "
                    "which parts `unpriced_parts` names, if any."
                )
            else:
                out["pricing_available"] = False
                out["pricing_note"] = "This package's parts could not be read, so it has no live price."
        return out
    except TripPlannerError as e:
        logger.warning("get_package_details error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("get_package_details unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


from mcp_tools.result_cache import cache_impl
get_package_details_tool = tool(cache_impl("get_package_details")(_impl))
get_package_details_tool.name = "get_package_details"
mcp.tool(name="get_package_details")(_impl)
