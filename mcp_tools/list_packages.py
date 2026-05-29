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
    max_results: int = 5,
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
            city_id=int(city.get("city_id") or 0),
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
        list_items = list_raw.get("result") or list_raw.get("packages") or []
        if not isinstance(list_items, list):
            list_items = []
        package_ids: list[int] = []
        for p in list_items:
            if isinstance(p, dict):
                pid = p.get("packageId") or p.get("packageID")
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

        return {
            "options": options,
            "cheapest_price_inr": (options[0].get("price_inr") if options else None),
            "total_results": len(options),
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


list_packages_tool = tool(_impl)
list_packages_tool.name = "list_packages"
mcp.tool(name="list_packages")(_impl)
