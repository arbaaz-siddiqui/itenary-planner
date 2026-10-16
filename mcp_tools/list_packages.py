"""list_packages — agent + MCP tool.

Static packages from Technoheaven. The new API takes a date window + room
configuration. Two calls: list (metadata) + rate (for a single package).
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_list_packages
from core import TripPlannerError, nights_between
from rules import missing_search_fields, needs_input_error
from mcp_tools.server import mcp
from parsers import parse_package_response
from reference_data_loader import resolve_city

logger = logging.getLogger(__name__)

# How many packages a list prices live (each is one page read plus one hotel
# and ~5 tour rate calls, run in parallel).
PRICE_TOP = 8


def _safe_nights(option: dict[str, Any]) -> int:
    try:
        return int(option.get("nights") or 0)
    except (TypeError, ValueError):
        return 0


def _price_options(options: list[dict[str, Any]], *, city: dict[str, Any], check_in: str,
                   adults: int, child_ages: list[int], rooms: int, nationality: str) -> None:
    """Attach the live price and parts to each option, in place."""
    import concurrent.futures as cf

    from mcp_tools.package_pricing import price_package

    def one(o: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        own = resolve_city(str(o.get("city") or "")) or city
        try:
            return o, price_package(
                package_id=int(o["package_id"]), name=str(o.get("name") or ""),
                country=str(o.get("country") or own.get("country_name") or ""),
                country_id=int(own["country_id"]), city=str(own["name"]),
                city_id=int(own["city_id"]), check_in=check_in, nights=_safe_nights(o),
                adults=adults, child_ages=child_ages, rooms=rooms, nationality=nationality,
            )
        except Exception as e:  # noqa: BLE001 -- an unpriced row stays in the list
            logger.warning("package price failed id=%s: %s", o.get("package_id"), e)
            return o, None

    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for o, priced in pool.map(one, options):
            if not priced:
                continue
            o.update({
                "price_inr": priced["price_inr"],
                "price_display": priced["price_display"],
                "price_per_person_inr": priced["price_per_person_inr"],
                "price_per_person_display": priced["price_per_person_display"],
                "pricing_available": priced["pricing_available"],
                "pricing_note": priced["pricing_note"],
                "hotel_name": (priced["hotel"] or {}).get("name"),
                "hotel": priced["hotel"],
                "package_lines": priced["lines"],
                "check_in": priced["check_in"],
                "check_out": priced["check_out"],
            })


def _impl(
    destination_city: str = "Dubai",
    check_in: str = "",
    check_out: str = "",
    adults: int = 2,
    children: int = 0,
    nationality: str = "India",
    fetch_rates: bool = True,
    max_results: int = 25,
    child_ages: list[int] | None = None,
    rooms: int = 1,
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
        child_ages: one age per child, e.g. [5, 8] (prices the hotel rooms).
        rooms: how many hotel rooms the party needs.
    """
    try:
        city = resolve_city(destination_city)
        if city is None:
            return {
                "error": True,
                "message": (
                    f"We do not sell packages in {destination_city!r} — the "
                    f"supplier's inventory for this service is Dubai only."
                ),
                "error_type": "UnsupportedRoute",
                # A bare "Unsupported destination" got paraphrased to the
                # customer as "I don't have that in my database", which reads
                # as OUR system being broken rather than us not selling it.
                "agent_instructions": (
                    f"Say plainly that we do not offer packages in that city — we "
                    f"cover Dubai for this service. Never say the data is "
                    f"missing, unavailable, or not in your database, and never "
                    f"imply a technical problem. Offer the Dubai equivalent "
                    f"instead, and do not ask the customer for more details "
                    f"first — the city is the blocker, not their input."
                ),
            }

        # The supplier REQUIRES both dates: sending checkInDate:"" returns
        # HTTP 400 `{"$.checkInDate": ["Can't get datetime from the reader!"]}`
        # and the whole package list is lost. We were treating them as optional
        # and defaulting nights to 0, so every dateless ask returned nothing.
        missing = missing_search_fields(check_in=check_in, check_out=check_out)
        if missing:
            return needs_input_error(missing)

        nights = nights_between(check_in, check_out)

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

        # The supplier's packagerate answers empty lists for every package, so
        # it is not called; prices come from the package's own parts, priced
        # the way the website prices them (mcp_tools.package_pricing).
        options = parse_package_response(list_raw, {"result": []}, max_results=max_results)
        for _o in options:
            for _k in ("price_inr", "price_original", "currency_original",
                       "pricing_available", "price_display", "booking_status"):
                _o.pop(_k, None)

        # Packages with the nights asked for come first, and those are priced.
        options.sort(key=lambda o: _safe_nights(o) != nights)
        if fetch_rates:
            _price_options(options[:PRICE_TOP], city=city, check_in=check_in,
                           adults=adults, child_ages=list(child_ages or []),
                           rooms=rooms, nationality=nationality)

        return {
            "options": options,
            "total_results": len(options),
            "agent_instructions": (
                "Packages carry a live price when `pricing_available` is true: "
                "`price_display` is the TOTAL for the party and stay, "
                "`price_per_person_display` the per-person figure, priced the "
                "way gujjutours.com prices the package (hotel for the stay plus "
                "each tour and transfer in it; `package_lines` lists them). "
                "Quote those figures as they are; never add, scale or estimate "
                "them. A package without a price was not priced in this list: "
                "call get_package_details with the same dates and party for its "
                "price and parts. Show a table: Package | Nights | Hotel | "
                "Includes | Total for the party. Use `package_type`, `tags`, "
                "`includes` and `is_free_cancellation` as facts."
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
