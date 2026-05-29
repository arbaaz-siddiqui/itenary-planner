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
