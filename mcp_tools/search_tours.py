"""search_tours — agent + MCP tool."""

from __future__ import annotations

import functools
import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_tour_rates, call_tour_search
from core import TripPlannerError
from mcp_tools.server import mcp
from mcp_tools.tour_pricing import party_label
from parsers import parse_tour_response
from reference_data_loader import resolve_city

logger = logging.getLogger(__name__)

# Timeslot fan-out budget. One supplier call is ~5s, so we go wide (one worker
# per tour on the page) and cap total wait: a page of 10 costs ~5-7s, not 50s.
_SLOT_MAX_WORKERS = 24
_SLOT_FETCH_DEADLINE_S = 8.0
# Two supplier calls per tour at ~2.5s each. A transfer-bearing tour that
# misses this deadline shows no price at all, so the budget is generous and
# the pool is wide enough that every candidate starts in the first wave.
_TRANSFER_MAX_WORKERS = 24
_TRANSFER_DEADLINE_S = 14.0



def _tours_table(options: list) -> str:
    """Render every option as one markdown row.

    Built here rather than left to the model: asked for 13 rows in prose, it
    still sent 5, which made our inventory look a third of its real size.
    """
    # Extras is only worth a column when some row has one, otherwise every
    # tour carries a dash. Customers were never told add-ons existed at all.
    show_extras = any(getattr(o, "addon_names", None) for o in options)
    # "Total for party" holds the supplier's grand total per transfer type;
    # a bare "Transfer" header read as a surcharge to add to Price/adult.
    cols = ["Tour", "Price/adult", "Type", "Duration", "Total for party", "Cancellation"]
    if show_extras:
        cols.append("Extras")
    cols.append("Start times")
    head = (
        "| " + " | ".join(cols) + " |" + chr(10)
        + "|" + "|".join("---" for _ in cols) + "|"
    )
    rows = []
    for o in options:
        star = "⭐ " if getattr(o, "is_recommended", False) else ""
        cells = [
            f"{star}{getattr(o, 'name', '') or ''}",
            getattr(o, "price_display", "") or "",
            getattr(o, "category", "") or "",
            getattr(o, "duration", "") or "-",
            getattr(o, "sharing_display", "") or "",
            getattr(o, "cancellation_display", "") or "",
        ]
        if show_extras:
            names = getattr(o, "addon_names", None) or []
            cells.append(", ".join(n.replace(" (Add-on)", "") for n in names) or "-")
        cells.append(getattr(o, "slots_display", "") or "")
        rows.append("| " + " | ".join(str(c).replace("|", "/") for c in cells) + " |")
    return chr(10).join([head, *rows])


def _recommended_first(options: list) -> list:
    """Supplier-recommended tours first, then cheapest.

    `is_recommended` was parsed from the supplier's `isRecommanded` flag but
    never used, so results came back in raw supplier order. Ranking cannot use
    rating: `tourrating` and `reviewsCount` are 0 for ALL 332 Dubai tours.
    Sort is stable, so equal-priced tours keep supplier order.
    """
    return sorted(options, key=lambda o: (not o.is_recommended, o.price_per_adult_inr))


def _party_totals_line(rows: list[dict[str, Any]], party: str, top: int = 3) -> str:
    """'**Total for 2 adults:** A from ₹9,780.77, B ₹3,660, C ₹3,800.'"""
    parts = [
        f"{r.get('name')} {r['party_total_display'].split(' total for ')[0]}"
        for r in rows[:top]
        if r.get("name") and r.get("party_total_display") and " total for " in r["party_total_display"]
    ]
    if not parts:
        return ""
    return f"**Total for {party}:** " + ", ".join(parts) + "."


def _party_total(o: Any, adults: int, child_ages: list[int] | None) -> str:
    """What the customer's whole party pays for this row, ready to quote.

    The cards show a price per person; the reply should say what the party
    pays. Built here so the model never multiplies: from the supplier's own
    party totals when the row has them (cheapest transfer type), else the
    per-adult ticket times the adults. A ticket-only row with children has no
    honest total without the child rate, so it says so instead.
    """
    from core import format_inr
    from mcp_tools.tour_pricing import party_label

    party = party_label(adults, child_ages or [])
    if getattr(o, "sold_per_vehicle", False):
        # One price per vehicle (airport pickup): multiplying it by the
        # party quoted two adults Rs 5,016.08 for one Rs 2,508.04 car.
        return f"{format_inr(o.price_per_adult_inr)} per vehicle"
    totals = [
        float(p.get("total_inr") or p.get("price_inr") or 0)
        for p in (getattr(o, "transfer_prices", None) or [])
        if isinstance(p, dict)
    ]
    totals = [t for t in totals if t > 0]
    if totals:
        return f"from {format_inr(min(totals))} total for {party}"
    if child_ages:
        return f"{format_inr(o.price_per_adult_inr)} per adult; child price on request"
    return f"{format_inr(round(o.price_per_adult_inr * adults, 2))} total for {party}"


def _attach_transfer_prices(
    options: list, travel_date: str, adults: int, child_ages: list[int] | None = None
) -> None:
    """Attach the party's FINAL total per transfer type to each tour row.

    toursearchlistrate has one flat per-adult rate; the transfer split lives
    in /api/tours/options + /api/tours/optionRate. Each total is the
    supplier's own grand total for the whole party (children included) on
    that transfer type -- the figure gujjutours.com charges -- so the agent
    quotes it and never adds a transfer to a ticket price. Concurrent,
    best-effort: a failed lookup keeps the flat display.
    """
    import concurrent.futures as _cf

    from booking_api.endpoints import call_tour_options
    from mcp_tools.tour_pricing import (
        party_label,
        party_prices,
        prices_display,
        sold_per_unit,
    )

    ages = [int(a) for a in child_ages or []]
    party = party_label(int(adults), ages)

    def _one(o: Any) -> None:
        try:
            raw = call_tour_options(tour_id=int(o.tour_id), travel_date=travel_date)
            opts = ((raw or {}).get("result") or {}).get("tourOptionlist") or []
            if not opts:
                return
            # This response already lists every bookable variant, add-ons
            # included. Recording it here costs no extra call and is what lets
            # a row advertise its own add-ons and variants.
            o.variant_count = len(opts)
            o.addon_names = [
                str(v.get("optionName") or "").strip()
                for v in opts
                if "add-on" in str(v.get("optionName") or "").lower()
            ]
            # The row stands for the tour's main product: the first option
            # that is neither an add-on nor sold per unit.
            main = next(
                (
                    v for v in opts
                    if "add-on" not in str(v.get("optionName") or "").lower()
                    and not sold_per_unit(v)
                ),
                None,
            )
            if main is None:
                return
            priced = party_prices(int(o.tour_id), main, travel_date, int(adults), ages)
            if not priced:
                o.transfer_lookup_failed = True
                return
            o.transfer_prices = priced
            o.priced_option_name = str(main.get("optionName") or "").strip()
            o.transfer_price_display = prices_display(priced, party)
        except Exception as e:  # noqa: BLE001 — best-effort enrichment
            logger.debug("transfer price lookup failed for %s: %s", o.tour_id, e)
        finally:
            # The lookup ran to a conclusion, whatever it concluded. A tour left
            # False was cut off by the deadline, and must NOT be described as
            # having no published rates — that states a supplier fact we never
            # checked. sharing_display reads this.
            o.transfer_lookup_done = True

    # "Without Transfer" tours have no pickup and so no split to fetch.
    # Skipping them removed ~12 of 15 calls per search — and, because they never
    # enter the pool, the deadline below is spent only on tours that can answer.
    pending = [
        o for o in options
        if o.tour_id
        and "without" not in (getattr(o, "transfer_scenario", "") or "").strip().lower()
    ]
    if not pending:
        return
    # One worker per candidate: a second wave would start after the deadline had
    # already begun, so its tours came back priceless and read as "rates not
    # published" — a supplier fact we had not checked. min(N, 0) is 0, which
    # ThreadPoolExecutor rejects, hence the max(1, ...).
    ex = _cf.ThreadPoolExecutor(max_workers=max(1, min(_TRANSFER_MAX_WORKERS, len(pending))))
    try:
        futures = [ex.submit(_one, o) for o in pending]
        _cf.wait(futures, timeout=_TRANSFER_DEADLINE_S)
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


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
    adults: int,
    tour_category_id: int = 1,
    max_results: int = 10,
    query: str = "",
    force_refresh: bool = False,
    offset: int = 0,
    transfer_type: str = "",
    assume_missing: bool = False,
    child_ages: list[int] | None = None,
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
        adults: how many adults are travelling.
        child_ages: one age per child, e.g. [5, 8]. Every total on a row is
            for adults AND children, so pass them whenever the customer
            mentions children.
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

    # Tour inventory and prices are date-specific (transfer rate plans end
    # 30 Oct 2026), so a guessed date shows the wrong catalogue as fact.
    from rules import missing_search_fields, needs_input_error

    missing = missing_search_fields(travel_date=travel_date)
    if missing and not assume_missing:
        return needs_input_error(missing)

    try:
        city = resolve_city(destination_city)
        if city is None or not city.get("city_id"):
            return {
                "error": True,
                "message": (
                    f"We do not sell tours in {destination_city!r} — the "
                    f"supplier's inventory for this service is Dubai only."
                ),
                "error_type": "UnsupportedRoute",
                # A bare "Unsupported destination" got paraphrased to the
                # customer as "I don't have that in my database", which reads
                # as OUR system being broken rather than us not selling it.
                "agent_instructions": (
                    f"Say plainly that we do not offer tours in that city — we "
                    f"cover Dubai for this service. Never say the data is "
                    f"missing, unavailable, or not in your database, and never "
                    f"imply a technical problem. Offer the Dubai equivalent "
                    f"instead, and do not ask the customer for more details "
                    f"first — the city is the blocker, not their input."
                ),
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
        # A named search returns its whole match set (up to a sane ceiling), so
        # "desert safari" shows all 13 the way the website does, not 10 of 13.
        page_size = max_results
        if query.strip() and not offset and len(ranked) <= 20:
            page_size = len(ranked)
        options = ranked[start : start + page_size]
        shown_end = start + len(options)
        remaining = max(0, len(ranked) - shown_end)

        # Real start times and party totals for this page. Independent
        # fan-outs, so they run side by side: one after the other they cost
        # the sum of both deadlines (~19s on a desert-safari page).
        import concurrent.futures as _cf

        with _cf.ThreadPoolExecutor(max_workers=2) as ex:
            slots = ex.submit(_attach_timeslots, options, travel_date, adults)
            prices = ex.submit(
                _attach_transfer_prices, options, travel_date, adults, child_ages
            )
            slots.result()
            prices.result()

        # Presentation-ready fields so the agent RELAYS these rather than
        # deriving (or omitting) them. The client requires sharing/private and
        # cancellation on EVERY tour — computing them here makes that the
        # default rather than something the model has to remember.
        # Rows that actually carry extras. Used to name the single unambiguous
        # target for a follow-up add-ons question.
        addon_rows = [o for o in options if getattr(o, "addon_names", None)]


        option_dicts = []
        for o in options:
            d = o.model_dump()
            d["sharing_display"] = o.sharing_display
            # Real per-transfer-type prices when the supplier has them.
            if getattr(o, "transfer_prices", None):
                d["transfer_prices"] = o.transfer_prices
                d["transfer_price_display"] = o.transfer_price_display
                d["party_total_option"] = getattr(o, "priced_option_name", "")
            # Named so a follow-up ("does it have add ons?") can be answered
            # from the row the customer is already looking at.
            addons = getattr(o, "addon_names", []) or []
            d["addon_names"] = addons
            d["addons_display"] = (
                f"{len(addons)} add-on(s): " + ", ".join(addons) if addons else ""
            )
            # How many bookable variants sit behind this row. Already
            # harvested from the options call above, so it costs nothing
            # extra. The customer cannot ask "which variant?" when nothing
            # tells them variants exist -- on the website choosing a date
            # shows them, here the model asked "the Gujjutours one or the
            # Rayna one?" and the customer had no way to answer.
            vc = int(getattr(o, "variant_count", 0) or 0)
            d["variant_count"] = vc
            d["variants_display"] = (
                f"{vc} ticket options to choose from" if vc > 1 else ""
            )
            d["cancellation_display"] = o.cancellation_display
            d["price_display"] = o.price_display
            d["party_total_display"] = _party_total(o, int(adults), child_ages)
            d["timeslots"] = getattr(o, "timeslots", [])
            d["is_slot_based"] = bool(getattr(o, "timeslots", []))
            d["slots_display"] = (
                f"{len(o.timeslots)} start times: "
                + ", ".join(x["time"] for x in o.timeslots[:6])
                + (" …" if len(o.timeslots) > 6 else "")
                if getattr(o, "timeslots", []) else "Flexible, no fixed start time"
            )
            option_dicts.append(d)

        return {
            "options": option_dicts,
            "cheapest_price_inr": (options[0].price_per_adult_inr if options else None),
            # `total_results` is this PAGE (kept for back-compat). The two below
            # are what the customer should hear.
            "total_results": len(options),
            # These prices are valid for THIS party size only. The model kept
            # reusing a 1-adult result and multiplying it by the real party
            # size, which is how a 10-person quote came out at 10 x 11,569
            # when the supplier charges 23,138.
            "priced_for_pax": adults + len(child_ages or []),
            "party": party_label(int(adults), [int(a) for a in child_ages or []]),
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
            # The table must have this many rows. A number is harder to skim
            # past than a sentence.
            "rows_to_render": len(options),
            # Pre-built table. The model rendered 5 of 13 rows even when told
            # the exact count, so the row count is no longer its decision.
            "table_markdown": _tours_table(options),
            # The cards show a price per person; this line tells the customer
            # what their whole party pays, quoted as built here.
            "party_totals_markdown": _party_totals_line(
                option_dicts, party_label(int(adults), [int(a) for a in child_ages or []])
            ),
            "agent_instructions": (
                (
                    f"`table_markdown` is the finished table for all "
                    f"{len(options)} options — PASTE IT VERBATIM into your reply, "
                    f"and right after it paste `party_totals_markdown` verbatim "
                    f"(the cards show per-person prices; that line gives the "
                    f"party total). "
                    f"Do not rebuild it, shorten it, or drop rows from it. "
                    f"BUILD A TABLE WITH EXACTLY {len(options)} ROWS — one per "
                    f"entry in `options`, in order, none skipped. Count them "
                    f"before you send: {len(options)} options in, "
                    f"{len(options)} rows out. Trimming to a 'nice' few makes us "
                    f"look like we hold less inventory than we do (the customer "
                    f"saw 13 desert safaris on the website and 5 in chat). "
                    "Each entry in a row's `transfer_prices` is the FINAL total "
                    "for `party` on that transfer type — ticket, transfer and "
                    "vehicles included, exactly what gujjutours.com charges for "
                    "`party_total_option`. Quote it as it is. NEVER add a "
                    "transfer to `price_display` or to another total, never "
                    "multiply or divide it, and never call it 'per person'. If "
                    "the party changes (more people, a child), call search_tours "
                    "again with the real adults and child_ages. "
                    "Never explain a "
                    "tour's shared/private price by hotel distance — that is how "
                    "AIRPORT transfers work, not tours. A row with no "
                    "`transfer_prices` means the supplier has not "
                    "published transfer rates for THAT DATE yet (they run "
                    "out a few weeks ahead) — say exactly that and offer "
                    "to check an earlier date; never say the tour has no "
                    "shared/private option. "
                    "If the customer asks about ONE tour — its options, variants, "
                    "ticket types, add-ons or what is included — call "
                    "get_tour_options with that row's `tour_id` before replying. "
                    "These rows are product lines, not the bookable variants. "
                    "A row with `addon_names` HAS extras: name them from "
                    "`addons_display` when you present it. If the customer then "
                    "asks about add-ons, extras, upgrades or 'what else can I "
                    "add', call get_tour_options with the `tour_id` of the tour "
                    "under discussion — do NOT ask them which tour, and never "
                    "answer that a tour has no add-ons without calling "
                    "get_tour_options for it first. "
                )
                + (
                    "Do NOT quote counts or totals to the customer — no '85 tours', "
                    "no 'showing 1-10 of 270'. Just present the tours. "
                    "If `next_offset` is present there are MORE beyond this "
                    "page: offer them, and call search_tours again with that "
                    "offset if the customer wants to see more. "
                    "If the customer NAMED a tour that is not in `names_on_this_page` "
                    "(Burj Khalifa, desert safari, dhow cruise...), call search_tours "
                    "again with query=<their words> BEFORE replying — the catalogue "
                    "has ~270 tours and this page is only the cheapest few. Never say "
                    "you do not have a tour's details, and never ask permission to "
                    "search: just search. `slots_display` on each row IS the "
                    "start-time list -- read the times from there. Do not call "
                    "get_tour_timeslots to answer a slots question: it needs ids, "
                    "and when those were guessed it returned nothing and Burj "
                    "Khalifa was wrongly called 'flexible' while 33 real slots "
                    "sat in this field. "
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
                    "| Type (`category`) | Duration | Total for party (`sharing_display`) "
                    "| Cancellation (`cancellation_display`); add Extras "
                    "(`addon_names`) when any row has add-ons, then Start times "
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

_cached_impl = cache_impl("search_tours")(_impl)


@functools.wraps(_impl)
def _dated_impl(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """`_impl` behind a check that the CUSTOMER named the day.

    Tour prices are per date, so a date the model picked itself quoted a real
    price for a day nobody asked about. Checked above the cache, which is
    shared across conversations.
    """
    from rules import customer_gave_date, customer_waived_field, needs_input_error

    waived = kwargs.get("assume_missing") and customer_waived_field()
    if not waived and not customer_gave_date():
        return needs_input_error(["travel_date"])
    return _cached_impl(*args, **kwargs)


search_tours_tool = tool(_dated_impl)
search_tours_tool.name = "search_tours"
mcp.tool(name="search_tours")(_impl)
