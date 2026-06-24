"""agent_tools — Plain (non-MCP) tools + central registry.

Plain tools are internal to the agent's flow (intake, budget ops, travel info).
They don't get exposed via MCP.

The registry combines plain tools + the 7 MCP tools into ALL_TOOLS.
"""

from __future__ import annotations

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
    inclusions: list[str] | None = None,
    exclusions: list[str] | None = None,
    total_inr: float | None = None,
    payment_schedule: list[dict[str, Any]] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Generate the branded, downloadable itinerary PDF once the customer is happy.

    CALL THIS when the customer confirms they like the plan ("looks good",
    "send it", "can I get this in writing", "share the itinerary"). It renders
    the Gujju Tours letterhead (header + footer on every page) with the trip
    laid out clearly, saves it, and returns a download link.

    Use ONLY real numbers you already obtained from search/pricing tools — never
    invent figures here. Money fields are INR. Pass `amount_inr: null` for any
    service that is On Request.

    Args:
        destination/origin_city/start_date/end_date/nights/party_summary: trip facts.
        customer_name, reference: optional personalization (quote/booking ref).
        overview: 1-2 sentence intro to the trip.
        day_plans: [{"title": "Day 1 - Arrival", "items": ["Pickup", "Check-in"]}].
        components: priced services
            [{"label": "Flights (Air India)", "detail": "BOM->DXB return, 2 adults",
              "amount_inr": 217366}]. amount_inr null => "On Request".
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
    from mcp_tools.get_hotel_description import get_hotel_description_tool
    from mcp_tools.get_hotel_info import get_hotel_info_tool
    from mcp_tools.get_hotel_reviews import get_hotel_reviews_tool
    from mcp_tools.get_package_details import get_package_details_tool
    from mcp_tools.get_restaurant_details import get_restaurant_details_tool
    from mcp_tools.get_tour_details import get_tour_details_tool
    from mcp_tools.get_tour_option_details import get_tour_option_details_tool
    from mcp_tools.get_tour_options import get_tour_options_tool
    from mcp_tools.get_transfer_details import get_transfer_details_tool
    from mcp_tools.get_visa_info import get_visa_info_tool
    from mcp_tools.list_city_hotels import list_city_hotels_tool
    from mcp_tools.list_packages import list_packages_tool
    from mcp_tools.lookup_hotel_city import lookup_hotel_city_tool
    from mcp_tools.search_flights import search_flights_tool
    from mcp_tools.search_hotels import search_hotels_tool
    from mcp_tools.search_restaurants import search_restaurants_tool
    from mcp_tools.search_tours import search_tours_tool
    from mcp_tools.search_transfers import search_transfers_tool

    return [
        # Search/list tools (MCP-exposed)
        search_flights_tool,
        search_hotels_tool,
        search_tours_tool,
        search_transfers_tool,
        search_restaurants_tool,
        get_visa_info_tool,
        list_packages_tool,
        get_exchange_rate_tool,
        # Detail tools (MCP-exposed)
        get_flight_details_tool,
        get_tour_details_tool,
        get_tour_options_tool,
        get_tour_option_details_tool,
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
    ]


ALL_TOOLS: list[BaseTool] = _build_all_tools()
