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

# FAST-FIRST strategy (measured per-code latency, HYD↔DXB India-Gulf routes):
#   - The "" all-airlines pass is COMPLETE (~135 flights) but SLOW (~15s).
#   - A handful of carrier codes are fast AND carry the real inventory Indian
#     travellers want: AI (~2.5s, 27 flights), 6E, EK, FZ, QR, UL, SQ.
#   - The other ~35 codes return 0 for these routes and some are slow → skip.
# So: fire the FAST high-yield codes first → return as soon as ~2 respond (first
# paint in ~2-3s), THEN run the "" all-airlines pass in the background to backfill
# the COMPLETE set into the cache for "show more"/filters.
FAST_PROVIDERS: list[str] = ["AI", "6E", "EK", "FZ", "QR", "UL", "SQ"]
BACKFILL_PROVIDERS: list[str] = [""]  # complete all-airlines pass (slow, background)

_MAX_WORKERS = 7  # fire all fast codes at once

# Return to the user as soon as this many fast providers respond WITH results.
_MIN_PROVIDERS_BEFORE_RETURN = 2


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


def _forward_date(date_str: str) -> str:
    """If an ISO date is in the past, roll its YEAR forward to the next future
    occurrence. Guards against models passing a stale year (e.g. 2023) for a bare
    date like '3 aug', which would make the flight API return zero results.
    Non-ISO / unparseable input is returned unchanged."""
    from datetime import date, datetime as _dt
    try:
        d = _dt.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return date_str
    today = date.today()
    if d >= today:
        return date_str
    # Past date — advance the year until it's today or later.
    y = today.year
    while True:
        try:
            candidate = d.replace(year=y)
        except ValueError:  # e.g. Feb 29 on a non-leap year
            y += 1
            continue
        if candidate >= today:
            return candidate.isoformat()
        y += 1


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
        max_results:     Options to return (default 5). When the customer asks to
                         "see more" / "show 10 options", CALL THIS TOOL AGAIN with a
                         higher max_results (e.g. 10 or 15) — the cache already holds
                         hundreds of options across all airlines, so a second call is
                         instant and returns MORE variety (different airlines/prices),
                         NOT the same 5. Never tell the customer "that's all" without
                         re-calling with a higher max_results first.
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
        # Defense-in-depth: small models sometimes pass a PAST year (e.g. llama
        # defaulting "3 aug" to 2023) → the supplier returns 0 flights. If a date
        # is in the past, bump its year forward to today/next occurrence so the
        # search actually returns results instead of a confusing "no flights".
        departure_date = _forward_date(departure_date)
        if return_date:
            return_date = _forward_date(return_date)

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
            display = _diversify(all_opts, max_results)
            return {
                "options": display,
                "cheapest_price_inr": display[0]["price_inr"] if display else None,
                "cheapest_price_per_adult_inr": display[0].get("price_per_adult_inr") if display else None,
                "total_results": len(display),
                "cached_total": len(all_opts),
                "providers_done": cached.providers_done,
                "providers_still_loading": cached.providers_pending,
                "from_cache": True,
                "trip_type": trip_type,
                "pricing_note": "price_total_inr is full party; price_per_adult_inr is per adult.",
                # STOP signal — these results are ready. The model must present
                # them now and NOT call search_flights again this turn.
                "agent_instructions": (
                    "These flight results are READY. Present them to the customer "
                    "now in your reply. DO NOT call search_flights again — you "
                    "already have the results."
                ),
                "search_params": cached.search_params,
            }

        # --- FAST-FIRST + background backfill ---
        # Fire the fast high-yield airline codes concurrently, return as soon as
        # MIN_PROVIDERS respond (first paint ~2-3s), then run the complete ""
        # all-airlines pass in the background to fill the cache.
        import threading

        search_params = {
            "origin": origin_city, "origin_iata": origin_iata,
            "destination": destination_city, "destination_iata": dest_iata,
            "departure_date": departure_date, "return_date": return_date,
            "adults": adults, "children": children,
            "pax_count": searched_pax, "trip_type": trip_type,
        }

        all_options: list[dict[str, Any]] = []
        seen_fares: set = set()
        done: list[str] = []
        lock = threading.Lock()
        ready = threading.Event()

        set_entry(
            origin_iata, dest_iata, departure_date, return_date, adults, children,
            options=[], providers_done=[], providers_pending=list(FAST_PROVIDERS),
            search_params=search_params,
        )

        def _merge(code: str, opts: list[Any]) -> None:
            with lock:
                done.append(code)
                for o in opts:
                    d = _to_dict(o, searched_pax)
                    fsc = d.get("fare_source_code")
                    if fsc not in seen_fares:
                        seen_fares.add(fsc)
                        all_options.append(d)
                all_options.sort(key=lambda o: o.get("price_inr", float("inf")))
                update_entry(
                    origin_iata, dest_iata, departure_date, return_date, adults, children,
                    new_options=list(all_options), providers_done=list(done), providers_pending=[],
                )
                if not ready.is_set() and all_options and len(done) >= _MIN_PROVIDERS_BEFORE_RETURN:
                    ready.set()

        def _worker(codes: list[str]) -> None:
            try:
                with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
                    futs = {ex.submit(_search_one_provider, c, origin_iata, dest_iata,
                            departure_date, return_date, adults, children, child_ages,
                            cabin, max_stops): c for c in codes}
                    for f in as_completed(futs):
                        code, opts = f.result()
                        _merge(code, opts)
                # Background backfill: the complete "" all-airlines pass.
                for c in BACKFILL_PROVIDERS:
                    _, opts = _search_one_provider(c, origin_iata, dest_iata,
                        departure_date, return_date, adults, children, child_ages, cabin, max_stops)
                    _merge(c, opts)
            except Exception as e:  # noqa: BLE001
                logger.warning("flight fan-out error (non-fatal): %s", e)
            finally:
                ready.set()

        threading.Thread(target=_worker, args=(list(FAST_PROVIDERS),), daemon=True).start()
        ready.wait(timeout=25)  # first paint once MIN_PROVIDERS respond

        with lock:
            snapshot = list(all_options)

        display_pool = snapshot
        if airline_filter:
            code = airline_filter.upper()
            display_pool = [o for o in display_pool if _matches_airline(o, code)]

        # Diversify: collapse identical (airline, price) fares + cap per airline so
        # the customer sees variety (IndiGo, Emirates, Air India…), not one airline.
        display = _diversify(display_pool, max_results)
        cheapest = display[0] if display else None

        return {
            "options": display,
            "cheapest_price_inr": cheapest["price_inr"] if cheapest else None,
            "cheapest_price_per_adult_inr": cheapest.get("price_per_adult_inr") if cheapest else None,
            "total_results": len(display),
            "cached_total": len(snapshot),
            "providers_done": list(done),
            "providers_still_loading": [],
            "from_cache": False,
            "trip_type": trip_type,
            "pricing_note": "price_total_inr is full party; price_per_adult_inr is per adult.",
            # STOP signal — same as the cached path. Without this the model often
            # re-calls search_flights, and that extra LLM round truncates the
            # streamed reply mid-sentence (seen as "Quick question: How" cut off).
            "agent_instructions": (
                "These flight results are READY. Present them to the customer "
                "now in your reply. DO NOT call search_flights again — you "
                "already have the results."
            ),
            "search_params": search_params,
        }

    except TripPlannerError as e:
        logger.warning("search_flights error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("search_flights unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


def _diversify(options: list[dict[str, Any]], limit: int, per_airline: int | None = None) -> list[dict[str, Any]]:
    """Pick up to `limit` options showing airline VARIETY (price-sorted input).

    Two passes:
      1. Drop exact (airline, rounded price) duplicates — the supplier returns
         many identical-fare rows that differ only by flight number.
      2. Cap each airline to `per_airline` in the shown set so one cheap airline
         can't monopolise all slots; backfill remaining slots if we run short.

    per_airline scales with the request: a small list (5) caps at 2/airline for
    variety; a "show me 10" list allows more per airline so we can actually fill
    the slots instead of running short.
    """
    if per_airline is None:
        per_airline = 2 if limit <= 6 else max(3, limit // 3)
    def key(o: dict[str, Any]) -> tuple:
        return (o.get("airline", ""), round(float(o.get("price_inr") or 0)))

    seen_fare: set[tuple] = set()
    deduped: list[dict[str, Any]] = []
    for o in options:  # already price-sorted
        k = key(o)
        if k in seen_fare:
            continue
        seen_fare.add(k)
        deduped.append(o)

    picked: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    overflow: list[dict[str, Any]] = []
    for o in deduped:
        a = o.get("airline", "")
        if counts.get(a, 0) < per_airline:
            picked.append(o)
            counts[a] = counts.get(a, 0) + 1
        else:
            overflow.append(o)
        if len(picked) >= limit:
            break
    # If diversity capping left us short of `limit`, backfill from overflow.
    if len(picked) < limit:
        picked.extend(overflow[: limit - len(picked)])
    return picked[:limit]


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
