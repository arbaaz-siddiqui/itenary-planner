"""Tests for core — errors, currency, dates, models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from core import (
    BookingApiUnauthorized,
    BudgetState,
    FlightOption,
    FlightSegment,
    InvalidDateError,
    InvalidSettingError,
    TripPlannerError,
    days_until,
    format_inr,
    nights_between,
    to_dd_mm_yyyy,
    to_inr,
    to_mm_dd_yyyy,
    validate_future_date,
)


# =============================================================================
# Errors
# =============================================================================
class TestErrors:
    def test_to_dict_includes_type_and_code(self) -> None:
        e = BookingApiUnauthorized(
            "401 for flights",
            endpoint="/api/Flight/search",
            status_code=401,
            server_message="Invalid token",
        )
        d = e.to_dict()
        assert d["error_type"] == "BookingApiUnauthorized"
        assert d["code"] == "BOOKING_API_UNAUTHORIZED"
        assert d["message"] == "401 for flights"
        assert d["status_code"] == 401
        assert d["server_message"] == "Invalid token"

    def test_inheritance(self) -> None:
        assert issubclass(BookingApiUnauthorized, TripPlannerError)


# =============================================================================
# Currency
# =============================================================================
class TestCurrency:
    @pytest.fixture
    def rates(self) -> dict[str, float]:
        return {"INR": 1.0, "USD": 84.0, "AED": 23.0}

    def test_inr_passthrough(self, rates: dict[str, float]) -> None:
        assert to_inr(100, "INR", rates=rates) == 100.0

    def test_usd_to_inr(self, rates: dict[str, float]) -> None:
        assert to_inr(100, "USD", rates=rates) == 8400.0

    def test_aed_to_inr(self, rates: dict[str, float]) -> None:
        assert to_inr(100, "AED", rates=rates) == 2300.0

    def test_decimal_input(self, rates: dict[str, float]) -> None:
        result = to_inr(Decimal("100.50"), "USD", rates=rates)
        assert abs(result - 8442.0) < 0.01

    def test_negative_amount(self, rates: dict[str, float]) -> None:
        assert to_inr(-100, "USD", rates=rates) == -8400.0

    def test_unsupported_currency(self, rates: dict[str, float]) -> None:
        with pytest.raises(InvalidSettingError, match="Unsupported currency"):
            to_inr(100, "XYZ", rates=rates)

    def test_empty_currency(self, rates: dict[str, float]) -> None:
        with pytest.raises(InvalidSettingError):
            to_inr(100, "", rates=rates)

    def test_zero_rate(self) -> None:
        with pytest.raises(InvalidSettingError, match="must be positive"):
            to_inr(100, "USD", rates={"USD": 0.0})

    def test_negative_rate(self) -> None:
        with pytest.raises(InvalidSettingError):
            to_inr(100, "USD", rates={"USD": -5.0})

    def test_case_insensitive(self, rates: dict[str, float]) -> None:
        assert to_inr(100, "usd", rates=rates) == 8400.0


class TestFormatInr:
    @pytest.mark.parametrize(
        ("amount", "expected"),
        [
            (0, "₹0"),
            (None, "₹0"),
            (100, "₹100"),
            (1000, "₹1,000"),
            (10000, "₹10,000"),
            (100000, "₹1,00,000"),
            (1000000, "₹10,00,000"),
            (10000000, "₹1,00,00,000"),
            (12345678, "₹1,23,45,678"),
            (1234.50, "₹1,234.50"),
            (-1234, "-₹1,234"),
            (1234.001, "₹1,234"),  # tiny fractions hidden
        ],
    )
    def test_grouping(self, amount: float | int | None, expected: str) -> None:
        assert format_inr(amount) == expected

    def test_no_symbol(self) -> None:
        assert format_inr(1000, include_symbol=False) == "1,000"


# =============================================================================
# Dates
# =============================================================================
class TestDates:
    def test_dd_mm_yyyy(self) -> None:
        assert to_dd_mm_yyyy("2026-07-19") == "19-07-2026"

    def test_mm_dd_yyyy(self) -> None:
        assert to_mm_dd_yyyy("2026-07-19") == "07-19-2026"

    @pytest.mark.parametrize("bad", ["2026/07/19", "19-07-2026", "", "not-a-date"])
    def test_bad_format(self, bad: str) -> None:
        with pytest.raises(InvalidDateError):
            to_dd_mm_yyyy(bad)

    def test_nights_between(self) -> None:
        assert nights_between("2026-07-19", "2026-07-23") == 4

    def test_nights_same_day(self) -> None:
        with pytest.raises(InvalidDateError):
            nights_between("2026-07-19", "2026-07-19")

    def test_nights_inverted(self) -> None:
        with pytest.raises(InvalidDateError):
            nights_between("2026-07-23", "2026-07-19")

    def test_validate_future(self) -> None:
        ref = date(2026, 5, 25)
        validate_future_date("2026-07-19", today=ref)  # should not raise
        with pytest.raises(InvalidDateError):
            validate_future_date("2026-05-25", today=ref)
        with pytest.raises(InvalidDateError):
            validate_future_date("2026-01-01", today=ref)

    def test_days_until(self) -> None:
        ref = date(2026, 5, 25)
        assert days_until("2026-07-19", today=ref) == 55
        assert days_until("2026-05-25", today=ref) == 0
        assert days_until("2026-05-20", today=ref) == -5


# =============================================================================
# Models
# =============================================================================
class TestModels:
    def test_flight_construction(self) -> None:
        seg = FlightSegment(
            from_airport="DEL",
            to_airport="DXB",
            departure=datetime(2026, 7, 19, 7, 15),
            arrival=datetime(2026, 7, 19, 10, 0),
        )
        flight = FlightOption(
            fare_source_code="FSC1",
            airline="IndiGo",
            price_inr=36107.4,
            price_original=36107.4,
            currency_original="INR",
            segments_outbound=[seg],
        )
        assert flight.airline == "IndiGo"
        assert flight.price_display == "₹36,107.40"

    def test_budget_remaining(self) -> None:
        b = BudgetState(total=100000, spent=30000)
        assert b.remaining == 70000
        assert not b.is_over_budget

    def test_budget_over(self) -> None:
        b = BudgetState(total=100000, spent=120000)
        assert b.remaining == 0
        assert b.is_over_budget
