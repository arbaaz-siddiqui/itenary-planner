"""agent_tools — Plain (non-MCP) tools + central registry.

Plain tools are internal to the agent's flow (intake, budget ops, travel info).
They don't get exposed via MCP.

The registry combines plain tools + the 7 MCP tools into ALL_TOOLS.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain_core.tools import BaseTool, tool

from core import format_inr
from rules import (
    BUDGET_SCOPE_ALL_INCLUSIVE,
    compute_floor_price,
    compute_hotel_block_cost,
    floor_for_scope,
    is_budget_feasible,
    price_group,
    resolve_party,
    sum_trip_total,
)


# =============================================================================
# Plain tools
# =============================================================================
@tool
def collect_guest_info_tool(
    adults: int,
    children: int = 0,
    child_ages: list[int] | None = None,
    nationality: str = "India",
    interests: list[str] | None = None,
) -> dict[str, Any]:
    """Record structured guest info. Returns echo dict or error."""
    if adults < 1:
        return {"error": True, "message": "At least one adult is required"}
    if children < 0:
        return {"error": True, "message": "children cannot be negative"}
    ages = list(child_ages or [])
    if len(ages) != children:
        return {
            "error": True,
            "message": f"child_ages length ({len(ages)}) must equal children ({children})",
        }
    for a in ages:
        if not (0 <= a <= 17):
            return {"error": True, "message": f"Invalid child age: {a}"}
    return {
        "adults": adults,
        "children": children,
        "child_ages": ages,
        "infants": sum(1 for a in ages if a < 2),
        "nationality": nationality,
        "interests": list(interests or []),
    }


@tool
def check_floor_tool(
    budget_inr: float,
    cheapest_flight_inr: float,
    cheapest_hotel_inr: float,
    visa_inr: float = 0.0,
    transfer_inr: float = 0.0,
    budget_scope: str = BUDGET_SCOPE_ALL_INCLUSIVE,
) -> dict[str, Any]:
    """Compute minimum viable trip cost and check budget feasibility.

    Returns explicit feasibility + over-budget gap so the agent doesn't have
    to interpret a signed "headroom" number (which it has historically gotten
    wrong, dropping the sign).

    `budget_scope` MUST reflect what the customer's stated budget covers — ASK
    them before calling:
      - "all_inclusive"               → budget covers flights + hotel + everything
      - "excludes_flights"            → budget is hotel + on-ground only (they book flights)
      - "excludes_flights_and_hotel"  → budget is tours/transfers/visa only
    The feasibility verdict is measured against the floor for THAT scope, while
    `full_floor_inr` always reports the complete flight+hotel+visa+transfer floor
    for reference.
    """
    full_floor = compute_floor_price(
        cheapest_flight_inr=cheapest_flight_inr,
        cheapest_hotel_inr=cheapest_hotel_inr,
        visa_inr=visa_inr,
        transfer_inr=transfer_inr,
    )
    floor = floor_for_scope(
        cheapest_flight_inr=cheapest_flight_inr,
        cheapest_hotel_inr=cheapest_hotel_inr,
        visa_inr=visa_inr,
        transfer_inr=transfer_inr,
        budget_scope=budget_scope,
    )
    feasible = is_budget_feasible(budget_inr=budget_inr, floor_inr=floor)
    gap = round(budget_inr - floor, 2)  # signed: positive = headroom, negative = over budget
    is_over = gap < 0
    return {
        "agent_instructions": (
            "Quote floor_display and headroom/over_by EXACTLY — never write "
            "'approximately', 'about' or '~' before them. They are computed "
            "figures, not estimates."
        ),
        "budget_inr": round(budget_inr, 2),
        "budget_scope": budget_scope,
        "floor_inr": floor,
        "floor_display": format_inr(floor),
        "full_floor_inr": full_floor,
        "full_floor_display": format_inr(full_floor),
        "is_feasible": feasible,
        # Only populate one of these. Never both.
        "headroom_inr": gap if not is_over else 0.0,
        "headroom_display": format_inr(gap) if not is_over else None,
        "over_by_inr": abs(gap) if is_over else 0.0,
        "over_by_display": format_inr(abs(gap)) if is_over else None,
        "status": "OVER_BUDGET" if is_over else "WITHIN_BUDGET",
        "recommended_action": (
            f"Trip floor is {format_inr(floor)}, which is {format_inr(abs(gap))} "
            f"over the {format_inr(budget_inr)} budget. Suggest dropping a night, "
            f"cheaper flight/hotel, or raising the budget."
            if is_over
            else f"Floor {format_inr(floor)} fits in budget {format_inr(budget_inr)}. "
            f"{format_inr(gap)} left for tours, transfers, meals."
        ),
    }


@tool
def apply_selection_tool(
    component: str,
    item_id: str,
    title: str,
    price_inr: float,
    current_spent: float = 0.0,
) -> dict[str, Any]:
    """Record a selection. Returns the new total spent."""
    new_spent = round(current_spent + price_inr, 2)
    return {
        "selection": {
            "component": component,
            "item_id": item_id,
            "title": title,
            "price_inr": price_inr,
            "price_display": format_inr(price_inr),
        },
        "new_total_spent_inr": new_spent,
        "new_total_spent_display": format_inr(new_spent),
    }


@tool
def compute_remaining_budget_tool(budget_total_inr: float, spent_inr: float) -> dict[str, Any]:
    """Compute budget remaining after spending. Reports over-budget honestly.

    Returns a SIGNED remaining value (can be negative) so the caller can see
    when a trip is over budget. Do NOT clamp to zero — that hides the gap
    from the LLM and leads to math errors like "₹0 remaining" when the
    customer is actually ₹58,000 over budget.
    """
    remaining = round(budget_total_inr - spent_inr, 2)
    is_over = remaining < 0
    return {
        "budget_total_inr": round(budget_total_inr, 2),
        "spent_inr": round(spent_inr, 2),
        "remaining_inr": remaining,  # SIGNED — negative when over budget
        "remaining_display": format_inr(remaining),
        "is_over_budget": is_over,
        "over_by_inr": abs(remaining) if is_over else 0.0,
        "over_by_display": format_inr(abs(remaining)) if is_over else None,
        "status": "OVER_BUDGET" if is_over else "WITHIN_BUDGET",
        "recommended_action": (
            f"Trip is over budget by {format_inr(abs(remaining))}. "
            "Suggest one of: drop a night, choose cheaper flight/hotel, "
            "or stretch budget."
            if is_over
            else f"On track. {format_inr(remaining)} remaining for tours, transfers, meals."
        ),
    }


@tool
def resolve_party_tool(
    total_people: int | None = None,
    adults: int | None = None,
    children: int = 0,
    child_ages: list[int] | None = None,
) -> dict[str, Any]:
    """Resolve a loosely-stated headcount into an exact adult/child/infant split.

    USE THIS whenever the customer gives a headcount — especially a bare total
    like "6 people" with "2 kids". That means 4 ADULTS + 2 children, NOT
    6 adults + 2 children. Do NOT do this subtraction in your head; you have
    gotten it inverted before.

    Pass `total_people` + `children` (adults are derived), OR an explicit
    `adults` count. Always read back the returned `summary` to confirm the
    split with the customer before searching flights/hotels.
    """
    try:
        return resolve_party(
            total_people=total_people,
            adults=adults,
            children=children,
            child_ages=child_ages,
        )
    except ValueError as e:
        return {"error": True, "message": str(e)}


@tool
def price_group_tool(
    per_adult_inr: float,
    adults: int,
    children: int = 0,
    child_ages: list[int] | None = None,
) -> dict[str, Any]:
    """Convert a per-adult price into a group total, applying child discounts.

    Tours, restaurants, and visas are quoted PER ADULT. To get the group total
    for 4 adults + 2 children (ages 5, 7) you must apply each child's age-tier
    discount and sum — never multiply per_adult by the headcount in your head.
    Pass child_ages so discounts apply; without ages, children are charged at
    full adult fare (and `ages_assumed_full_fare` is True — ask for ages).

    Returns group_total_inr plus a per-head breakdown.
    """
    try:
        result = price_group(
            per_adult_inr=per_adult_inr,
            adults=adults,
            children=children,
            child_ages=child_ages,
        )
    except ValueError as e:
        return {"error": True, "message": str(e)}
    result["group_total_display"] = format_inr(result["group_total_inr"])
    return result


@tool
def compute_hotel_block_cost_tool(
    per_room_per_night_inr: float,
    rooms: int,
    nights: int,
) -> dict[str, Any]:
    """Total hotel cost for a block of identical rooms = rooms x nights x rate.

    USE THIS for any "3 rooms for 3 nights" math. Pass the per-room-per-night
    rate (hotels are priced per room, not per person — do not divide by party
    size). For rooms at different rates, call this once per rate or use
    sum_trip_total with one line per room type.
    """
    try:
        result = compute_hotel_block_cost(
            per_room_per_night_inr=per_room_per_night_inr,
            rooms=rooms,
            nights=nights,
        )
    except ValueError as e:
        return {"error": True, "message": str(e)}
    result["block_total_display"] = format_inr(result["block_total_inr"])
    return result


@tool
def sum_trip_total_tool(line_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum named line items into ONE inclusive trip total, deterministically.

    USE THIS any time you state a combined trip cost (flights + hotel + tours +
    transfers + visa). Never add the components in your head — that produced
    three different totals in one conversation. Each line item is
    {"label": "Flights", "amount_inr": 224934}. Quote the returned total_inr.
    """
    result = sum_trip_total(line_items)
    result["total_display"] = format_inr(result["total_inr"])
    return result


# CLIENT_PLACEHOLDER: review and expand
DESTINATION_TIPS: dict[str, dict[str, Any]] = {
    "dubai": {
        "best_months": "November to March (15-30°C)",
        "avoid_months": "June to August (often above 40°C)",
        "currency": {
            "local": "AED (UAE Dirham)",
            "tip": "USD widely accepted; cards everywhere",
        },
        "visa": {
            "indian_passport": "Visa required (60-day tourist)",
            "note": "Apply 5-7 working days before travel",
        },
        "transportation": "Metro covers most tourist areas; taxis metered; Careem/Uber widely used",
        "must_see": [
            "Burj Khalifa (book 124/148 floor in advance)",
            "Dubai Mall + Dubai Fountain",
            "Desert Safari (sunset)",
            "Dhow Cruise (Marina or Creek)",
            "Palm Jumeirah + Atlantis",
            "Old Dubai (Al Fahidi, Gold/Spice Souk)",
        ],
        "dietary_notes": (
            "Halal food everywhere. Strong Indian veg presence in Bur Dubai "
            "and Karama. Jain food available at several restaurants."
        ),
        "etiquette": [
            "Dress modestly in public (shoulders + knees covered)",
            "Public drinking is prohibited; alcohol in licensed venues only",
            "Friday is the weekly holy day",
        ],
    },
}


@tool
def get_destination_tips_tool(destination: str = "Dubai") -> dict[str, Any]:
    """Static travel tips for a destination."""
    key = destination.strip().lower().split(",")[0].strip()
    tips = DESTINATION_TIPS.get(key)
    if tips is None:
        return {
            "error": True,
            "message": f"No tips available for {destination!r}",
            "available": list(DESTINATION_TIPS.keys()),
        }
    return {"destination": destination, **tips}


_DISPLAYABLE_KINDS = {"flight", "hotel", "tour", "transfer", "restaurant", "visa", "package"}


@tool
def display_options_tool(kind: str) -> dict[str, Any]:
    """Render the most recent search results for `kind` as visual cards (with
    images where available) in the web UI.

    Call this when the customer asks to SEE the options visually — "show me",
    "render the images", "show with pictures", "let me see them", etc. It does
    not fetch anything; it tells the web surface to display the cards from the
    latest matching search. On WhatsApp (no UI) it is a harmless no-op.

    Args:
        kind: one of flight, hotel, tour, transfer, restaurant, visa, package.

    Returns a `{display: True, kind}` signal the web app reads. You still write a
    short text reply; do NOT paste raw image URLs — the cards show the images.
    """
    k = (kind or "").strip().lower().rstrip("s")  # tolerate "tours" -> "tour"
    if k not in _DISPLAYABLE_KINDS:
        return {
            "error": True,
            "message": f"Cannot display {kind!r}. Supported: {sorted(_DISPLAYABLE_KINDS)}",
        }
    return {"display": True, "kind": k}


_SCHEDULE_KINDS = {"flight", "hotel", "tour", "transfer", "restaurant", "activity", "free"}


@tool
def build_trip_schedule_tool(days: list[dict[str, Any]]) -> dict[str, Any]:
    """Lay the agreed plan out as a day-by-day, time-slotted SCHEDULE that the web
    app renders as a calendar (time rows × day columns).

    CALL THIS when the customer wants to SEE their plan on a timeline/calendar —
    "show me the schedule", "what's the day-by-day plan", "lay it out by time",
    or after you've assembled an itinerary they like. Use ONLY real items you've
    actually discussed/searched (hotels, tours, transfers, flights, meals) — never
    invent activities or times. It's fine to give sensible times for things like
    "morning at the souk"; just don't invent the activity itself.

    Args:
        days: one entry per day, each:
            {
              "date": "2026-08-03",            # ISO date (or "Day 1" if unknown)
              "label": "Arrival & Downtown",   # short day theme (optional)
              "items": [
                {
                  "start": "10:00",            # 24h HH:MM
                  "end": "11:00",              # optional
                  "title": "Arrive at DXB",
                  "kind": "transfer",          # flight|hotel|tour|transfer|restaurant|activity|free
                  "detail": "Private cab to hotel"   # optional, short
                }, ...
              ]
            }

    Returns a `{schedule: True, days: [...]}` signal the web app reads to draw the
    calendar. Still write a short text reply; don't paste the whole grid as text.
    """
    if not isinstance(days, list) or not days:
        return {"error": True, "message": "days must be a non-empty list of day plans"}
    clean_days: list[dict[str, Any]] = []
    for d in days:
        if not isinstance(d, dict):
            continue
        items_in = d.get("items") or []
        items: list[dict[str, Any]] = []
        for it in items_in:
            if not isinstance(it, dict) or not str(it.get("title", "")).strip():
                continue
            kind = str(it.get("kind", "activity")).strip().lower().rstrip("s")
            if kind not in _SCHEDULE_KINDS:
                kind = "activity"
            items.append(
                {
                    "start": str(it.get("start", "")).strip(),
                    "end": str(it.get("end", "")).strip(),
                    "title": str(it.get("title", "")).strip(),
                    "kind": kind,
                    "detail": str(it.get("detail", "")).strip(),
                }
            )
        # keep items in time order when a start time is given
        items.sort(key=lambda x: x["start"] or "99:99")
        clean_days.append(
            {
                "date": str(d.get("date", "")).strip() or f"Day {len(clean_days) + 1}",
                "label": str(d.get("label", "")).strip(),
                "items": items,
            }
        )
    return {"schedule": True, "days": clean_days, "total_days": len(clean_days)}


# Last itinerary built by plan_itinerary_tool, so the PDF can reuse its day
# plan rather than erroring and forcing the model to retry.
_LAST_PLAN: dict[str, Any] = {}


def _end_date_for(start_date: str, nights: int) -> str:
    """start_date + nights, ISO. Empty string when the input is unusable."""
    from datetime import datetime, timedelta

    try:
        d = datetime.strptime(str(start_date), "%Y-%m-%d").date()
        return (d + timedelta(days=max(1, int(nights or 1)))).isoformat()
    except (ValueError, TypeError):
        return ""


@lru_cache(maxsize=8)
def _tour_facts_index(travel_date: str) -> tuple[tuple[str, float, str, str], ...]:
    """(name_lower, price_per_adult, duration, timeslots_json) for the catalogue.

    Cached: a plan with six tours must not trigger six catalogue searches.
    """
    import json as _json

    try:
        from mcp_tools.search_tours import _impl as _search_tours

        res = _search_tours(
            destination_city="Dubai", travel_date=travel_date, max_results=400
        )
        return tuple(
            (
                str(o.get("name") or "").lower(),
                float(o.get("price_per_adult_inr") or 0.0),
                str(o.get("duration") or ""),
                _json.dumps(o.get("timeslots") or []),
            )
            for o in (res.get("options") or [])
            if o.get("name")
        )
    except Exception:  # noqa: BLE001 — missing facts are reported, never fatal
        return ()


def _tour_facts_lookup(title: str, travel_date: str) -> dict[str, Any]:
    """Price, duration and slots for a tour named in a plan.

    Exact match first, then containment either way round, because the model
    often shortens a name ("Abu Dhabi City Tour" for "Abu Dhabi City Tour from
    Dubai"). Returns {} when nothing matches — never a guess.
    """
    import json as _json

    want = (title or "").strip().lower()
    if not want:
        return {}
    index = _tour_facts_index(travel_date)
    hit = next((row for row in index if row[0] == want), None)
    if hit is None:
        # Longest containment match wins, so "Dubai Frame" does not grab
        # "Dubai Frame Ticket" ahead of an exact-ish alternative.
        cands = [r for r in index if want in r[0] or r[0] in want]
        hit = max(cands, key=lambda r: len(r[0])) if cands else None
    if hit is None:
        return {}
    _name, price, duration, slots_json = hit
    try:
        slots = _json.loads(slots_json)
    except Exception:  # noqa: BLE001
        slots = []
    return {"price": price, "duration": duration, "timeslots": slots}


def _tour_price_lookup(title: str, travel_date: str) -> float:
    """Per-adult price for a tour named in a plan. 0.0 when unmatched."""
    return float(_tour_facts_lookup(title, travel_date).get("price") or 0.0)


@tool
def plan_itinerary_tool(
    start_date: str,
    nights: int,
    tours: list[dict[str, Any]] | None = None,
    arrival_time: str = "",
    hotel_name: str = "",
    hotel_checkin_time: str = "",
    departure_time: str = "",
    adults: int = 0,
    flight_total_inr: float = 0.0,
    hotel_total_inr: float = 0.0,
    visa_per_adult_inr: float = 0.0,
    transfer_total_inr: float = 0.0,
    fill_days: bool = True,
    origin_city: str = "",
    destination_city: str = "Dubai",
) -> dict[str, Any]:
    """Build a VALIDATED day-by-day itinerary with real times.

    CALL THIS instead of writing a day-by-day plan yourself. It checks each
    tour against its published timeslots and the rest policy, so the times are
    computed facts rather than guesses. Prefer this over
    build_trip_schedule_tool whenever tours are involved.

    Rules it enforces (you do not have to reason about these):
      - 3 hours to settle in after landing before any activity.
      - A slot-based tour is only placed at a REAL published slot time.
      - Nothing starts after 21:00; nothing runs past midnight.
      - The departure day stays clear of tours.
      - A tour whose last slot falls before the customer is free MOVES to a
        later day — the Burj-Khalifa-on-arrival-day case.

    Args:
        start_date: arrival date, ISO yyyy-mm-dd
        nights: nights booked (days = nights + 1)
        tours: the tours the customer wants, each:
            {"name": str,
             "duration": "0-Days 2-Hours 0-Minutes"   # from search_tours
             "timeslots": [...]}                      # from get_tour_timeslots
            Pass `timeslots` whenever you have them — without them the tour is
            scheduled by duration alone and may be placed at an unbookable time.
        arrival_time: flight landing time "HH:MM" — pass it, it drives the rest rule
        hotel_name: for the transfer line
        hotel_checkin_time: "HH:MM" if known; otherwise a 60min transfer is assumed
        departure_time: return flight "HH:MM", so the last day is kept clear
        flight_total_inr: the TOTAL price of the flight you showed the customer,
            for the whole party. Pass it whenever you have shown flights. Flight
            fares change between searches, so if you omit this the tool has to
            re-search and may bill a different fare than the one on screen --
            that is how an "Emirates Rs 40,909" plan came to be billed at
            Rs 64,792.
        hotel_total_inr: total stay price of the hotel you showed, all rooms.
        visa_per_adult_inr: per-adult visa price you quoted (tool multiplies).
        transfer_total_inr: total transfer price you showed, if any.
        origin_city: departure city, only needed as a fallback if you cannot
            pass flight_total_inr.

    Returns:
        {days: [{day_number, date, label, items: [{start, end, title, kind,
         detail, slot_id}]}], excluded: [{title, reason, detail}], policy: {...}}

        **Relay `excluded` reasons to the customer verbatim.** They are specific
        ("last slot is 18:00, but you are not free until 20:45") and explain why
        something is not on the plan. Silently dropping a tour they asked for is
        the failure this field exists to prevent.
    """
    from scheduling import build_itinerary

    # The model routinely passes bare names — [{"name": "Dubai Frame"}] — with
    # no price and no duration. Resolve both from the catalogue first, otherwise
    # every tour is treated as a 2-hour block and the day plan is fiction.
    enriched: list[dict[str, Any]] = []
    for t in tours or []:
        if not isinstance(t, dict):
            continue
        row = dict(t)
        title = str(row.get("name") or row.get("title") or "").strip()
        if title:
            facts = _tour_facts_lookup(title, start_date)
            if not row.get("duration") and facts.get("duration"):
                row["duration"] = facts["duration"]
            if not (row.get("price_per_adult_inr") or row.get("price_inr")) and facts.get("price"):
                row["price_per_adult_inr"] = facts["price"]
            if not row.get("timeslots") and facts.get("timeslots"):
                row["timeslots"] = facts["timeslots"]
        enriched.append(row)
    tours = enriched

    # TOP UP so the trip is not mostly empty. Three tours cannot fill five days,
    # and telling the model to "search for more and call again" did not work —
    # it printed "Free Day" three times instead. So we add recommended-first
    # catalogue tours (skipping anything already chosen) until there is roughly
    # one day's activity per day. `auto_filled` names every addition so the
    # agent can say which were its own picks and offer to swap them.
    auto_filled: list[str] = []
    from scheduling import SchedulePolicy as _Policy, parse_duration_minutes

    per_day = _Policy().target_activity_min_per_day
    if fill_days and int(nights or 0) >= 1:
        def _dedupe_key(name: str) -> str:
            """Collapse near-duplicate catalogue entries.

            The catalogue lists the same attraction several times ("Dubai Frame"
            / "Dubai Frame Ticket", "Butterfly Garden Dubai" / "Dubai Butterfly
            Garden Ticket"). Booking a customer onto both is embarrassing, so
            match on the significant words, order-independent.
            """
            drop = {
                "the", "a", "an", "in", "of", "at", "to", "from", "with", "and",
                "dubai", "abu", "dhabi", "ticket", "tickets", "tour", "tours",
                "experience", "entry", "pass", "combo",
            }
            words = {w for w in name.lower().replace("-", " ").split() if w not in drop}
            return " ".join(sorted(words)) or name.lower()

        chosen_keys = {_dedupe_key(str(t.get("name") or "")) for t in tours}
        # Budget: full days for the middle of the trip, half days for arrival and
        # departure. NO slack multiplier — an earlier 15% over-shoot added 14
        # tours and crammed six into one day.
        # A trip of N nights has N usable days (arrival and departure count as
        # roughly one between them). Budget a full day of activity for each.
        usable_days = max(1, int(nights or 1))
        want_min = per_day * usable_days
        have_min = sum(parse_duration_minutes(t.get("duration")) or 120 for t in tours)

        # No add cap: an earlier cap of `usable_days * 2` stopped the top-up
        # before the budget was met and left the last day blank. The minute
        # budget plus the duplicate filter are the real limiters.
        catalogue = list(_tour_facts_index(start_date))
        for name, price, duration, _slots in catalogue:
            if have_min >= want_min:
                break
            if not name or price <= 0:
                continue
            key = _dedupe_key(name)
            if key in chosen_keys:
                continue
            mins = parse_duration_minutes(duration) or 120
            if mins > per_day:  # cannot fit a single day on its own
                continue
            chosen_keys.add(key)
            tours.append(
                {"name": name.title(), "price_per_adult_inr": price, "duration": duration}
            )
            auto_filled.append(name.title())
            have_min += mins

    _sched_kwargs = dict(
        start_date=start_date,
        nights=nights,
        arrival_time=arrival_time or None,
        hotel_name=hotel_name,
        hotel_checkin_time=hotel_checkin_time or None,
        departure_time=departure_time or None,
    )
    out = build_itinerary(tours=tours, **_sched_kwargs)
    if out.get("error"):
        return out

    # CLOSE THE LOOP. The open-loop estimate above could still leave a day bare
    # (short tours, tours that would not fit a slot). Keep adding catalogue
    # tours while any day is empty — this is what makes "Free Day" impossible
    # rather than merely unlikely.
    if fill_days:
        pool = [
            (n, p, d)
            for n, p, d, _s in _tour_facts_index(start_date)
            if n and p > 0 and (parse_duration_minutes(d) or 120) <= per_day
        ]
        guard = 0
        while out.get("empty_days") and guard < 40:
            guard += 1
            added = False
            for name, price, duration in pool:
                key = _dedupe_key(name)
                if key in chosen_keys:
                    continue
                chosen_keys.add(key)
                tours.append(
                    {"name": name.title(), "price_per_adult_inr": price, "duration": duration}
                )
                auto_filled.append(name.title())
                added = True
                break
            if not added:
                break  # catalogue exhausted; nothing more we can honestly add
            out = build_itinerary(tours=tours, **_sched_kwargs)
            if out.get("error"):
                return out

    # COST THE TRIP HERE. Left to itself the model wrote an "ESTIMATED TOTAL"
    # table with a guessed tours line — twice, differing by Rs 3,000, and both
    # UNDER the real figure by Rs 15-18k (it guessed ~25,000/~28,000 when the
    # five tours it scheduled actually cost Rs 43,264 for four adults). The
    # prompt already forbids estimates; a 26B model does not hold that rule
    # under a full context. So the arithmetic happens in code.
    pax = max(1, int(adults or 0))

    # SELF-PRICE anything the caller left at 0. Depending on the model to pass
    # these produced a tours-only total and a follow-up that asked the customer
    # to repeat their dates. The searches are cached, so this is cheap.
    _lookup_notes: list[str] = []
    if not flight_total_inr and origin_city:
        try:
            from mcp_tools.search_flights import _impl as _sf

            # origin_city was passed to THIS tool, not invented by the model
            # mid-search, so the unstated-origin guard does not apply here.
            _f = _sf(
                origin_city=origin_city,
                destination_city=destination_city or "Dubai",
                departure_date=start_date,
                return_date=_end_date_for(start_date, nights),
                adults=pax,
                max_results=5,
                assume_missing=True,
            )
            _opts = [o for o in (_f.get("options") or []) if o.get("price_inr")]
            if _opts:
                flight_total_inr = min(float(o["price_inr"]) for o in _opts)
                # Say so loudly: this is a FRESH fare, not the one on screen.
                _lookup_notes.append(
                    "flights: re-searched because flight_total_inr was not "
                    "passed — this is the cheapest fare available NOW and may "
                    "differ from the one shown earlier. Tell the customer the "
                    "flight figure needs reconfirming, or call again passing "
                    "flight_total_inr from the fare you displayed"
                )
        except Exception as e:  # noqa: BLE001 — a missing component is reported
            logger.debug("flight lookup for total failed: %s", e)

    if not hotel_total_inr:
        try:
            from mcp_tools.search_hotels import _impl as _sh

            # 4 adults => 2 rooms. Getting this wrong put a one-room rate in a
            # four-adult total.
            _rooms = [{"adults": 2, "children": 0, "child_ages": []} for _ in range((pax + 1) // 2)]
            _h = _sh(
                destination_city=destination_city or "Dubai",
                check_in=start_date,
                check_out=_end_date_for(start_date, nights),
                rooms=_rooms,
            )
            _cands = _h.get("options") or []
            if hotel_name:
                _want = hotel_name.strip().lower()
                _match = [o for o in _cands if _want in str(o.get("hotel_name", "")).lower()]
                _cands = _match or _cands
            if _cands and _cands[0].get("price_inr"):
                hotel_total_inr = float(_cands[0]["price_inr"])
                _lookup_notes.append(f"hotel ({_cands[0].get('hotel_name')})")
        except Exception as e:  # noqa: BLE001
            logger.debug("hotel lookup for total failed: %s", e)

    if not visa_per_adult_inr:
        try:
            from mcp_tools.get_visa_info import _impl as _gv

            _v = _gv(
                destination_country="UAE",
                nationality_country="India",
                travel_date=start_date,
            )
            _vo = [o for o in (_v.get("options") or []) if o.get("price_per_person_inr")]
            if _vo:
                visa_per_adult_inr = min(float(o["price_per_person_inr"]) for o in _vo)
                _lookup_notes.append("visa (30-day single)")
        except Exception as e:  # noqa: BLE001
            logger.debug("visa lookup for total failed: %s", e)

    if not transfer_total_inr and hotel_name:
        try:
            from mcp_tools.search_transfers import _impl as _st

            _t = _st(arrival_date=start_date, hotel_name=hotel_name, adults=pax)
            _to = [o for o in (_t.get("options") or []) if o.get("price_inr")]
            if _to:
                transfer_total_inr = min(float(o["price_inr"]) for o in _to)
                _lookup_notes.append("airport transfer")
        except Exception as e:  # noqa: BLE001
            logger.debug("transfer lookup for total failed: %s", e)
    scheduled: list[dict[str, Any]] = []
    seen: set[str] = set()
    for day in out.get("days") or []:
        for item in day.get("items") or []:
            if item.get("kind") != "tour":
                continue
            title = str(item.get("title") or "")
            if title in seen:
                continue
            seen.add(title)
            per_adult = 0.0
            for t in tours or []:
                if isinstance(t, dict) and str(t.get("name") or t.get("title") or "") == title:
                    try:
                        per_adult = float(
                            t.get("price_per_adult_inr") or t.get("price_inr") or 0.0
                        )
                    except (TypeError, ValueError):
                        per_adult = 0.0
                    break
            # PRICE IT OURSELVES if the caller passed a bare name. The model
            # routinely sends [{"name": "Dubai Citytour"}] with no price, having
            # already shown the customer ₹2,022 two messages earlier — and the
            # total then read "Tours: pricing to be confirmed" while the trip
            # total silently excluded them. Looking the price up here means a
            # bare name still produces a correct total.
            if per_adult <= 0 and title:
                per_adult = _tour_price_lookup(title, start_date)
            scheduled.append(
                {
                    "tour": title,
                    "per_adult_inr": round(per_adult, 2),
                    "group_inr": round(per_adult * pax, 2),
                }
            )

    tours_total = round(sum(s["group_inr"] for s in scheduled), 2)
    priced = [s for s in scheduled if s["per_adult_inr"] > 0]
    visa_total = round(float(visa_per_adult_inr or 0.0) * pax, 2)
    components = {
        "flights": round(float(flight_total_inr or 0.0), 2),
        "hotel": round(float(hotel_total_inr or 0.0), 2),
        "tours": tours_total,
        "visa": visa_total,
        "transfers": round(float(transfer_total_inr or 0.0), 2),
    }
    # Cache for generate_itinerary_pdf_tool: the day plan it needs already
    # exists here, so a PDF request should never bounce on MissingDayPlans.
    global _LAST_PLAN
    _LAST_PLAN = {
        "days": out.get("days") or [],
        "start_date": start_date,
        "nights": nights,
        "adults": pax,
        "total_inr": out.get("total_inr"),
        "cost_breakdown": out.get("cost_breakdown") or {},
    }
    out["auto_filled_tours"] = auto_filled
    out["tour_costs"] = scheduled
    out["cost_breakdown"] = components
    out["total_inr"] = round(sum(components.values()), 2)
    out["adults"] = pax
    missing = [s["tour"] for s in scheduled if s["per_adult_inr"] <= 0]
    out["costing_note"] = (
        "These totals are computed, not estimated — quote `total_inr` and the "
        "`cost_breakdown` lines VERBATIM. Never write your own total, and never "
        "label it 'estimated'. "
        + (
            f"No price was supplied for: {', '.join(missing)} — say those are "
            f"still to be priced rather than guessing."
            if missing
            else f"All {len(priced)} scheduled tours are priced."
        )
        + (
            f" Priced automatically: {', '.join(_lookup_notes)} — these are the "
            f"cheapest available; say so and offer alternatives."
            if _lookup_notes
            else ""
        )
        + (
            ""
            if components["flights"] and components["hotel"]
            else " Flights and/or hotel could not be priced, so the total covers "
            "only the listed lines — say which are missing. NEVER ask the "
            "customer to repeat dates, origin or party size."
        )
    )
    return out


@tool
def compose_customer_payment_summary_tool(
    total_inr_inclusive: float,
    travel_date_iso: str,
    cancellation_cutoff_iso: str | None = None,
    is_international: bool = True,
) -> dict[str, Any]:
    """Build the customer-facing payment summary when the user is ready to book.

    Per client spec: ONE inclusive total + payment schedule + EMI hint +
    any compliance docs. NO per-component breakdown, no GST/TCS line items.

    Args:
        total_inr_inclusive: Final all-in INR total the customer pays.
        travel_date_iso: Trip start date 'yyyy-mm-dd'.
        cancellation_cutoff_iso: Earliest "free cancel until" across all booked
            services. If unknown, omit — the schedule will use a default
            buffer before travel.
        is_international: True for Dubai trips (triggers TCS / PAN requirement).

    Returns:
        A dict ready to present to the customer:
            total_inr_inclusive, total_display
            payment_schedule: [{label, amount_inr, amount_display, due_date_iso}, ...]
            emi_starting: "₹X/month over 12 months" (or None)
            free_cancellation_until_display (or None)
            compliance_documents (list of doc names, may be empty)
            disclaimer: "All taxes and fees included."
    """
    from rules import compose_customer_payment_summary

    summary = compose_customer_payment_summary(
        total_inr_inclusive=total_inr_inclusive,
        travel_date_iso=travel_date_iso,
        cancellation_cutoff_iso=cancellation_cutoff_iso,
        is_international=is_international,
    )

    return {
        "total_inr_inclusive": summary.total_inr_inclusive,
        "total_display": format_inr(summary.total_inr_inclusive),
        "payment_schedule": [
            {
                "label": inst.label,
                "amount_inr": inst.amount_inr,
                "amount_display": format_inr(inst.amount_inr),
                "due_date_iso": inst.due_date_iso,
            }
            for inst in summary.schedule.installments
        ],
        "schedule_bucket": summary.schedule.bucket,
        "emi_starting": (
            f"{format_inr(summary.emi_starting_inr_per_month)}/month over "
            f"{max(summary.emi_tenures_available)} months"
            if summary.emi_starting_inr_per_month
            else None
        ),
        "emi_tenures_available": summary.emi_tenures_available,
        "free_cancellation_until_iso": summary.free_cancellation_until_iso,
        "compliance_documents": summary.compliance_documents_required,
        "disclaimer": "All taxes and fees included.",
    }


@tool
def generate_itinerary_pdf_tool(
    destination: str = "Dubai",
    origin_city: str = "",
    start_date: str = "",
    end_date: str = "",
    nights: int | None = None,
    party_summary: str = "",
    customer_name: str = "",
    reference: str = "",
    overview: str = "",
    day_plans: list[dict[str, Any]] | None = None,
    components: list[dict[str, Any]] | None = None,
    visa: dict[str, Any] | None = None,
    inclusions: list[str] | None = None,
    exclusions: list[str] | None = None,
    total_inr: float | None = None,
    payment_schedule: list[dict[str, Any]] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Generate the branded itinerary PDF. CALL IT THE MOMENT IT IS ASKED FOR.

    Triggers: "send it", "make the pdf", "PDF bhej do", "share the itinerary",
    the PDF button. Returns a download link.

    NEVER refuse, defer, or ask a question first — no preconditions. If they
    have not picked a flight/hotel, build it around what you would recommend
    and name those in your reply ("Built around the Emirates flight and
    Movenpick — say the word and I'll swap either"). A PDF is a proposal, not a
    booking. Searched nothing yet? Search in the SAME turn, then call this.
    Asking "which flight would you like?" is our top customer complaint.

    Use ONLY figures from search/pricing tools — never invent. INR;
    `amount_inr: null` for On Request.

    Args:
        destination/origin_city/start_date/end_date/nights/party_summary: trip facts.
        customer_name, reference: optional personalization (quote/booking ref).
        overview: 1-2 sentence intro to the trip.
        day_plans: [{"title": "Day 1 - Arrival", "items": [...]}] where each item
            is EITHER a plain string ("Check-in") or, preferred, a dict rendered
            as a Time/Activity table:
              {"title": "Arrival at DXB", "start": "12:25",
               "detail": "Private transfer to hotel", "kind": "transfer"}
            `start` may be "" for untimed entries (the time column is then
            omitted). `kind` is transfer/tour/flight/hotel/meal.
        components: priced services
            [{"label": "Flights (Air India)", "detail": "BOM->DXB return, 2 adults",
              "amount_inr": 217366}]. amount_inr null => "On Request".
        visa: the visa the customer chose, REUSED from the `get_visa_info` result
            you already have — never ask the customer for this at PDF time:
              {"visa_type": "30 Days Single Entry Tourist Visa",
               "entry_type": "Single", "stay_duration": "30 Days",
               "validity": "58 Days From Date Of Issue",
               "processing": "Confirm with supplier",
               "price_display": "On Request",
               "documents": ["Passport Copy", "Passport Size Photograph", ...]}
            Omit only if the trip genuinely has no visa component.
        inclusions/exclusions/notes: lists of plain strings.
        total_inr: final all-inclusive total.
        payment_schedule: [{"label": "Deposit", "amount_inr": 78598,
                            "due_date_iso": "2026-06-10"}].

    Returns:
        {itinerary_id, download_url (or None), filename, summary} — share the
        download link with the customer. On WhatsApp the service attaches the
        PDF automatically when an itinerary_id is produced.
    """
    from itinerary_store import public_url_for, save_itinerary_pdf

    # A customer who was just given a day-by-day plan in chat expects to see it
    # in the document. The model kept writing a full 5-day schedule in the reply
    # and then calling this with day_plans empty, producing a PDF with a price
    # table and no itinerary. day_plans is optional, so nothing objected.
    #
    # Refuse rather than ship a hollow PDF: this returns an actionable error the
    # model can immediately retry, instead of a link the customer will complain
    # about. Only enforced for multi-night trips, where a schedule is the point.
    if not (day_plans or []) and (nights or 0) >= 1:
        # Reuse the plan we just built rather than bouncing the call. The model
        # otherwise burns a full round-trip re-sending a schedule it already
        # produced — which happened on every single PDF request.
        cached = (_LAST_PLAN or {}).get("days") or []
        _cached_start = str((_LAST_PLAN or {}).get("start_date") or "")
        _asked_start = str(start_date or "")
        # Same trip if the dates agree, or if the caller simply did not pass one
        # (the common case) and the night count still matches.
        _same_trip = bool(cached) and (
            _cached_start == _asked_start
            # No date passed: the only plan in play is the one just built.
            or (not _asked_start
                and int((_LAST_PLAN or {}).get("nights") or -1) == int(nights or -2))
        )
        if _same_trip:
            day_plans = [
                {"title": d.get("title") or f"Day {d.get('day_number')}",
                 "items": d.get("items") or []}
                for d in cached
            ]
            if not start_date:
                start_date = _cached_start
            if nights is None:
                nights = (_LAST_PLAN or {}).get("nights")
    if not (day_plans or []) and (nights or 0) >= 1:
        return {
            "error": True,
            "error_type": "MissingDayPlans",
            "message": (
                f"day_plans is empty for a {nights}-night trip, so the PDF would "
                "have no itinerary — only a price table. Retry this call with "
                "day_plans filled in: one entry per day, each item a dict like "
                '{"title": "Dubai Fountain Show", "start": "18:00", '
                '"detail": "Burj Khalifa lake ride", "kind": "tour"}. Use the '
                "day-by-day plan you already described to the customer — do not "
                "invent new activities, and do not ask them to repeat it."
            ),
            "retry_with": {"day_plans": "[{title, items:[{title,start,detail,kind}]}, ...]"},
        }

    # Backfill end times the model dropped. It sends `start` only, so without
    # this the PDF shows "09:45" where the chat showed "09:45-11:45".
    if day_plans and (_LAST_PLAN or {}).get("days"):
        _ends: dict[tuple[str, str], str] = {}
        for _d in _LAST_PLAN["days"]:
            for _it in _d.get("items") or []:
                _t = str(_it.get("title") or "").strip().lower()
                _st = str(_it.get("start") or "").strip()
                _en = str(_it.get("end") or "").strip()
                if _t and _st and _en:
                    _ends[(_t, _st)] = _en
        for _d in day_plans:
            for _it in (_d.get("items") or []) if isinstance(_d, dict) else []:
                if not isinstance(_it, dict) or _it.get("end"):
                    continue
                _k = (str(_it.get("title") or "").strip().lower(),
                      str(_it.get("start") or "").strip())
                if _k in _ends:
                    _it["end"] = _ends[_k]

    data: dict[str, Any] = {
        "destination": destination,
        "origin_city": origin_city,
        "start_date": start_date,
        "end_date": end_date,
        "nights": nights,
        "party_summary": party_summary,
        "customer_name": customer_name,
        "reference": reference,
        "overview": overview,
        "day_plans": day_plans or [],
        "components": components or [],
        "visa": visa or None,
        "inclusions": inclusions or [],
        "exclusions": exclusions or [],
        "total_inr": total_inr,
        "payment_schedule": payment_schedule or [],
        "notes": notes or [],
    }
    try:
        itinerary_id, _path = save_itinerary_pdf(data)
    except Exception as e:  # never crash the turn over a PDF
        return {"error": True, "message": f"Could not generate the PDF: {e}"}

    url = public_url_for(itinerary_id)
    return {
        "itinerary_id": itinerary_id,
        "download_url": url,
        "filename": f"{destination}-itinerary.pdf",
        "summary": (
            f"Itinerary PDF ready ({destination}, {party_summary or 'your party'})."
            + (f" Download: {url}" if url else " Available to download in the app.")
        ),
    }


# =============================================================================
# Tool registry — ONE place, ALL tools
# =============================================================================
def _build_all_tools() -> list[BaseTool]:
    # Import MCP tools here (they have decorator side effects)
    from mcp_tools.get_exchange_rate import get_exchange_rate_tool
    from mcp_tools.get_flight_details import get_flight_details_tool
    from mcp_tools.lookup_entity import lookup_entity_tool
    from mcp_tools.get_hotel_description import get_hotel_description_tool
    from mcp_tools.get_hotel_info import get_hotel_info_tool
    from mcp_tools.get_hotel_reviews import get_hotel_reviews_tool
    from mcp_tools.get_package_details import get_package_details_tool
    from mcp_tools.get_restaurant_details import get_restaurant_details_tool
    from mcp_tools.get_tour_details import get_tour_details_tool
    from mcp_tools.get_tour_options import get_tour_options_tool
    from mcp_tools.get_tour_timeslots import get_tour_timeslots_tool
    from mcp_tools.get_transfer_details import get_transfer_details_tool
    from mcp_tools.get_visa_info import get_visa_info_tool
    from mcp_tools.list_visa_countries import list_visa_countries_tool
    from mcp_tools.list_city_hotels import list_city_hotels_tool
    from mcp_tools.list_packages import list_packages_tool
    from mcp_tools.lookup_hotel_city import lookup_hotel_city_tool
    from mcp_tools.search_flights import search_flights_tool
    from mcp_tools.search_hotels import search_hotels_tool
    from mcp_tools.search_restaurants import search_restaurants_tool
    from mcp_tools.search_tours import search_tours_tool
    from mcp_tools.search_transfers import search_transfers_tool

    return [
        # Autocomplete / name-to-ID lookup (MCP-exposed, no auth)
        lookup_entity_tool,
        # Search/list tools (MCP-exposed)
        search_flights_tool,
        search_hotels_tool,
        search_tours_tool,
        search_transfers_tool,
        search_restaurants_tool,
        get_visa_info_tool,
        list_visa_countries_tool,
        list_packages_tool,
        get_exchange_rate_tool,
        # Detail tools (MCP-exposed)
        get_flight_details_tool,
        get_tour_details_tool,
        get_tour_options_tool,
        get_tour_timeslots_tool,
        get_transfer_details_tool,
        get_restaurant_details_tool,
        get_package_details_tool,
        # Hotel static-content tools (MCP-exposed; Hotels-only token)
        lookup_hotel_city_tool,
        list_city_hotels_tool,
        get_hotel_info_tool,
        get_hotel_description_tool,
        get_hotel_reviews_tool,
        # Plain tools (agent-only)
        collect_guest_info_tool,
        resolve_party_tool,
        check_floor_tool,
        price_group_tool,
        compute_hotel_block_cost_tool,
        sum_trip_total_tool,
        apply_selection_tool,
        compute_remaining_budget_tool,
        get_destination_tips_tool,
        compose_customer_payment_summary_tool,
        generate_itinerary_pdf_tool,
        display_options_tool,
        build_trip_schedule_tool,
        plan_itinerary_tool,
    ]


ALL_TOOLS: list[BaseTool] = _build_all_tools()


# =============================================================================
# Per-surface tool sets
# =============================================================================
# Each surface gets only the tools it can use: fewer schema tokens per request,
# and fewer ways for the model to pick the wrong tool.
_UI_ONLY_TOOLS = frozenset({
    "display_options_tool",     # renders image cards — web only
    "build_trip_schedule_tool", # renders a calendar — web only
})

# Tools that only make sense where the customer can receive a file/link.
_DOCUMENT_TOOLS = frozenset({"generate_itinerary_pdf_tool"})


def tools_for_surface(surface: str) -> list[BaseTool]:
    """Tools appropriate to a surface.

    voice     — no screen, no attachments: drop UI renderers and the PDF.
    whatsapp  — no image cards, but PDFs are delivered as attachments.
    streamlit — everything.
    """
    s = (surface or "").strip().lower()
    if s == "voice":
        blocked = _UI_ONLY_TOOLS | _DOCUMENT_TOOLS
    elif s == "whatsapp":
        blocked = _UI_ONLY_TOOLS
    else:
        blocked = frozenset()
    if not blocked:
        return list(ALL_TOOLS)
    return [t for t in ALL_TOOLS if t.name not in blocked]
