"""Factory functions for tests. Import like:
from tests.factories import make_flight, make_selection
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import (
    BudgetState,
    CancellationTerm,
    DateRange,
    FlightOption,
    FlightSegment,
    GuestInfo,
    HotelOption,
    HotelRoom,
    RestaurantOption,
    Selection,
    TourOption,
    TransferOption,
    TripPhase,
    TripState,
    VisaOption,
)

FIXTURES_DIR = ROOT / "tests" / "fixtures"


def load_fixture(relative_path: str) -> Any:
    path = FIXTURES_DIR / relative_path
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def make_flight_segment(
    *,
    from_airport: str = "DEL",
    to_airport: str = "DXB",
    departure: datetime | str = "2026-07-19T07:15:00",
    arrival: datetime | str = "2026-07-19T10:00:00",
    duration_min: int = 225,
    marketing_airline: str = "IndiGo",
    marketing_airline_code: str = "6E",
    flight_number: str = "1471",
    **overrides: Any,
) -> FlightSegment:
    if isinstance(departure, str):
        departure = datetime.fromisoformat(departure)
    if isinstance(arrival, str):
        arrival = datetime.fromisoformat(arrival)
    base: dict[str, Any] = {
        "from_airport": from_airport,
        "to_airport": to_airport,
        "departure": departure,
        "arrival": arrival,
        "duration_min": duration_min,
        "marketing_airline": marketing_airline,
        "marketing_airline_code": marketing_airline_code,
        "flight_number": flight_number,
    }
    base.update(overrides)
    return FlightSegment(**base)


def make_flight(
    *,
    fare_source_code: str = "FSC_TEST_1",
    airline: str = "IndiGo",
    price_inr: float = 36107.4,
    price_original: float = 36107.4,
    currency_original: str = "INR",
    stops: int = 0,
    refundable: bool = False,
    segments_outbound: list[FlightSegment] | None = None,
    segments_return: list[FlightSegment] | None = None,
    **overrides: Any,
) -> FlightOption:
    if segments_outbound is None:
        segments_outbound = [make_flight_segment()]
    base: dict[str, Any] = {
        "fare_source_code": fare_source_code,
        "airline": airline,
        "price_inr": price_inr,
        "price_original": price_original,
        "currency_original": currency_original,
        "stops": stops,
        "refundable": refundable,
        "segments_outbound": segments_outbound,
        "segments_return": segments_return or [],
    }
    base.update(overrides)
    return FlightOption(**base)


def make_cancellation_term(
    *,
    from_date: str | None = "2026-07-19",
    to_date: str | None = "2026-07-22",
    cancellation_price: float = 0.0,
    is_free_cancellation: bool = True,
) -> CancellationTerm:
    return CancellationTerm(
        from_date=from_date,
        to_date=to_date,
        cancellation_price=cancellation_price,
        currency="USD",
        is_free_cancellation=is_free_cancellation,
    )


def make_hotel_room(
    *,
    room_type_name: str = "Standard Room",
    price_inr: float = 12000.0,
    meal_name: str = "Bed and Breakfast",
    mapped_meal_name: str = "BB",
    **overrides: Any,
) -> HotelRoom:
    base: dict[str, Any] = {
        "room_type_name": room_type_name,
        "price_inr": price_inr,
        "price_original": price_inr / 84,
        "currency_original": "USD",
        "meal_name": meal_name,
        "mapped_meal_name": mapped_meal_name,
        "cancellation_policy": [make_cancellation_term()],
        "supplier_name": "TestSupplier",
    }
    base.update(overrides)
    return HotelRoom(**base)


def make_hotel(
    *,
    hotel_id: int = 206,
    hotel_name: str = "Citymax Bur Dubai",
    area: str = "Bur Dubai",
    price_inr: float = 48000.0,
    per_night_inr: float = 12000.0,
    nights: int = 4,
    stars: float = 3.0,
    rooms: list[HotelRoom] | None = None,
    **overrides: Any,
) -> HotelOption:
    if rooms is None:
        rooms = [make_hotel_room()]
    base: dict[str, Any] = {
        "hotel_id": hotel_id,
        "hotel_name": hotel_name,
        "area": area,
        "price_inr": price_inr,
        "per_night_inr": per_night_inr,
        "nights": nights,
        "stars": stars,
        "rooms": rooms,
        "cheapest_room_type": rooms[0].room_type_name if rooms else "",
        "cheapest_board": rooms[0].mapped_meal_name if rooms else "",
        "has_free_cancellation": (rooms[0].is_free_cancellation if rooms else False),
    }
    base.update(overrides)
    return HotelOption(**base)


def make_tour(
    *,
    tour_id: int = 101,
    name: str = "Desert Safari with BBQ",
    price_per_adult_inr: float = 2300.0,
    rating: float = 4.5,
    is_recommended: bool = True,
    **overrides: Any,
) -> TourOption:
    base: dict[str, Any] = {
        "tour_id": tour_id,
        "name": name,
        "category": "Desert Safari",
        "price_per_adult_inr": price_per_adult_inr,
        "currency_original": "AED",
        "rating": rating,
        "reviews_count": 1200,
        "is_recommended": is_recommended,
        "duration": "6 hours",
    }
    base.update(overrides)
    return TourOption(**base)


def make_transfer(
    *,
    transfer_id: str = "TR_TEST_1",
    vehicle_name: str = "Toyota Camry",
    price_inr: float = 1500.0,
    **overrides: Any,
) -> TransferOption:
    base: dict[str, Any] = {
        "transfer_id": transfer_id,
        "vehicle_name": vehicle_name,
        "vehicle_type": "Sedan",
        "transfer_type": "Private Transfer",
        "capacity": 3,
        "luggage_capacity": 3,
        "price_inr": price_inr,
        "estimated_time": "30 - 40 Minutes",
        "badges": ["Private", "Sedan"],
    }
    base.update(overrides)
    return TransferOption(**base)


def make_restaurant(
    *,
    restaurant_id: int = 555,
    name: str = "Asha's",
    price_per_adult_inr: float = 3500.0,
    **overrides: Any,
) -> RestaurantOption:
    base: dict[str, Any] = {
        "restaurant_id": restaurant_id,
        "name": name,
        "price_per_adult_inr": price_per_adult_inr,
        "cuisine": "Indian",
        "veg_type": "Veg",
        "rating": 4.4,
        "city": "Dubai",
    }
    base.update(overrides)
    return RestaurantOption(**base)


def make_visa(
    *,
    visa_id: int = 1,
    visa_type: str = "UAE 60-day Tourist Visa",
    pricing_available: bool = False,
    price_per_person_inr: float = 0.0,
    **overrides: Any,
) -> VisaOption:
    base: dict[str, Any] = {
        "visa_id": visa_id,
        "visa_type": visa_type,
        "validity": "60 days",
        "stay_duration": "30 days",
        "processing_days": 4,
        "entry_type": "Single",
        "is_evisa": True,
        "price_per_person_inr": price_per_person_inr,
        "pricing_available": pricing_available,
    }
    base.update(overrides)
    return VisaOption(**base)


def make_budget(total: float = 100000.0, spent: float = 0.0) -> BudgetState:
    return BudgetState(total=total, spent=spent)


def make_selection(
    *,
    component: str = "flight",
    item_id: str = "TEST_1",
    title: str = "Test Flight",
    price_inr: float = 30000.0,
) -> Selection:
    return Selection(
        component=component,
        item_id=item_id,
        title=title,
        price_inr=price_inr,
        raw={},
    )


def make_trip_state(
    *,
    origin_city: str = "Delhi",
    destination_city: str = "Dubai",
    check_in: str = "2026-07-19",
    check_out: str = "2026-07-23",
    adults: int = 2,
    children: int = 0,
    budget_total: float = 100000.0,
    phase: TripPhase = TripPhase.INTAKE,
    **overrides: Any,
) -> TripState:
    base: dict[str, Any] = {
        "origin_city": origin_city,
        "destination_city": destination_city,
        "dates": DateRange(check_in=check_in, check_out=check_out, nights=4),
        "guests": GuestInfo(adults=adults, children=children),
        "budget": BudgetState(total=budget_total),
        "phase": phase,
    }
    base.update(overrides)
    return TripState(**base)
