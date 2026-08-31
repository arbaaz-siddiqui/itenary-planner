"""rules — Business logic: pricing rules, client policies, budget tracking.

Sections (search by `# ===`):
    # === PRICING — child discount, infant, group, peak, markup, GST, tourism dirham
    # === POLICIES — cancellation (DYNAMIC from supplier), payment schedule, TCS, EMI
    # === POLICIES — airport routing, food, visa, handoff
    # === BUDGET — floor check, remaining, selection tracking

Per client direction (Tanvir, 2026-05): child pricing, cancellation, and
payment terms are NOT hardcoded — they're driven by supplier API responses
and configurable settings. See PricingSettings in settings.py for thresholds.
"""

from __future__ import annotations

import re
from datetime import date as _date
from datetime import timedelta
from typing import Final

from core import (
    BudgetState,
    CancellationTerm,
    CustomerPaymentSummary,
    PaymentInstallment,
    PaymentSchedule,
    Selection,
    TcsBreakdown,
    _parse_iso,
    days_until,
)
from settings import get_pricing_settings

# =============================================================================
# === PRICING — child discount
# =============================================================================
# CLIENT_PLACEHOLDER: actual age tiers TBD
# Tier shape: (min_age_inclusive, max_age_exclusive, fare_multiplier)
CHILD_AGE_TIERS: Final[list[tuple[int, int, float]]] = [
    (0, 2, 0.0),  # infant
    (2, 6, 0.50),  # young child 50%
    (6, 12, 0.75),  # older child 75%
    (12, 999, 1.00),  # treated as adult
]


def apply_child_discount(adult_price: float, age: int) -> float:
    if age < 0:
        return 0.0
    for min_age, max_age, multiplier in CHILD_AGE_TIERS:
        if min_age <= age < max_age:
            return round(adult_price * multiplier, 2)
    return adult_price


# =============================================================================
# === PRICING — infant
# =============================================================================
# CLIENT_PLACEHOLDER: confirm flat fee or free
INFANT_FEE_INR: Final[float] = 0.0


def apply_infant_pricing(_adult_price: float) -> float:
    return INFANT_FEE_INR


# =============================================================================
# === PRICING — group discount
# =============================================================================
# CLIENT_PLACEHOLDER: tier shape (min_group_size, discount_percent)
GROUP_DISCOUNT_TIERS: Final[list[tuple[int, float]]] = [
    (10, 5.0),
    (20, 8.0),
    (50, 12.0),
]


def apply_group_discount(total_price: float, group_size: int) -> float:
    discount_pct = 0.0
    for min_size, pct in GROUP_DISCOUNT_TIERS:
        if group_size >= min_size:
            discount_pct = pct
    return round(total_price * (1 - discount_pct / 100), 2)


# =============================================================================
# === PRICING — peak season
# =============================================================================
# CLIENT_PLACEHOLDER: which months are peak for Dubai operations
PEAK_MONTHS: Final[set[int]] = {11, 12, 1, 2}
PEAK_SURCHARGE_PERCENT: Final[float] = 15.0


def is_peak_date(iso_date: str) -> bool:
    return _parse_iso(iso_date).month in PEAK_MONTHS


def apply_peak_season_surcharge(price: float, travel_date: str) -> float:
    if is_peak_date(travel_date):
        return round(price * (1 + PEAK_SURCHARGE_PERCENT / 100), 2)
    return round(price, 2)


# =============================================================================
# === PRICING — agency markup
# =============================================================================
# CLIENT_PLACEHOLDER: per-component markup
DEFAULT_MARKUP_PERCENT: Final[float] = 12.0
MARKUP_BY_COMPONENT: Final[dict[str, float]] = {
    "flight": 8.0,
    "hotel": 15.0,
    "tour": 20.0,
    "transfer": 15.0,
    "restaurant": 15.0,
    "visa": 10.0,
    "package": 12.0,
}


def apply_agency_markup(net_price: float, component: str = "default") -> float:
    pct = MARKUP_BY_COMPONENT.get(component, DEFAULT_MARKUP_PERCENT)
    return round(net_price * (1 + pct / 100), 2)


# =============================================================================
# === PRICING — GST
# =============================================================================
# CLIENT_PLACEHOLDER: 5% vs 18%, applicable base TBD
GST_PERCENT: Final[float] = 5.0


def apply_gst(taxable_amount: float) -> float:
    return round(taxable_amount * (1 + GST_PERCENT / 100), 2)


def gst_breakdown(taxable_amount: float) -> dict[str, float]:
    gst = round(taxable_amount * GST_PERCENT / 100, 2)
    return {
        "subtotal": round(taxable_amount, 2),
        "gst_percent": GST_PERCENT,
        "gst": gst,
        "total": round(taxable_amount + gst, 2),
    }


# =============================================================================
# === PRICING — Tourism Dirham
# =============================================================================
# CLIENT_PLACEHOLDER: confirm whether ActivityLinker hotel rates include this
TOURISM_DIRHAM_INCLUDED_IN_RATE: Final[bool] = False
TOURISM_DIRHAM_AED_BY_STAR: Final[dict[int, float]] = {
    5: 20.0,
    4: 15.0,
    3: 10.0,
    2: 7.0,
    1: 7.0,
}


def apply_tourism_dirham(
    nights: int, rooms: int, star_rating: int, aed_to_inr: float = 23.0
) -> float:
    if TOURISM_DIRHAM_INCLUDED_IN_RATE or nights <= 0 or rooms <= 0:
        return 0.0
    aed_per_night = TOURISM_DIRHAM_AED_BY_STAR.get(int(star_rating), 10.0)
    return round(aed_per_night * nights * rooms * aed_to_inr, 2)


# =============================================================================
# === POLICIES — cancellation, payment, TCS (all DYNAMIC per client spec)
# =============================================================================
# The client (Tanvir, 2026-05) explicitly asked us to drop hardcoded
# cancellation tiers. Cancellation policies must be read from the supplier's
# API response (each room/service has its own). The payment schedule is then
# derived from those deadlines + a safety buffer (configurable in settings).
# =============================================================================


def parse_supplier_cancellation_terms(
    raw_policies: list[dict] | None,
) -> tuple[list[CancellationTerm], str | None]:
    """Read cancellation rules from a supplier API response.

    The Technoheaven hotel API returns `CancellationPolicy` as:
        [{"FromDate": "mm-dd-yyyy", "ToDate": "mm-dd-yyyy",
          "CancellationPrice": N, "daysBeforeCheckIn": N | None, "isNRF": bool}]

    Returns: (terms, earliest_free_cancellation_until_iso)
    The 2nd value is the latest date the customer can cancel free of charge,
    derived from the first window with CancellationPrice = 0.
    """
    if not raw_policies:
        return [], None

    terms: list[CancellationTerm] = []
    free_until_iso: str | None = None
    for p in raw_policies:
        if not isinstance(p, dict):
            continue
        price = float(p.get("CancellationPrice", 0) or 0)
        is_free = price == 0 and not p.get("isNRF", False)
        from_date = p.get("FromDate") or p.get("from_date")
        to_date = p.get("ToDate") or p.get("to_date")
        terms.append(
            CancellationTerm(
                from_date=from_date,
                to_date=to_date,
                cancellation_price=price,
                currency=str(p.get("currency") or "INR"),
                is_free_cancellation=is_free,
                days_before_check_in=p.get("daysBeforeCheckIn"),
                is_nrf=bool(p.get("isNRF", False)),
            )
        )
        # Track the latest "free until" date — convert mm-dd-yyyy to ISO
        if is_free and to_date:
            iso = _mmddyyyy_to_iso(to_date)
            if iso and (free_until_iso is None or iso > free_until_iso):
                free_until_iso = iso
    return terms, free_until_iso


def _mmddyyyy_to_iso(d: str) -> str | None:
    """Convert 'mm-dd-yyyy' (Technoheaven format) to 'yyyy-mm-dd' (ISO)."""
    try:
        m, dd, yyyy = d.split("-")
        return f"{yyyy}-{m.zfill(2)}-{dd.zfill(2)}"
    except (ValueError, AttributeError):
        return None


def compute_payment_schedule(
    total_inr: float,
    travel_date_iso: str,
    *,
    cancellation_cutoff_iso: str | None = None,
    today_iso: str | None = None,
) -> PaymentSchedule:
    """Build a dynamic payment schedule for the booking.

    Logic (per client spec):
        - Travel > 120 days away: small deposit now, 2nd installment, final before cutoff
        - Travel 30-120 days away: larger deposit, balance before cutoff
        - Travel < 30 days away: full payment now

    `cancellation_cutoff_iso` is the supplier's "free cancellation until" date
    (across all booked services, take the earliest). The customer's payment
    cutoff is `cancellation_cutoff_iso - payment_safety_buffer_days`.
    """
    cfg = get_pricing_settings()
    ref = _date.fromisoformat(today_iso) if today_iso else _date.today()
    travel_date = _date.fromisoformat(travel_date_iso)
    days_until = (travel_date - ref).days

    # Derive customer payment cutoff (cancellation cutoff minus buffer)
    customer_cutoff_iso = None
    if cancellation_cutoff_iso:
        try:
            cutoff = _date.fromisoformat(cancellation_cutoff_iso)
            customer_cutoff_iso = (
                cutoff - timedelta(days=cfg.payment_safety_buffer_days)
            ).isoformat()
        except ValueError:
            pass

    # Pick the bucket
    if days_until > 120:
        bucket = ">120 days"
        deposit_pct = cfg.deposit_pct_more_than_120_days
    elif days_until >= 30:
        bucket = "30-120 days"
        deposit_pct = cfg.deposit_pct_30_to_120_days
    else:
        bucket = "<30 days"
        deposit_pct = cfg.deposit_pct_within_30_days

    installments: list[PaymentInstallment] = []
    today_iso_str = ref.isoformat()

    if deposit_pct >= 100:
        # Full payment today (within 30 days of travel)
        installments.append(
            PaymentInstallment(
                label="Full payment", amount_inr=round(total_inr, 2), due_date_iso=today_iso_str
            )
        )
    else:
        deposit_amount = round(total_inr * deposit_pct / 100, 2)
        balance = round(total_inr - deposit_amount, 2)
        installments.append(
            PaymentInstallment(
                label="Deposit today", amount_inr=deposit_amount, due_date_iso=today_iso_str
            )
        )
        balance_due_iso = (
            customer_cutoff_iso
            or (travel_date - timedelta(days=cfg.payment_safety_buffer_days)).isoformat()
        )
        installments.append(
            PaymentInstallment(
                label="Final payment", amount_inr=balance, due_date_iso=balance_due_iso
            )
        )

    return PaymentSchedule(
        total_inr=round(total_inr, 2),
        installments=installments,
        cancellation_cutoff_iso=cancellation_cutoff_iso,
        customer_payment_cutoff_iso=customer_cutoff_iso,
        days_until_travel=days_until,
        bucket=bucket,
    )


def compute_tcs(
    total_inr: float,
    *,
    is_overseas_tour_package: bool = True,
) -> TcsBreakdown:
    """Indian Tax Collected at Source (Section 206C(1G)).

    Rules (FY 2024-25, configurable):
        - Overseas tour package: 20% on the full amount (no threshold)
        - Other overseas remittance: 5% above ₹7L threshold
    """
    cfg = get_pricing_settings()
    if is_overseas_tour_package:
        amount = round(total_inr * cfg.tcs_overseas_package_rate_pct / 100, 2)
        return TcsBreakdown(
            applicable=amount > 0,
            rate_pct=cfg.tcs_overseas_package_rate_pct,
            amount_inr=amount,
            reason="Overseas tour package (Section 206C(1G))",
            required_documents=["PAN Card", "Passport"],
        )
    # Non-package overseas — only above threshold
    if total_inr <= cfg.tcs_non_package_threshold_inr:
        return TcsBreakdown(applicable=False, reason="Below threshold")
    taxable = total_inr - cfg.tcs_non_package_threshold_inr
    amount = round(taxable * cfg.tcs_non_package_rate_pct / 100, 2)
    return TcsBreakdown(
        applicable=True,
        rate_pct=cfg.tcs_non_package_rate_pct,
        amount_inr=amount,
        reason=f"Above ₹{cfg.tcs_non_package_threshold_inr:,.0f} threshold",
        required_documents=["PAN Card"],
    )


def compute_emi_options(total_inclusive_inr: float) -> tuple[float, list[int]]:
    """Lightweight EMI surface: divide total by each available tenure.

    This is a sales hint, not a real gateway integration. Returns
    (lowest_monthly_amount, list_of_tenures). Interest is not modeled
    here — real numbers come from the payment gateway at checkout.
    """
    cfg = get_pricing_settings()
    if total_inclusive_inr <= 0 or not cfg.emi_tenures_months:
        return 0.0, []
    longest = max(cfg.emi_tenures_months)
    lowest_monthly = round(total_inclusive_inr / longest, 2)
    return lowest_monthly, sorted(cfg.emi_tenures_months)


def compose_customer_payment_summary(
    total_inr_inclusive: float,
    travel_date_iso: str,
    *,
    cancellation_cutoff_iso: str | None = None,
    is_international: bool = True,
    today_iso: str | None = None,
) -> CustomerPaymentSummary:
    """The single function the agent calls at quote/booking time.

    Aggregates payment schedule + EMI + compliance docs into ONE
    customer-facing view. Internal pricing (GST, supplier rules) stays
    inside; the customer sees only the inclusive total + payment plan.
    """
    schedule = compute_payment_schedule(
        total_inr_inclusive,
        travel_date_iso,
        cancellation_cutoff_iso=cancellation_cutoff_iso,
        today_iso=today_iso,
    )
    emi_lowest, emi_tenures = compute_emi_options(total_inr_inclusive)

    # Compliance docs (TCS triggers PAN requirement for international)
    docs: list[str] = []
    if is_international:
        tcs = compute_tcs(total_inr_inclusive, is_overseas_tour_package=True)
        if tcs.applicable:
            docs = list(tcs.required_documents)

    return CustomerPaymentSummary(
        total_inr_inclusive=round(total_inr_inclusive, 2),
        schedule=schedule,
        emi_starting_inr_per_month=emi_lowest if emi_lowest > 0 else None,
        emi_tenures_available=emi_tenures,
        free_cancellation_until_iso=cancellation_cutoff_iso,
        compliance_documents_required=docs,
    )


# =============================================================================
# === LEGACY: compute_refund kept for back-compat (used by some tests).
# New code should call parse_supplier_cancellation_terms() + price-per-policy
# computations from the actual supplier response.
# =============================================================================
_LEGACY_CANCELLATION_TIERS: Final[list[tuple[int, float]]] = [
    (45, 80.0),
    (30, 50.0),
    (15, 25.0),
    (7, 10.0),
    (0, 0.0),
]


def compute_refund(
    booking_total: float,
    travel_date_iso: str,
    today_iso: str | None = None,
) -> dict[str, float | int]:
    """LEGACY: hardcoded refund tiers.

    Kept for back-compat with older tests. New code should use
    parse_supplier_cancellation_terms() + per-room cancellation_price
    from the supplier API response.
    """
    if today_iso is not None:
        ref = _date.fromisoformat(today_iso)
        days = days_until(travel_date_iso, today=ref)
    else:
        days = days_until(travel_date_iso)
    refund_pct = 0.0
    for min_days, pct in _LEGACY_CANCELLATION_TIERS:
        if days >= min_days:
            refund_pct = pct
            break
    return {
        "refund_amount": round(booking_total * refund_pct / 100, 2),
        "refund_percent": refund_pct,
        "days_until": days,
    }


# =============================================================================
# === POLICIES — airport routing
# =============================================================================
UAE_AIRPORTS: Final[dict[str, dict[str, str]]] = {
    "DXB": {"name": "Dubai International", "city": "Dubai"},
    "DWC": {"name": "Al Maktoum International", "city": "Dubai"},
    "SHJ": {"name": "Sharjah International", "city": "Sharjah"},
    "AUH": {"name": "Abu Dhabi International", "city": "Abu Dhabi"},
}


def suggest_uae_airport(*, budget_inr: float, stay_area: str = "", primary: str = "DXB") -> str:
    """CLIENT_PLACEHOLDER: tune thresholds with client."""
    area = stay_area.lower()
    if "abu dhabi" in area:
        return "AUH"
    if "sharjah" in area:
        return "SHJ"
    if 0 < budget_inr < 30000:
        return "SHJ"
    return primary


# =============================================================================
# === POLICIES — food handling
# =============================================================================
# CLIENT_PLACEHOLDER: actual restaurants client books for these groups
JAIN_FRIENDLY_RESTAURANTS: Final[list[str]] = [
    "Rasoi Ghar (Bur Dubai)",
    "Govinda's (Karama)",
    "Maharaja Bhog (Bur Dubai)",
]
SWAMINARAYAN_FRIENDLY_RESTAURANTS: Final[list[str]] = [
    "BAPS Hindu Mandir Restaurant (Abu Dhabi)",
    "Govinda's (Karama)",
    "Maharaja Bhog (Bur Dubai)",
]
HALAL_ONLY_RESTAURANTS: Final[list[str]] = [
    "Bait Al Mandi",
    "Al Mallah",
    "Ravi Restaurant",
]


def suggest_restaurants(diet: str = "") -> list[str]:
    d = diet.lower()
    if "jain" in d:
        return JAIN_FRIENDLY_RESTAURANTS
    if "swami" in d:
        return SWAMINARAYAN_FRIENDLY_RESTAURANTS
    if "halal" in d:
        return HALAL_ONLY_RESTAURANTS
    return []


# =============================================================================
# === POLICIES — visa edge cases
# =============================================================================
# CLIENT_PLACEHOLDER
STANDARD_TOURIST_VISA_INR: Final[float] = 6500.0
REFUND_VISA_FEE_ON_REJECTION: Final[bool] = False
VISA_DOCUMENT_CHECKLIST: Final[list[str]] = [
    "Passport scan (clear, color, all 4 corners visible)",
    "Passport-size photograph (white background, recent)",
    "Confirmed return flight ticket",
    "Hotel booking confirmation",
    "Bank statement (last 3 months)",
    "PAN card copy",
]


def get_visa_notes() -> dict[str, object]:
    return {
        "standard_price_inr": STANDARD_TOURIST_VISA_INR,
        "refund_on_rejection": REFUND_VISA_FEE_ON_REJECTION,
        "document_checklist": VISA_DOCUMENT_CHECKLIST,
        "common_rejection_reasons": [
            "Passport less than 6 months valid from travel date",
            "Previous overstay in any GCC country",
            "Incomplete bank statement (less than 3 months)",
            "Blurry or cropped passport scan",
        ],
    }


# =============================================================================
# === POLICIES — human handoff
# =============================================================================
# CLIENT_PLACEHOLDER
GROUP_SIZE_HANDOFF_THRESHOLD: Final[int] = 10
BUDGET_HANDOFF_THRESHOLD_INR: Final[float] = 500000
HANDOFF_CONTACT_PHONE: Final[str] = "+91-XXXXX-XXXXX"
HANDOFF_CONTACT_EMAIL: Final[str] = "support@example.com"
HANDOFF_WHATSAPP: Final[str] = "https://wa.me/91XXXXXXXXXX"
HANDOFF_WORKING_HOURS: Final[str] = "10:00 AM – 7:00 PM IST, Mon–Sat"
ALWAYS_ESCALATE_KEYWORDS: Final[list[str]] = [
    "complaint",
    "refund",
    "urgent",
    "emergency",
    "wrong booking",
    "cancel my trip",
    "speak to a human",
    "speak to an agent",
    "speak to manager",
]


def should_hand_off(
    *, group_size: int = 1, budget_inr: float = 0, user_message: str = ""
) -> tuple[bool, str]:
    msg = user_message.lower()
    for keyword in ALWAYS_ESCALATE_KEYWORDS:
        if keyword in msg:
            return True, f"User said: {keyword!r}"
    if group_size > GROUP_SIZE_HANDOFF_THRESHOLD:
        return True, f"Group size {group_size} > threshold {GROUP_SIZE_HANDOFF_THRESHOLD}"
    if budget_inr > BUDGET_HANDOFF_THRESHOLD_INR:
        return True, f"Budget ₹{budget_inr:,.0f} > threshold"
    return False, ""


def get_handoff_contact() -> dict[str, str]:
    return {
        "phone": HANDOFF_CONTACT_PHONE,
        "email": HANDOFF_CONTACT_EMAIL,
        "whatsapp": HANDOFF_WHATSAPP,
        "working_hours": HANDOFF_WORKING_HOURS,
    }


# =============================================================================
# === BUDGET — floor check
# =============================================================================
def compute_floor_price(
    *,
    cheapest_flight_inr: float,
    cheapest_hotel_inr: float,
    visa_inr: float = 0.0,
    transfer_inr: float = 0.0,
    safety_margin_percent: float = 5.0,
) -> float:
    base = cheapest_flight_inr + cheapest_hotel_inr + visa_inr + transfer_inr
    return round(base * (1 + safety_margin_percent / 100), 2)


def is_budget_feasible(*, budget_inr: float, floor_inr: float) -> bool:
    return budget_inr >= floor_inr


# =============================================================================
# === BUDGET — scope of the customer's stated budget
# =============================================================================
# When a customer says "my budget is ₹2.7L", they may mean different things:
# the all-in number, or "₹2.7L on TOP of flights/hotel I'll handle". The agent
# must ask. These constants name the three scopes; the floor check then compares
# the budget only against the components it's meant to cover.
BUDGET_SCOPE_ALL_INCLUSIVE: Final[str] = "all_inclusive"
BUDGET_SCOPE_EXCLUDES_FLIGHTS: Final[str] = "excludes_flights"
BUDGET_SCOPE_EXCLUDES_FLIGHTS_AND_HOTEL: Final[str] = "excludes_flights_and_hotel"

_VALID_BUDGET_SCOPES: Final[frozenset[str]] = frozenset(
    {
        BUDGET_SCOPE_ALL_INCLUSIVE,
        BUDGET_SCOPE_EXCLUDES_FLIGHTS,
        BUDGET_SCOPE_EXCLUDES_FLIGHTS_AND_HOTEL,
    }
)


def floor_for_scope(
    *,
    cheapest_flight_inr: float,
    cheapest_hotel_inr: float,
    visa_inr: float,
    transfer_inr: float,
    budget_scope: str,
    safety_margin_percent: float = 5.0,
) -> float:
    """Compute the floor the budget must clear, given what the budget covers.

    The full floor is always flight + hotel + visa + transfer (+ margin). But if
    the customer's budget EXCLUDES flights (they'll book those separately), the
    budget should only be measured against hotel + visa + transfer. This keeps
    the over/under-budget verdict honest for the scope the customer actually meant.
    """
    if budget_scope not in _VALID_BUDGET_SCOPES:
        budget_scope = BUDGET_SCOPE_ALL_INCLUSIVE

    flight = cheapest_flight_inr
    hotel = cheapest_hotel_inr
    if budget_scope == BUDGET_SCOPE_EXCLUDES_FLIGHTS:
        flight = 0.0
    elif budget_scope == BUDGET_SCOPE_EXCLUDES_FLIGHTS_AND_HOTEL:
        flight = 0.0
        hotel = 0.0

    base = flight + hotel + visa_inr + transfer_inr
    return round(base * (1 + safety_margin_percent / 100), 2)


# =============================================================================
# === BUDGET — party resolution (headcount → adult/child/infant split)
# =============================================================================
def resolve_party(
    *,
    total_people: int | None = None,
    adults: int | None = None,
    children: int = 0,
    child_ages: list[int] | None = None,
) -> dict[str, object]:
    """Turn a loosely-stated headcount into an exact adult/child/infant split.

    Customers say "6 people, 2 kids" — which means 4 ADULTS + 2 children, NOT
    "6 adults + 2 children". The agent has historically gotten this inverted.
    This function does the arithmetic deterministically.

    Provide EITHER total_people (and children) — adults are derived as
    total_people - children — OR an explicit adults count. Infants (under 2)
    are counted from child_ages and reported separately, since most APIs price
    them as lap infants.

    Returns a dict with the resolved counts plus a human-readable `summary`
    the agent should confirm back to the customer before searching.

    Raises ValueError on contradictory input (e.g. more children than people,
    or child_ages length not matching children).
    """
    ages = list(child_ages or [])

    if adults is None:
        if total_people is None:
            raise ValueError("Provide either total_people or adults")
        if children < 0:
            raise ValueError("children cannot be negative")
        if children > total_people:
            raise ValueError(
                f"children ({children}) cannot exceed total_people ({total_people})"
            )
        adults = total_people - children
    else:
        if adults < 0 or children < 0:
            raise ValueError("adults and children cannot be negative")
        if total_people is not None and total_people != adults + children:
            raise ValueError(
                f"total_people ({total_people}) != adults ({adults}) + children ({children})"
            )

    if adults < 1:
        raise ValueError("At least one adult is required")

    if ages and len(ages) != children:
        raise ValueError(
            f"child_ages length ({len(ages)}) must equal children ({children})"
        )
    for a in ages:
        if not (0 <= a <= 17):
            raise ValueError(f"Invalid child age: {a} (expected 0-17)")

    infants = sum(1 for a in ages if a < 2)
    party_total = adults + children
    # Rooms for the whole party, not just the adults. Supplier allows at most
    # 2 children per room, so both the adult and child counts constrain this.
    rooms_needed = max(1, -(-adults // 2), -(-children // 2) if children else 1)
    # Spread adults and children across those rooms so the hotel search prices
    # every traveller. Children ride with adults; ages drive the child tiers.
    _room_plan: list[dict[str, object]] = [
        {"adults": 0, "children": 0, "child_ages": []} for _ in range(rooms_needed)
    ]
    for i in range(adults):
        _room_plan[i % rooms_needed]["adults"] += 1  # type: ignore[operator]
    for i, _age in enumerate(sorted(ages)):
        r = _room_plan[i % rooms_needed]
        r["children"] += 1  # type: ignore[operator]
        r["child_ages"].append(_age)  # type: ignore[union-attr]
    # A room with children but no adult is not bookable — fold it into room 1.
    for r in _room_plan:
        if r["children"] and not r["adults"] and _room_plan[0] is not r:
            _room_plan[0]["children"] += r["children"]  # type: ignore[operator]
            _room_plan[0]["child_ages"].extend(r["child_ages"])  # type: ignore[union-attr]
            r["children"], r["child_ages"] = 0, []
    _room_plan = [r for r in _room_plan if r["adults"] or r["children"]]
    rooms_needed = len(_room_plan) or 1

    # Human-readable confirmation line.
    parts = [f"{adults} adult" + ("s" if adults != 1 else "")]
    if children:
        if ages:
            ages_str = ", ".join(str(a) for a in sorted(ages))
            parts.append(f"{children} child" + ("ren" if children != 1 else "") + f" (ages {ages_str})")
        else:
            parts.append(f"{children} child" + ("ren" if children != 1 else ""))
    summary = " + ".join(parts) + f" = {party_total} travellers"
    if infants:
        summary += f" ({infants} infant" + ("s" if infants != 1 else "") + " under 2)"

    return {
        "adults": adults,
        "children": children,
        "infants": infants,
        "child_ages": sorted(ages),
        "party_total": party_total,
        "billable_for_flights": adults + children,  # infants usually lap-priced separately
        # Rooms the party needs, at the standard 2 adults per room. Without this
        # the model guessed, and quoted 8 adults a 2-room stay.
        "rooms_needed": rooms_needed,
        "rooms": _room_plan,
        "rooms_note": (
            f"{rooms_needed} room(s) for {party_total} traveller(s) "
            f"({adults} adult(s), {children} child(ren)). Pass the `rooms` list "
            f"above straight to search_hotels -- it already splits adults and "
            f"child ages per room. A hotel rate is PER ROOM, so one room's price "
            f"is not the party's stay cost."
        ),
        "summary": summary,
    }


# =============================================================================
# === PRICING — per-person ↔ group total (applies child age discounts)
# =============================================================================
def price_group(
    *,
    per_adult_inr: float,
    adults: int,
    children: int = 0,
    child_ages: list[int] | None = None,
) -> dict[str, object]:
    """Compute a group total from a per-adult price, applying child discounts.

    Tours / restaurants / visas are quoted per adult. A group of 4 adults +
    2 children (ages 5, 7) does NOT cost 6x per_adult — children get the
    age-tier discount (see apply_child_discount / CHILD_AGE_TIERS). This does
    that multiply-and-sum so the agent never does it by hand.

    Returns adults_subtotal, children_subtotal, the group total, and a per-head
    breakdown for transparency.
    """
    if per_adult_inr < 0:
        raise ValueError("per_adult_inr cannot be negative")
    if adults < 0 or children < 0:
        raise ValueError("adults and children cannot be negative")

    ages = list(child_ages or [])
    if ages and len(ages) != children:
        raise ValueError(
            f"child_ages length ({len(ages)}) must equal children ({children})"
        )

    adults_subtotal = round(per_adult_inr * adults, 2)

    child_lines: list[dict[str, object]] = []
    children_subtotal = 0.0
    # If ages are unknown, fall back to charging children at full adult fare
    # (conservative — never under-quote). Better to ask for ages.
    effective_ages = ages if ages else [99] * children
    for age in effective_ages:
        price = apply_child_discount(per_adult_inr, age)
        children_subtotal += price
        child_lines.append({"age": age if age != 99 else None, "price_inr": round(price, 2)})
    children_subtotal = round(children_subtotal, 2)

    group_total = round(adults_subtotal + children_subtotal, 2)
    return {
        "per_adult_inr": round(per_adult_inr, 2),
        "adults": adults,
        "children": children,
        "adults_subtotal_inr": adults_subtotal,
        "children_subtotal_inr": children_subtotal,
        "child_lines": child_lines,
        "group_total_inr": group_total,
        "ages_assumed_full_fare": not ages and children > 0,
    }


# =============================================================================
# === PRICING — hotel room-block cost (rooms x nights)
# =============================================================================
def compute_hotel_block_cost(
    *,
    per_room_per_night_inr: float,
    rooms: int,
    nights: int,
) -> dict[str, object]:
    """Total hotel cost for a block of identical rooms over a stay.

    rooms x nights x per-room-per-night. 3 rooms over 3 nights is exactly the
    kind of small multiply the agent has fumbled — make it a tool call.

    For rooms at DIFFERENT rates, call sum_trip_total with one hotel line per
    distinct room type instead.
    """
    if per_room_per_night_inr < 0:
        raise ValueError("per_room_per_night_inr cannot be negative")
    if rooms < 1:
        raise ValueError("rooms must be at least 1")
    if nights < 1:
        raise ValueError("nights must be at least 1")

    per_room_total = round(per_room_per_night_inr * nights, 2)
    block_total = round(per_room_total * rooms, 2)
    return {
        "per_room_per_night_inr": round(per_room_per_night_inr, 2),
        "rooms": rooms,
        "nights": nights,
        "per_room_total_inr": per_room_total,
        "block_total_inr": block_total,
    }


# =============================================================================
# === BUDGET — deterministic line-item trip total
# =============================================================================
def sum_trip_total(line_items: list[dict[str, object]]) -> dict[str, object]:
    """Sum named line items into ONE inclusive trip total, deterministically.

    Each line item is {"label": str, "amount_inr": number}. This exists so the
    agent never adds flights + hotel + tours + transfers + visa in its head
    (which produced three different totals in one conversation). The returned
    `total_inr` is the single number to quote.
    """
    cleaned: list[dict[str, object]] = []
    total = 0.0
    for item in line_items or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "Item")
        try:
            amount = float(item.get("amount_inr") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        amount = round(amount, 2)
        total += amount
        cleaned.append({"label": label, "amount_inr": amount})
    total = round(total, 2)
    return {
        "line_items": cleaned,
        "total_inr": total,
    }


# =============================================================================
# === BUDGET — selections
# =============================================================================
def add_selection(
    selections: list[Selection],
    new: Selection,
    *,
    replace_existing_component: bool = True,
) -> list[Selection]:
    if replace_existing_component:
        return [s for s in selections if s.component != new.component] + [new]
    return [*selections, new]


def remove_selection(
    selections: list[Selection],
    *,
    component: str | None = None,
    item_id: str | None = None,
) -> list[Selection]:
    if component is None and item_id is None:
        return list(selections)
    return [
        s
        for s in selections
        if not (
            (component is None or s.component == component)
            and (item_id is None or s.item_id == item_id)
        )
    ]


def total_spent(selections: list[Selection]) -> float:
    return round(sum(s.price_inr for s in selections), 2)


def compute_remaining_budget(budget: BudgetState, selections: list[Selection]) -> float:
    return round(max(0.0, budget.total - total_spent(selections)), 2)


# =============================================================================
# === PROMPT-INJECTION GUARD — checked in code BEFORE the model is invoked
# =============================================================================
# Prompt rules alone do not hold on this model (the client demonstrated four
# working attacks), so the guard runs in code: a match never reaches the model.
_INJECTION_PATTERNS: list[tuple[str, str]] = [
    # Prompt / instruction extraction
    (r"\b(system|hidden|internal|secret|original)\s+(prompt|instructions?|rules?|message)", "extraction"),
    (r"\b(print|reveal|show|repeat|output|display|tell)\b.{0,60}\byour\b.{0,30}\b(instructions?|prompt|restrictions?|guidelines)\b", "extraction"),
    (r"\b(exact|verbatim|word[- ]for[- ]word|full)\b.{0,50}\b(text|copy|wording)\b.{0,40}\b(instructions?|prompt|restrictions?|rules?)\b", "extraction"),
    (r"\brules?\s+(were|was)\s+you\s+given\b", "extraction"),
    (r"\bgiven\s+(to\s+you\s+)?by\s+(the\s+)?developer", "extraction"),
    (r"\bwhat\s+does\s+your\s+(system\s+)?prompt\b", "extraction"),
    (r"\byour\s+(instructions?|system\s+prompt)\b", "extraction"),
    # Instruction override
    (r"\b(ignore|disregard|forget|override)\b.{0,30}\b(previous|prior|above|all|your)\b.{0,20}\b(instructions?|rules?)\b", "override"),
    (r"\bpretend\b.{0,50}\b(developer|authoriz|admin|permission)", "override"),
    (r"\bdeveloper\s+(mode|has\s+authoriz)", "override"),
    (r"\bjailbreak\b|\bDAN\s+mode\b", "override"),
    (r"\byou\s+are\s+no\s+longer\b", "override"),
    # Off-topic work the client saw it perform (a full Next.js app)
    (r"\b(next\.?js|react(js)?|python|javascript|typescript|node\.?js|c\+\+|golang|rust|sql|html|css)\b.{0,60}\b(app|application|code|script|component|program|website)\b", "offtopic"),
    (r"\b(write|build|create|generate|implement)\b.{0,40}\b(code|script|program|a\s+web\s*(site|app))\b", "offtopic"),
    (r"\bprogramming\s+questions?\b", "offtopic"),
]

_INJECTION_REPLY = (
    "I'm the Gujju Tours trip planner, so that's outside what I can help "
    "with — but I'd love to get you to Dubai! Tell me your dates and how "
    "many of you are travelling, and I'll pull up flights, hotels and tours."
)


def detect_prompt_injection(text: str) -> str | None:
    """Category of injection attempt, or None for a normal travel message."""
    t = (text or "").strip()
    if not t:
        return None
    for pattern, category in _INJECTION_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return category
    return None


def injection_reply() -> str:
    return _INJECTION_REPLY


def normalize_reply(text: str) -> str:
    """Deterministic cleanup of model output the prompt cannot fully prevent:
    LaTeX arrows leak as raw text off-web, and "~" before an exact figure
    violates the no-approximation rule."""
    if not text:
        return text
    text = text.replace("$\\rightarrow$", "→").replace("\\rightarrow", "→")
    return re.sub(r"[~≈]\s*(₹|Rs\.?\s)", r"\1", text)
