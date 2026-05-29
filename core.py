"""core — Domain types, errors, currency, dates.

The bottom layer. Imports nothing project-internal. Everything else
imports from here.

Sections (search by `# ===`):
    # === ERRORS              error hierarchy
    # === CURRENCY            to_inr, format_inr
    # === DATES               date format helpers
    # === MODELS — flight
    # === MODELS — hotel
    # === MODELS — tour
    # === MODELS — transfer
    # === MODELS — restaurant
    # === MODELS — visa
    # === MODELS — budget + trip
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# === ERRORS
# =============================================================================
class TripPlannerError(Exception):
    """Root of every error in this codebase."""

    code: str = "TRIP_PLANNER_ERROR"

    def __init__(self, message: str, **fields: Any) -> None:
        super().__init__(message)
        self.message = message
        self.fields: dict[str, Any] = fields

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "code": self.code,
            "message": self.message,
            **self.fields,
        }


# --- Booking API errors ---
class BookingApiError(TripPlannerError):
    code = "BOOKING_API_ERROR"

    def __init__(
        self,
        message: str,
        endpoint: str | None = None,
        status_code: int | None = None,
        server_message: str | None = None,
        **fields: Any,
    ) -> None:
        super().__init__(
            message,
            endpoint=endpoint,
            status_code=status_code,
            server_message=server_message,
            **fields,
        )


class BookingApiUnauthorized(BookingApiError):
    code = "BOOKING_API_UNAUTHORIZED"


class BookingApiNotFound(BookingApiError):
    code = "BOOKING_API_NOT_FOUND"


class BookingApiTimeout(BookingApiError):
    code = "BOOKING_API_TIMEOUT"


class BookingApiServerError(BookingApiError):
    code = "BOOKING_API_SERVER_ERROR"


class FlightSearchFailed(BookingApiError):
    code = "FLIGHT_SEARCH_FAILED"


class HotelSearchFailed(BookingApiError):
    code = "HOTEL_SEARCH_FAILED"


class TourSearchFailed(BookingApiError):
    code = "TOUR_SEARCH_FAILED"


class TransferSearchFailed(BookingApiError):
    code = "TRANSFER_SEARCH_FAILED"


class RestaurantSearchFailed(BookingApiError):
    code = "RESTAURANT_SEARCH_FAILED"


class VisaInfoFailed(BookingApiError):
    code = "VISA_INFO_FAILED"


class PackageSearchFailed(BookingApiError):
    code = "PACKAGE_SEARCH_FAILED"


class FlightDetailsFailed(BookingApiError):
    code = "FLIGHT_DETAILS_FAILED"


class TourDetailsFailed(BookingApiError):
    code = "TOUR_DETAILS_FAILED"


class TransferDetailsFailed(BookingApiError):
    code = "TRANSFER_DETAILS_FAILED"


class RestaurantDetailsFailed(BookingApiError):
    code = "RESTAURANT_DETAILS_FAILED"


class PackageDetailsFailed(BookingApiError):
    code = "PACKAGE_DETAILS_FAILED"


# --- Normalization errors ---
class NormalizationError(TripPlannerError):
    code = "NORMALIZATION_ERROR"

    def __init__(self, message: str, missing_field: str | None = None, **fields: Any) -> None:
        super().__init__(message, missing_field=missing_field, **fields)


class FlightNormalizationError(NormalizationError):
    code = "FLIGHT_NORMALIZATION_ERROR"


class HotelNormalizationError(NormalizationError):
    code = "HOTEL_NORMALIZATION_ERROR"


class TourNormalizationError(NormalizationError):
    code = "TOUR_NORMALIZATION_ERROR"


class TransferNormalizationError(NormalizationError):
    code = "TRANSFER_NORMALIZATION_ERROR"


class RestaurantNormalizationError(NormalizationError):
    code = "RESTAURANT_NORMALIZATION_ERROR"


class VisaNormalizationError(NormalizationError):
    code = "VISA_NORMALIZATION_ERROR"


# --- Business rule errors ---
class BusinessRuleError(TripPlannerError):
    code = "BUSINESS_RULE_ERROR"


class OverBudgetError(BusinessRuleError):
    code = "OVER_BUDGET"


class InvalidDateError(BusinessRuleError):
    code = "INVALID_DATE"


class InvalidPassengerCountError(BusinessRuleError):
    code = "INVALID_PASSENGER_COUNT"


class UnsupportedRouteError(BusinessRuleError):
    code = "UNSUPPORTED_ROUTE"


# --- Configuration errors ---
class ConfigurationError(TripPlannerError):
    code = "CONFIGURATION_ERROR"


class MissingApiKey(ConfigurationError):
    code = "MISSING_API_KEY"


class InvalidSettingError(ConfigurationError):
    code = "INVALID_SETTING"


# =============================================================================
# === CURRENCY
# =============================================================================
DEFAULT_FX_RATES: Final[dict[str, float]] = {
    "INR": 1.0,
    "AED": 23.0,
    "USD": 84.0,
    "EUR": 91.0,
    "GBP": 107.0,
    "SGD": 63.0,
}


def to_inr(
    amount: float | int | Decimal,
    source_currency: str,
    rates: dict[str, float] | None = None,
) -> float:
    """Convert amount from source_currency to INR.

    Raises InvalidSettingError if currency is unsupported or rate is non-positive.
    """
    if source_currency is None:
        raise InvalidSettingError("source_currency cannot be None")

    code = source_currency.strip().upper()
    if not code:
        raise InvalidSettingError("source_currency cannot be empty")

    rate_map = rates if rates is not None else DEFAULT_FX_RATES
    if code not in rate_map:
        raise InvalidSettingError(
            f"Unsupported currency: {code!r}",
            currency=code,
            supported=sorted(rate_map.keys()),
        )

    rate = rate_map[code]
    if rate <= 0:
        raise InvalidSettingError(
            f"FX rate for {code} must be positive, got {rate}",
            currency=code,
            rate=rate,
        )

    result = (Decimal(str(amount)) * Decimal(str(rate))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return float(result)


def format_inr(amount: float | int | Decimal | None, include_symbol: bool = True) -> str:
    """Format with Indian thousands grouping: ₹1,00,000 (not ₹100,000)."""
    if amount is None:
        return "₹0" if include_symbol else "0"

    is_negative = amount < 0
    abs_amount = abs(amount)
    whole_part = int(abs_amount)
    fractional_part = abs_amount - whole_part

    whole_str = str(whole_part)
    if len(whole_str) <= 3:
        grouped = whole_str
    else:
        last_three = whole_str[-3:]
        rest = whole_str[:-3]
        chunks: list[str] = []
        while len(rest) > 2:
            chunks.append(rest[-2:])
            rest = rest[:-2]
        if rest:
            chunks.append(rest)
        grouped = ",".join(reversed(chunks)) + "," + last_three

    if fractional_part > 0:
        frac_decimal = Decimal(str(fractional_part)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        frac_str = f"{frac_decimal:.2f}".split(".")[1]
        if frac_str != "00":
            grouped = f"{grouped}.{frac_str}"

    sign = "-" if is_negative else ""
    symbol = "₹" if include_symbol else ""
    return f"{sign}{symbol}{grouped}"


# =============================================================================
# === DATES
# =============================================================================
_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_iso(iso_date: str) -> date:
    if not isinstance(iso_date, str):
        raise InvalidDateError(
            f"Expected ISO date string, got {type(iso_date).__name__}",
            value=str(iso_date),
        )
    if not _ISO_DATE_PATTERN.match(iso_date):
        raise InvalidDateError(f"Date must be 'yyyy-mm-dd', got {iso_date!r}", value=iso_date)
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").date()
    except ValueError as e:
        raise InvalidDateError(f"Invalid date {iso_date!r}: {e}", value=iso_date) from e


def to_dd_mm_yyyy(iso_date: str) -> str:
    """'2026-07-19' → '19-07-2026' (used by Flight + Visa APIs)."""
    return _parse_iso(iso_date).strftime("%d-%m-%Y")


def to_mm_dd_yyyy(iso_date: str) -> str:
    """'2026-07-19' → '07-19-2026' (used by Hotel API)."""
    return _parse_iso(iso_date).strftime("%m-%d-%Y")


def nights_between(check_in: str, check_out: str) -> int:
    """Strictly positive number of nights between two ISO dates."""
    ci = _parse_iso(check_in)
    co = _parse_iso(check_out)
    if co <= ci:
        raise InvalidDateError(
            f"check_out ({check_out}) must be after check_in ({check_in})",
            check_in=check_in,
            check_out=check_out,
        )
    return (co - ci).days


def validate_future_date(iso_date: str, today: date | None = None) -> None:
    """Raise InvalidDateError if the date is today or past."""
    d = _parse_iso(iso_date)
    ref = today if today is not None else date.today()
    if d <= ref:
        raise InvalidDateError(
            f"Date {iso_date} must be after today ({ref.isoformat()})",
            value=iso_date,
            today=ref.isoformat(),
        )


def days_until(iso_date: str, today: date | None = None) -> int:
    """Days from today (or given reference) until the target date."""
    target = _parse_iso(iso_date)
    ref = today if today is not None else date.today()
    return (target - ref).days


# =============================================================================
# === MODELS — flight
# =============================================================================
class PenaltyInfo(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    allowed: bool = False
    amount: float = 0.0
    currency_code: str = "INR"
    penalty_type: str = ""
    last_ticketing_date: str | None = None


class FlightSegment(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    from_airport: str
    from_airport_name: str = ""
    from_city: str = ""
    from_country: str = ""
    to_airport: str
    to_airport_name: str = ""
    to_city: str = ""
    to_country: str = ""
    departure: datetime
    arrival: datetime
    duration_min: int = 0
    layover_min: int = 0
    marketing_airline: str = ""
    marketing_airline_code: str = ""
    operating_airline: str = ""
    operating_airline_code: str = ""
    airline_logo_path: str = ""
    flight_number: str = ""
    aircraft: str = ""
    cabin_class: str = "E"
    cabin_class_text: str = ""
    cabin_class_rbd: str = ""
    departure_terminal: str = ""
    arrival_terminal: str = ""
    seats_remaining: int | None = None
    luggage_info: str = ""
    cabin_luggage_info: str = ""


class FlightOption(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    fare_source_code: str
    itinerary_source_code: str = ""
    price_inr: float
    price_original: float
    currency_original: str = "INR"
    base_fare_inr: float | None = None
    total_tax_inr: float | None = None
    airline: str
    airline_code: str = ""
    stops: int = 0
    route_outbound: str = ""
    route_return: str = ""
    duration_min: int = 0
    segments_outbound: list[FlightSegment] = Field(default_factory=list)
    segments_return: list[FlightSegment] = Field(default_factory=list)
    refundable: bool = False
    fare_type: str = ""
    fare_basis_codes: list[str] = Field(default_factory=list)
    baggage_info: list[str] = Field(default_factory=list)
    cabin_baggage_info: list[str] = Field(default_factory=list)
    penalties: list[PenaltyInfo] = Field(default_factory=list)
    direction: str = "oneway"
    provider: str = ""
    is_refundable_label: str = ""

    @property
    def price_display(self) -> str:
        return format_inr(self.price_inr)


# =============================================================================
# === MODELS — hotel
# =============================================================================
class CancellationTerm(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    from_date: str | None = None
    to_date: str | None = None
    cancellation_price: float = 0.0
    currency: str = "INR"
    is_free_cancellation: bool = False
    # New API fields (Technoheaven CancellationPolicy[*])
    days_before_check_in: int | None = None
    is_nrf: bool = False  # Non-Refundable Fare flag


class HotelRoom(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    room_type_name: str = ""
    price_inr: float
    price_original: float = 0.0
    currency_original: str = "INR"
    meal_name: str = ""
    mapped_meal_name: str = ""
    booking_status: str = "Available"
    cancellation_policy: list[CancellationTerm] = Field(default_factory=list)
    supplier_currency: str = ""
    supplier_name: str = ""

    @property
    def is_free_cancellation(self) -> bool:
        return any(t.is_free_cancellation for t in self.cancellation_policy)


class HotelOption(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    hotel_id: int
    hotel_name: str
    area: str = ""
    price_inr: float
    per_night_inr: float
    nights: int = 1
    currency_original: str = "INR"
    start_price_original: float = 0.0
    stars: float = 0.0
    rooms: list[HotelRoom] = Field(default_factory=list)
    cheapest_room_type: str = ""
    cheapest_board: str = ""
    cheapest_room_supplier: str = ""
    has_free_cancellation: bool = False

    @property
    def price_display(self) -> str:
        return format_inr(self.price_inr)

    @property
    def per_night_display(self) -> str:
        return format_inr(self.per_night_inr)


# =============================================================================
# === MODELS — tour
# =============================================================================
class TourOption(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    tour_id: int
    name: str
    category: str = ""
    price_per_adult_inr: float
    price_original: float = 0.0
    currency_original: str = "INR"
    final_rate_original: float = 0.0
    discount_type: str = ""
    discount_value: float = 0.0
    duration: str = ""
    short_description: str = ""
    full_description: str = ""
    inclusions: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    city_name: str = ""
    country_name: str = ""
    address: str = ""
    rating: float = 0.0
    reviews_count: int = 0
    is_recommended: bool = False
    supplier_name: str = ""
    image_url: str = ""

    @property
    def price_display(self) -> str:
        return format_inr(self.price_per_adult_inr)


# =============================================================================
# === MODELS — transfer
# =============================================================================
class TransferOption(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    transfer_id: str
    unique_key: str = ""
    vehicle_name: str
    vehicle_type: str = ""
    transfer_type: str
    capacity: int = 0
    luggage_capacity: int = 0
    fuel_type: str = ""
    price_inr: float
    price_original: float = 0.0
    currency_original: str = "INR"
    distance_km: float = 0.0
    estimated_time: str = ""
    policy_name: str = ""
    cancellation_policy_summary: str = ""
    image_url: str = ""
    supplier_name: str = ""
    badges: list[str] = Field(default_factory=list)

    @property
    def price_display(self) -> str:
        return format_inr(self.price_inr)


# =============================================================================
# === MODELS — restaurant
# =============================================================================
class RestaurantOption(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    restaurant_id: int
    name: str
    price_per_adult_inr: float
    price_original: float = 0.0
    currency_original: str = "INR"
    cuisine: str = ""
    veg_type: str = ""
    full_address: str = ""
    city: str = ""
    opening_time: str = ""
    closing_time: str = ""
    seating_capacity: int = 0
    rating: float = 0.0
    description: str = ""
    image_url: str = ""

    @property
    def price_display(self) -> str:
        return format_inr(self.price_per_adult_inr)


# =============================================================================
# === MODELS — visa
# =============================================================================
class VisaOption(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    visa_id: int | str
    visa_type: str
    validity: str = ""
    stay_duration: str = ""
    processing_days: int = 0
    entry_type: str = ""
    is_evisa: bool = True
    price_per_person_inr: float = 0.0
    price_original: float = 0.0
    currency_original: str = "INR"
    pricing_available: bool = False
    document_requirements: list[str] = Field(default_factory=list)

    @property
    def price_display(self) -> str:
        if not self.pricing_available or self.price_per_person_inr == 0:
            return "On Request"
        return format_inr(self.price_per_person_inr)


# =============================================================================
# === MODELS — budget + trip
# =============================================================================
class ComponentBudget(BaseModel):
    model_config = ConfigDict(extra="ignore")
    allocated: float = 0.0
    spent: float = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.allocated - self.spent)


class BudgetState(BaseModel):
    model_config = ConfigDict(extra="ignore")
    total: float = 0.0
    currency: str = "INR"
    floor: float = 0.0
    spent: float = 0.0
    flights: ComponentBudget = Field(default_factory=ComponentBudget)
    hotel: ComponentBudget = Field(default_factory=ComponentBudget)
    tours: ComponentBudget = Field(default_factory=ComponentBudget)
    transfers: ComponentBudget = Field(default_factory=ComponentBudget)
    restaurants: ComponentBudget = Field(default_factory=ComponentBudget)
    visa: ComponentBudget = Field(default_factory=ComponentBudget)

    @property
    def remaining(self) -> float:
        return max(0.0, self.total - self.spent)

    @property
    def is_over_budget(self) -> bool:
        return self.spent > self.total

    @property
    def total_display(self) -> str:
        return format_inr(self.total)

    @property
    def remaining_display(self) -> str:
        return format_inr(self.remaining)


class TripPhase(str, Enum):
    INTAKE = "intake"
    FLOOR_CHECK = "floor_check"
    NEGOTIATING = "negotiating"
    SEARCHING = "searching"
    PRESENTING = "presenting"
    FINALIZED = "finalized"


class GuestInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    adults: int = 1
    children: int = 0
    child_ages: list[int] = Field(default_factory=list)
    infants: int = 0


class DateRange(BaseModel):
    model_config = ConfigDict(extra="ignore")
    check_in: str | None = None
    check_out: str | None = None
    nights: int | None = None


class Selection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    component: str
    item_id: str
    title: str
    price_inr: float
    raw: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Payment models (per client spec — payment is dynamic, derived from supplier
# cancellation policies). NEVER exposed in raw form to the customer; the agent
# uses these internally and surfaces only a high-level CustomerPaymentSummary.
# =============================================================================
class PaymentInstallment(BaseModel):
    """One row in the payment schedule."""

    model_config = ConfigDict(extra="ignore")
    label: str  # "Deposit today", "Second installment", "Final payment"
    amount_inr: float
    due_date_iso: str  # "yyyy-mm-dd"


class PaymentSchedule(BaseModel):
    """Output of compute_payment_schedule(). Internal — not the customer view."""

    model_config = ConfigDict(extra="ignore")
    total_inr: float
    installments: list[PaymentInstallment] = Field(default_factory=list)
    cancellation_cutoff_iso: str | None = None  # earliest supplier deadline
    customer_payment_cutoff_iso: str | None = None  # cutoff - safety buffer
    days_until_travel: int = 0
    bucket: str = ""  # ">120 days" | "30-120 days" | "<30 days"


class TcsBreakdown(BaseModel):
    """Tax Collected at Source (Section 206C(1G)). Indian compliance only."""

    model_config = ConfigDict(extra="ignore")
    applicable: bool = False
    rate_pct: float = 0.0
    amount_inr: float = 0.0
    reason: str = ""  # "Overseas tour package", "Above ₹7L threshold"
    required_documents: list[str] = Field(default_factory=list)  # PAN, Aadhaar, Passport


class CustomerPaymentSummary(BaseModel):
    """The sales-focused view we DO show the customer.

    Per client direction: no per-component breakdown, no GST line items,
    no supplier-level rules. Just one inclusive total + a clear payment
    schedule + EMI option. Convince → don't itemize.
    """

    model_config = ConfigDict(extra="ignore")
    total_inr_inclusive: float  # "Includes all taxes and fees"
    schedule: PaymentSchedule
    emi_starting_inr_per_month: float | None = None
    emi_tenures_available: list[int] = Field(default_factory=list)
    free_cancellation_until_iso: str | None = None
    compliance_documents_required: list[str] = Field(default_factory=list)  # for TCS, etc.


class TripState(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=False)
    origin_city: str | None = None
    destination_city: str = "Dubai"
    dates: DateRange = Field(default_factory=DateRange)
    guests: GuestInfo = Field(default_factory=GuestInfo)
    nationality: str = "India"
    budget: BudgetState = Field(default_factory=BudgetState)
    interests: list[str] = Field(default_factory=list)
    preferences: dict[str, Any] = Field(default_factory=dict)
    phase: TripPhase = TripPhase.INTAKE
    pending_question: str = ""
    last_search: dict[str, Any] = Field(default_factory=dict)
    selections: list[Selection] = Field(default_factory=list)
    api_call_log: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def initial_state() -> TripState:
    return TripState()
