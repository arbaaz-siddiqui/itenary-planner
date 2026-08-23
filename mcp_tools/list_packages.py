"""list_packages — agent + MCP tool.

Static packages from Technoheaven. The new API takes a date window + room
configuration. Two calls: list (metadata) + rate (for a single package).
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_list_packages, call_package_rates
from core import TripPlannerError, nights_between
from mcp_tools.server import mcp
from parsers import parse_package_response
from reference_data_loader import resolve_city

logger = logging.getLogger(__name__)


def _impl(
    destination_city: str = "Dubai",
    check_in: str = "",
    check_out: str = "",
    adults: int = 2,
    children: int = 0,
    nationality: str = "India",
    fetch_rates: bool = True,
    max_results: int = 25,
) -> dict[str, Any]:
    """List packages for the destination.

    Args:
        destination_city: e.g. 'Dubai'.
        check_in: ISO yyyy-mm-dd.
        check_out: ISO yyyy-mm-dd.
        adults: Adults in one room.
        children: Children in one room.
        nationality: Country name (e.g. 'India').
        fetch_rates: If True, also fetch rates for each listed package.
            Set False if you only need package metadata (faster).
        max_results: Cap on options returned.
    """
    try:
        city = resolve_city(destination_city)
        if city is None:
            return {
                "error": True,
                "message": f"Unsupported destination: {destination_city!r}",
                "error_type": "UnsupportedRoute",
            }

        nights = nights_between(check_in, check_out) if (check_in and check_out) else 0

        list_raw = call_list_packages(
            country_id=int(city["country_id"]),
            city_id=0,  # API requires cityID=0 (country-level, not city-specific)
            check_in=check_in,
            check_out=check_out,
            nights=nights,
            adults=adults,
            children=children,
            nationality=nationality,
            residency=nationality,
        )

        # Pull package IDs out of the list response.
        # Parser handles the actual response shape; here we just extract IDs.
        # The supplier nests the real list at result.packages; `result` itself is
        # a dict (it also carries categories, hotelPackages, tourPackages...).
        # Reading `result` as a list silently yielded zero packages.
        _res = list_raw.get("result")
        list_items: list[Any] = []
        for candidate in (
            _res.get("packages") if isinstance(_res, dict) else None,
            _res if isinstance(_res, list) else None,
            list_raw.get("packages"),
        ):
            if isinstance(candidate, list) and candidate:
                list_items = candidate
                break
        package_ids: list[int] = []
        for p in list_items:
            if isinstance(p, dict):
                pid = (
                    p.get("packageId")
                    or p.get("packageID")
                    or p.get("selectedPackageId")
                )
                if pid is not None:
                    try:
                        package_ids.append(int(pid))
                    except (ValueError, TypeError):
                        continue

        # Rates: new API takes ONE packageId per call. Loop with a cap.
        rate_results: list[dict[str, Any]] = []
        if fetch_rates:
            for pid in package_ids[:max_results]:
                try:
                    rate_raw = call_package_rates(
                        package_id=pid,
                        country_id=int(city["country_id"]),
                        city_id=int(city.get("city_id") or 0),
                        check_in=check_in,
                        check_out=check_out,
                        nights=nights,
                        adults=adults,
                        children=children,
                        nationality=nationality,
                        residency=nationality,
                    )
                    rate_results.append({"packageId": pid, **rate_raw})
                except TripPlannerError as e:
                    logger.warning("package rate failed for %s: %s", pid, e)
                    continue

        # The parser will join list + rates.
        options = parse_package_response(
            list_raw,
            {"result": rate_results},
            max_results=max_results,
        )

        # Packages are quoted on request: the supplier's own list carries
        # bookingStatus=OnRequest with buyingTotalPrice=0, and /packagerate
        # returns 200 with every list empty. Say so, so the agent does not
        # present an empty price column (or invent one).
        _priced = [o for o in options if o.get("price_inr")]
        _on_request = len(options) - len(_priced)
        return {
            "options": options,
            "cheapest_price_inr": (_priced[0].get("price_inr") if _priced else None),
            "total_results": len(options),
            "pricing_status": (
                "on_request" if _on_request and not _priced else
                "partial" if _on_request else "priced"
            ),
            "agent_instructions": (
                (
                    "These packages are quoted ON REQUEST — the supplier returns "
                    "no rate for them, so there is NO price to show. List them by "
                    "name, nights and category (Dynamic Package / Land Package) "
                    "and say pricing is confirmed on request. Never invent or "
                    "estimate a package price, and never show a blank price "
                    "column. If the customer wants firm numbers now, build the "
                    "trip from search_flights + search_hotels + search_tours "
                    "instead, which ARE priced. "
                    if _on_request and not _priced else
                    "Show each package with its name, nights, category and price. "
                )
                + "`category` is the supplier's own label — a package marked "
                "'Dynamic Package' is dynamic; do not describe the list as "
                "static-only. "
            ),
            "search_params": {
                "destination": destination_city,
                "check_in": check_in,
                "check_out": check_out,
                "adults": adults,
                "children": children,
                "nationality": nationality,
            },
        }
    except TripPlannerError as e:
        logger.warning("list_packages error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("list_packages unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


from mcp_tools.result_cache import cache_impl
list_packages_tool = tool(cache_impl("list_packages")(_impl))
list_packages_tool.name = "list_packages"
mcp.tool(name="list_packages")(_impl)
