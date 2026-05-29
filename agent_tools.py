"""agent_tools — Plain (non-MCP) tools + central registry.

Plain tools are internal to the agent's flow (intake, budget ops, travel info).
They don't get exposed via MCP.

The registry combines plain tools + the 7 MCP tools into ALL_TOOLS.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from core import format_inr
from rules import compute_floor_price, is_budget_feasible


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
) -> dict[str, Any]:
    """Compute minimum viable trip cost and check budget feasibility."""
    floor = compute_floor_price(
        cheapest_flight_inr=cheapest_flight_inr,
        cheapest_hotel_inr=cheapest_hotel_inr,
        visa_inr=visa_inr,
        transfer_inr=transfer_inr,
    )
    feasible = is_budget_feasible(budget_inr=budget_inr, floor_inr=floor)
    headroom = round(budget_inr - floor, 2)
    return {
        "floor_inr": floor,
        "floor_display": format_inr(floor),
        "is_feasible": feasible,
        "headroom_inr": headroom,
        "headroom_display": format_inr(headroom),
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
    """Return remaining budget = max(0, total - spent)."""
    remaining = max(0.0, round(budget_total_inr - spent_inr, 2))
    return {
        "remaining_inr": remaining,
        "remaining_display": format_inr(remaining),
        "is_over_budget": spent_inr > budget_total_inr,
        "percent_used": round(
            min(100.0, (spent_inr / budget_total_inr * 100) if budget_total_inr > 0 else 0),
            1,
        ),
    }


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


# =============================================================================
# Tool registry — ONE place, ALL tools
# =============================================================================
def _build_all_tools() -> list[BaseTool]:
    # Import MCP tools here (they have decorator side effects)
    from mcp_tools.get_flight_details import get_flight_details_tool
    from mcp_tools.get_package_details import get_package_details_tool
    from mcp_tools.get_restaurant_details import get_restaurant_details_tool
    from mcp_tools.get_tour_details import get_tour_details_tool
    from mcp_tools.get_transfer_details import get_transfer_details_tool
    from mcp_tools.get_visa_info import get_visa_info_tool
    from mcp_tools.list_packages import list_packages_tool
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
        # Detail tools (MCP-exposed)
        get_flight_details_tool,
        get_tour_details_tool,
        get_transfer_details_tool,
        get_restaurant_details_tool,
        get_package_details_tool,
        # Plain tools (agent-only)
        collect_guest_info_tool,
        check_floor_tool,
        apply_selection_tool,
        compute_remaining_budget_tool,
        get_destination_tips_tool,
        compose_customer_payment_summary_tool,
    ]


ALL_TOOLS: list[BaseTool] = _build_all_tools()
