"""search_tours — agent + MCP tool."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_tour_rates, call_tour_search
from core import TripPlannerError
from mcp_tools.server import mcp
from parsers import parse_tour_response
from reference_data_loader import resolve_city

logger = logging.getLogger(__name__)

# Timeslot fan-out budget. One supplier call is ~5s, so we go wide (one worker
# per tour on the page) and cap total wait: a page of 10 costs ~5-7s, not 50s.
_SLOT_MAX_WORKERS = 24
_SLOT_FETCH_DEADLINE_S = 8.0


def _recommended_first(options: list) -> list:
    """Supplier-recommended tours first, then cheapest.

    `is_recommended` was parsed from the supplier's `isRecommanded` flag but
    never used, so results came back in raw supplier order. Ranking cannot use
    rating: `tourrating` and `reviewsCount` are 0 for ALL 332 Dubai tours.
    Sort is stable, so equal-priced tours keep supplier order.
    """
    return sorted(options, key=lambda o: (not o.is_recommended, o.price_per_adult_inr))


def _attach_timeslots(options: list, travel_date: str, adults: int) -> None:
    """Fetch real start times for each tour on the page, in parallel.

    Client requirement: show timeslots for every tour that has them. Making the
    agent call get_tour_timeslots per tour meant it usually did not bother, so
    the times never reached the customer. Fetching here makes slots the default.

    Deliberately concurrent and best-effort: a page of 10 costs one round of
    parallel calls, and any tour whose lookup fails simply has no slots rather
    than failing the whole search.
    """
    import concurrent.futures as _cf

    from booking_api import call_tour_timeslots
    from scheduling import usable_slots

    def _one(o: Any) -> None:
        if not (o.supplier_id and o.option_id):
            return
        try:
            raw = call_tour_timeslots(
                tour_id=o.tour_id,
                tour_option_id=str(o.option_id),
                travel_date=travel_date,
                supplier_id=o.supplier_id,
                adults=adults,
            )
            rows = raw.get("result") or []
            slots = usable_slots(rows if isinstance(rows, list) else [])
            # usable_slots strips the "00:00" placeholder 25% of tours return,
            # so is_slot_based is only True for REAL published times.
            o.is_slot_based = bool(slots)
            o.timeslots = [
                {
                    "time": f"{t.hour:02d}:{t.minute:02d}",
                    "available": row.get("available"),
                    "slot_id": str(row.get("timeSlotId") or ""),
                }
                for t, row in slots
            ]
        except Exception as e:  # noqa: BLE001 — a missing slot list is not fatal
            logger.debug("timeslot lookup failed for tour %s: %s", o.tour_id, e)

    if not options:
        return
    # Each supplier timeslot call costs ~5s, so this MUST be wide and bounded.
    # Wide: one worker per tour, so wall-clock is ~one call, not N.
    # Bounded: a hard deadline, because a slow supplier must never stall the
    # whole tour search — voice turns are already the top latency complaint.
    # Tours whose lookup does not land in time simply show as "flexible".
    workers = min(_SLOT_MAX_WORKERS, len(options))
    ex = _cf.ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [ex.submit(_one, o) for o in options]
        _cf.wait(futures, timeout=_SLOT_FETCH_DEADLINE_S)
    finally:
        # Do not block on stragglers; daemon threads die with the process.
        ex.shutdown(wait=False, cancel_futures=True)


def _impl(
    destination_city: str,
    travel_date: str,
    tour_category_id: int = 1,
    max_results: int = 10,
    query: str = "",
    force_refresh: bool = False,
    offset: int = 0,
    adults: int = 1,
    transfer_type: str = "",
) -> dict[str, Any]:
    """Search tours/activities. Calls both /toursearchlist and /toursearchlistrate.

    Recommended tours come first, then cheapest. Dubai has ~270 priced tours, so
    the reply is a page, not the whole catalogue — `total_available` tells the
    customer how many exist ("270 tours available, here are the first 10").

    Args:
        max_results: page size. Default 10.
        offset: how many to SKIP. This is what makes "show me more" work — the
            next page must be NEW tours, not the same ones again. Escalate the
            page size as they keep asking:
                1st ask  offset=0   max_results=10
                2nd ask  offset=10  max_results=20
                3rd ask  offset=30  max_results=40
                4th ask  offset=70  max_results=80   (doubling)
            `next_offset` in the response is the value to pass next time, so you
            never have to compute it.
        query: when the customer names a SPECIFIC tour ("desert safari", "dhow
            cruise", "burj khalifa"), pass it here. Results are filtered to names
            matching that keyword BEFORE the page cut — otherwise a specific tour
            can be missed because the first page shows only the cheapest.
        force_refresh: set True to SKIP the cache and re-fetch live from the
            supplier — use when the customer says "check again" / "is it still
            available" or just before booking. Normal repeats leave this False.
        transfer_type: filter by transfer basis. "with_transfer" returns only
            tours that include a shared/private transfer (86 of 332 in Dubai);
            "ticket_only" returns entry-ticket tours (246). Use this when the
            customer asks for "shared and private tours" — WITHOUT it they only
            see whatever happens to be on page one, which made the agent report
            "only 3 tours have transfers" when there are 86.
    """
    _ = force_refresh  # consumed by the cache layer; ignored here
    try:
        city = resolve_city(destination_city)
        if city is None or not city.get("city_id"):
            return {
                "error": True,
                "message": f"Unsupported destination: {destination_city!r}",
                "error_type": "UnsupportedRoute",
            }
        list_raw = call_tour_search(
            country_id=int(city["country_id"]),
            city_id=int(city["city_id"]),
            travel_date=travel_date,
            tour_category_id=tour_category_id,
        )
        rate_raw = call_tour_rates(
            country_id=int(city["country_id"]),
            city_id=int(city["city_id"]),
            travel_date=travel_date,
            tour_category_id=tour_category_id,
        )
        # Parse the FULL list first (no cap) so a keyword filter can reach tours
        # beyond the 5 cheapest. Only cap after filtering.
        all_options = parse_tour_response(list_raw, rate_raw, max_results=None)
        q = query.strip().lower()
        if q:
            terms = [w for w in q.split() if len(w) > 2]
            matched = [
                o for o in all_options
                if all(term in o.name.lower() for term in terms)
            ] or [
                # looser fallback: ANY term matches (e.g. "safari" hits "Desert Safari")
                o for o in all_options
                if any(term in o.name.lower() for term in terms)
            ]
            pool = matched
        else:
            pool = all_options

        # Filter on transfer basis BEFORE paging, so "show me the shared and
        # private tours" reaches all 86 rather than the handful on page one.
        tf = (transfer_type or "").strip().lower()

        def _has_transfer(o: Any) -> bool:
            sc = (o.transfer_scenario or "").strip()
            return bool(sc) and "without" not in sc.lower()

        def _is_private_product(o: Any) -> bool:
            """A tour that IS private, as opposed to one with a private transfer.

            "Private Luxury Yacht Experience" and "Dubai Half-Day Private Old
            Town Walking Tour" are private EXPERIENCES, yet their
            transferScenario is "Without Transfer" (no pickup included). Filtering
            on transferScenario alone dropped all 12 of them, so the agent told a
            customer asking for private tours that only shared-transfer ones
            existed.
            """
            return "private" in (o.name or "").lower()

        if tf in {"private", "private_only"}:
            # Private EXPERIENCES first ("Private Luxury Yacht"), then tours that
            # merely include a private transfer. Someone asking for private tours
            # wants the former; pure cheapest-first buried all 12 of them.
            pool = [o for o in pool if _is_private_product(o) or "private" in (o.transfer_scenario or "").lower()]
            pool = sorted(pool, key=lambda o: (not _is_private_product(o), o.price_per_adult_inr))
        elif tf in {"shared", "shared_only", "sharing"}:
            pool = [o for o in pool if "shar" in (o.transfer_scenario or "").lower()
                    or "all" in (o.transfer_scenario or "").lower()]
        elif tf in {"with_transfer", "transfer", "shared_private"}:
            # Everything relevant to "shared or private": tours with a transfer
            # PLUS private-experience products. 96 in Dubai, not 85.
            pool = [o for o in pool if _has_transfer(o) or _is_private_product(o)]
        elif tf in {"ticket_only", "ticket", "without_transfer", "no_transfer"}:
            pool = [o for o in pool if not _has_transfer(o) and not _is_private_product(o)]

        # A private-first ordering is deliberate and must not be re-sorted away.
        ranked = pool if tf in {"private", "private_only"} else _recommended_first(pool)

        # Page the ranked list. Slicing AFTER ranking is what makes "show me
        # more" return genuinely new tours instead of repeating page one.
        start = max(0, int(offset or 0))
        options = ranked[start : start + max_results]
        shown_end = start + len(options)
        remaining = max(0, len(ranked) - shown_end)

        # Real start times for this page (parallel, best-effort).
        _attach_timeslots(options, travel_date, max(1, int(adults or 1)))

        # Presentation-ready fields so the agent RELAYS these rather than
        # deriving (or omitting) them. The client requires sharing/private and
        # cancellation on EVERY tour — computing them here makes that the
        # default rather than something the model has to remember.
        option_dicts = []
        for o in options:
            d = o.model_dump()
            d["sharing_display"] = o.sharing_display
            d["cancellation_display"] = o.cancellation_display
            d["price_display"] = o.price_display
            d["timeslots"] = getattr(o, "timeslots", [])
            d["is_slot_based"] = bool(getattr(o, "timeslots", []))
            d["slots_display"] = (
                f"{len(o.timeslots)} start times: "
                + ", ".join(x["time"] for x in o.timeslots[:6])
                + (" …" if len(o.timeslots) > 6 else "")
                if getattr(o, "timeslots", []) else "Flexible — no fixed start time"
            )
            option_dicts.append(d)

        return {
            "options": option_dicts,
            "cheapest_price_inr": (options[0].price_per_adult_inr if options else None),
            # `total_results` is this PAGE (kept for back-compat). The two below
            # are what the customer should hear.
            "total_results": len(options),
            "total_available": len(ranked),
            "remaining": remaining,
            "offset": start,
            "showing": f"{start + 1}-{shown_end}" if options else "0",
            # Pass this back as `offset` next time, with double the page size.
            "next_offset": shown_end if remaining else None,
            "next_max_results": max_results * 2 if remaining else None,
            "recommended_count": sum(1 for o in options if o.is_recommended),
            # `filtered` matters: with transfer_type set, `total_available` is the
            # size of the FILTERED set, not the catalogue. The agent announced
            # "85 tours available in Dubai" after a with_transfer search, which
            # reads as though that is all we sell (it is 270).
            "filtered_by": tf or None,
            # Names on this page, so the model can SEE that a tour the customer
            # named is absent and re-search instead of claiming we lack the data.
            "names_on_this_page": [o.name for o in options],
            "agent_instructions": (
                (
                    "Do NOT quote counts or totals to the customer — no '85 tours', "
                    "no 'showing 1-10 of 270'. Just present the tours. "
                    "If the customer NAMED a tour that is not in `names_on_this_page` "
                    "(Burj Khalifa, desert safari, dhow cruise...), call search_tours "
                    "again with query=<their words> BEFORE replying — the catalogue "
                    "has ~270 tours and this page is only the cheapest few. Never say "
                    "you do not have a tour's details, and never ask permission to "
                    "search: just search. "
                )
                + (
                    f"These are filtered to {tf!r} tours only, so they are NOT the "
                    f"full catalogue — never imply they are. "
                    if tf
                    else ""
                )
                + (
                    # Naming the columns HERE rather than in the system prompt:
                    # this instruction travels with the data, so it lands in the
                    # model's context right next to the rows it describes and
                    # costs the prompt nothing. The `Type` column was lost when
                    # the table columns were last changed.
                    "Table columns, in this order: Tour | Price/adult (`price_display`) "
                    "| Type (`category`) | Duration | Transfer (`sharing_display`) "
                    "| Cancellation (`cancellation_display`); add Start times "
                    "(`slots_display`) when any row has real times. Mark "
                    "`is_recommended` rows with a star. Never move these into a "
                    "footnote. "
                )
                + (
                    f"If they ask for more, call search_tours again with "
                    f"offset={shown_end} and max_results={max_results * 2}."
                    if remaining
                    else "That is everything matching; there are no more to show."
                )
            ),
            "query": query or None,
            "search_params": {
                "destination": destination_city,
                "travel_date": travel_date,
                "tour_category_id": tour_category_id,
            },
        }
    except TripPlannerError as e:
        logger.warning("search_tours error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("search_tours unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


from mcp_tools.result_cache import cache_impl

search_tours_tool = tool(cache_impl("search_tours")(_impl))
search_tours_tool.name = "search_tours"
mcp.tool(name="search_tours")(_impl)
