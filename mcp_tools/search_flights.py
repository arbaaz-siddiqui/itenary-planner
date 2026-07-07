"""search_flights — agent + MCP tool.

Multi-provider async fan-out strategy:
  1. Fire one API call per airline IATA code simultaneously (asyncio).
  2. Return the FIRST batch of results to the agent immediately — no waiting
     for slow providers.
  3. Continue collecting remaining provider results in the background and
     store everything in flight_cache.
  4. On subsequent calls for the same route/date/pax, serve from cache — zero
     extra API calls.

One-way:  pass only departure_date (no return_date).
Round-trip: pass both departure_date and return_date.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from langchain_core.tools import tool

from booking_api import call_flight_search
from core import TripPlannerError
from flight_cache import get_entry, set_entry, update_entry
from mcp_tools.server import mcp
from parsers import parse_flight_response
from reference_data_loader import resolve_iata

logger = logging.getLogger(__name__)

# All airline IATA codes to fan out across (from client-provided list)
AIRLINE_PROVIDERS: list[str] = [
    "AI", "IX", "6E", "SG", "QP", "9I", "S5",   # Indian carriers
    "EK", "EY", "FZ", "QR",                        # Gulf carriers
    "TK", "SQ", "TR", "MH", "OD", "TG", "FD",    # Asian/ME carriers
    "UL", "CX", "JL", "NH", "KE", "OZ",           # Asia-Pacific
    "LH", "LX", "OS", "AF", "KL", "BA", "VS",    # European
    "AY", "SU",                                     # European cont.
    "DL", "UA", "AA", "AS", "WN", "B6",           # US carriers
    "AC", "WS",                                     # Canadian
    "QF", "VA", "NZ",                              # Oceania
]

# Workers for the thread pool — enough to fan out all providers
_MAX_WORKERS = 20

# How many providers to wait for before returning first results
# (wait for at least this many to respond, then return immediately)
_MIN_PROVIDERS_BEFORE_RETURN = 3


def _search_one_provider(
    airline_code: str,
    origin_iata: str,
    dest_iata: str,
    departure_date: str,
    return_date: str | None,
    adults: int,
    children: int,
    child_ages: list[int] | None,
    cabin: str,
    max_stops: int,
) -> tuple[str, list[Any]]:
    """Call API for one airline provider. Returns (airline_code, parsed_options)."""
    try:
        raw = call_flight_search(
            origin_iata=origin_iata,
            destination_iata=dest_iata,
            departure_date=departure_date,
            return_date=return_date,
            adults=adults,
            children=children,
            child_ages=child_ages,
            cabin=cabin,
            max_stops=max_stops,
            airline_code=airline_code,
        )
        options = parse_flight_response(
            raw,
            expected_origin=origin_iata,
            expected_destination=dest_iata,
            max_results=None,  # parse all, cache will sort
        )
        return airline_code, options
    except Exception as e:
        logger.debug("Provider %s failed: %s", airline_code, e)
        return airline_code, []


def _per_adult(o: Any, searched_pax: int) -> float:
    if o.price_per_adult_inr is not None and o.price_per_adult_inr > 0:
        return round(o.price_per_adult_inr, 2)
    pax = o.pax_count or searched_pax
    return round(o.price_inr / pax, 2) if pax else o.price_inr


def _to_dict(o: Any, searched_pax: int) -> dict[str, Any]:
    d = o.model_dump()
    d["price_total_inr"] = o.price_inr
    d["price_per_adult_inr"] = _per_adult(o, searched_pax)
    d["pax_count"] = o.pax_count or searched_pax
    return d


def _impl(
    origin_city: str,
    destination_city: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    children: int = 0,
    child_ages: list[int] | None = None,
    cabin: str = "Y",
    max_stops: int = 2,
    max_results: int = 5,
    airline_filter: str = "",
) -> dict[str, Any]:
    """Search for flights across all airline providers simultaneously.

    Args:
        origin_city:     Indian source city (e.g. "Delhi", "Mumbai").
        destination_city: Destination (typically "Dubai").
        departure_date:  ISO yyyy-mm-dd — outbound date.
        return_date:     ISO yyyy-mm-dd — return date for round-trip. Omit for one-way.
        adults:          Adult passenger count.
        children:        Child passenger count.
        child_ages:      Age of each child (length must equal children).
        cabin:           'Y' economy / 'S' premium economy / 'C' business / 'F' first.
        max_stops:       Max layovers (default 2).
        max_results:     Options to return to agent (default 5). Cache holds all.
        airline_filter:  IATA code to filter results from cache (e.g. "EK" for Emirates).
                         Only filters display — does not re-search.

    Returns:
        {options, cheapest_price_inr, cheapest_price_per_adult_inr, total_results,
         cached_total, providers_done, providers_still_loading, trip_type, search_params}
        Or {error: True, message: ...} on failure.

    TRIP TYPE NOTE:
        One-way:    pass departure_date only (no return_date)
        Round-trip: pass both departure_date AND return_date
    """
    try:
        origin_iata = resolve_iata(origin_city)
        dest_iata = resolve_iata(destination_city)
        if not origin_iata:
            return {"error": True, "message": f"Unknown origin city: {origin_city!r}", "error_type": "UnsupportedRoute"}
        if not dest_iata:
            return {"error": True, "message": f"Unknown destination city: {destination_city!r}", "error_type": "UnsupportedRoute"}

        searched_pax = max(1, adults + children)
        trip_type = "round_trip" if return_date else "one_way"

        # --- Check cache first ---
        cached = get_entry(origin_iata, dest_iata, departure_date, return_date, adults, children)
        if cached is not None:
            all_opts = cached.all_options
            if airline_filter:
                code = airline_filter.upper()
                all_opts = [o for o in all_opts if _matches_airline(o, code)]
            display = all_opts[:max_results]
            return {
                "options": display,
                "cheapest_price_inr": all_opts[0]["price_inr"] if all_opts else None,
                "cheapest_price_per_adult_inr": all_opts[0].get("price_per_adult_inr") if all_opts else None,
                "total_results": len(display),
                "cached_total": len(all_opts),
                "providers_done": cached.providers_done,
                "providers_still_loading": cached.providers_pending,
                "from_cache": True,
                "trip_type": trip_type,
                "pricing_note": "price_total_inr is full party; price_per_adult_inr is per adult.",
                "search_params": cached.search_params,
            }

        # --- Multi-provider fan-out ---
        providers = AIRLINE_PROVIDERS
        search_params = {
            "origin": origin_city,
            "origin_iata": origin_iata,
            "destination": destination_city,
            "destination_iata": dest_iata,
            "departure_date": departure_date,
            "return_date": return_date,
            "adults": adults,
            "children": children,
            "pax_count": searched_pax,
            "trip_type": trip_type,
        }

        import threading

        all_options: list[dict[str, Any]] = []
        providers_done: list[str] = []
        providers_pending: list[str] = list(providers)
        lock = threading.Lock()
        early_result: dict[str, Any] = {}

        # Pre-seed the cache entry so background thread can update it
        entry = set_entry(
            origin_iata, dest_iata, departure_date, return_date, adults, children,
            options=[],
            providers_done=[],
            providers_pending=list(providers),
            search_params=search_params,
        )

        def _run_all_providers():
            """Background fan-out — fires all providers, updates cache as each returns."""
            with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
                futures = {
                    executor.submit(
                        _search_one_provider,
                        code,
                        origin_iata, dest_iata,
                        departure_date, return_date,
                        adults, children, child_ages,
                        cabin, max_stops,
                    ): code
                    for code in providers
                }
                for future in as_completed(futures):
                    airline_code, options = future.result()
                    with lock:
                        providers_done.append(airline_code)
                        if airline_code in providers_pending:
                            providers_pending.remove(airline_code)
                        if options:
                            dicts = [_to_dict(o, searched_pax) for o in options]
                            seen = {o.get("fare_source_code") for o in all_options}
                            for d in dicts:
                                if d.get("fare_source_code") not in seen:
                                    all_options.append(d)
                                    seen.add(d.get("fare_source_code"))
                        # Update cache incrementally so callers see progressive results
                        sorted_now = sorted(all_options, key=lambda o: o.get("price_inr", float("inf")))
                        update_entry(
                            origin_iata, dest_iata, departure_date, return_date, adults, children,
                            new_options=sorted_now,
                            providers_done=list(providers_done),
                            providers_pending=list(providers_pending),
                        )
                        # Unblock the main thread once MIN_PROVIDERS responded with results
                        if (
                            not early_result
                            and len(providers_done) >= _MIN_PROVIDERS_BEFORE_RETURN
                            and all_options
                        ):
                            early_result["ready"] = True
                            ready_event.set()

            # Final update — all done
            ready_event.set()

        ready_event = threading.Event()
        bg_thread = threading.Thread(target=_run_all_providers, daemon=True)
        bg_thread.start()

        # Wait until MIN_PROVIDERS responded or all done (max 25s safety cap)
        ready_event.wait(timeout=25)

        with lock:
            snapshot_options = sorted(all_options, key=lambda o: o.get("price_inr", float("inf")))
            snapshot_done = list(providers_done)
            snapshot_pending = list(providers_pending)

        # Apply airline filter if requested
        display_pool = snapshot_options
        if airline_filter:
            code = airline_filter.upper()
            display_pool = [o for o in display_pool if _matches_airline(o, code)]

        display = display_pool[:max_results]
        cheapest = display_pool[0] if display_pool else None

        return {
            "options": display,
            "cheapest_price_inr": cheapest["price_inr"] if cheapest else None,
            "cheapest_price_per_adult_inr": cheapest.get("price_per_adult_inr") if cheapest else None,
            "total_results": len(display),
            "cached_total": len(snapshot_options),
            "providers_done": snapshot_done,
            "providers_still_loading": snapshot_pending,
            "from_cache": False,
            "trip_type": trip_type,
            "pricing_note": "price_total_inr is full party; price_per_adult_inr is per adult.",
            "search_params": search_params,
        }

    except TripPlannerError as e:
        logger.warning("search_flights error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("search_flights unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


def _matches_airline(option: dict[str, Any], iata_code: str) -> bool:
    """Check if a flight option belongs to the given airline IATA code."""
    # Top-level airline_code field (most reliable)
    if option.get("airline_code", "").upper() == iata_code:
        return True
    # Fallback: check first outbound segment
    segs = option.get("segments_outbound") or option.get("segments", [])
    if segs and isinstance(segs, list):
        first = segs[0]
        if isinstance(first, dict):
            carrier = (
                first.get("marketing_airline_code", "")
                or first.get("operating_airline_code", "")
            ).upper()
            return carrier == iata_code
    return False


search_flights_tool = tool(_impl)
search_flights_tool.name = "search_flights"
mcp.tool(name="search_flights")(_impl)
