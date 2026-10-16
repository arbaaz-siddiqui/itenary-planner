"""Live package price, built the way gujjutours.com builds it.

Fixture numbers are the live ones from 2026-10-16 for "Dubai Budget Escape
3 Nights" (id 2), 23-26 Oct, 2 adults, 1 room, where the website showed
Rs 31,455.11 total and Rs 15,727.55 per person.
"""

import json

import pytest

import booking_api.endpoints as endpoints
import mcp_tools.package_pricing as pp
from parsers import parse_package_hotel_rates, parse_package_page

SERVICES = [
    {"dayNo": 1, "servicesId": 53642, "optionId": 222644, "serviceName": "Dubai Airport Pickup by Gujjutours",
     "optionName": "Airport to Zone 1 - 4 Seater", "transferTypeId": 4, "transferType": "Unit",
     "isOptional": False, "transportProvider": "1", "serviceType": "Activity"},
    {"dayNo": 1, "servicesId": 53683, "optionId": 222615, "serviceName": "Dhow Cruise Dubai Marina With Dinner",
     "optionName": "Lower Deck", "transferTypeId": 1, "transferType": "Sharing Transfer",
     "isOptional": False, "transportProvider": "1", "serviceType": "Activity"},
    {"dayNo": 2, "servicesId": 53687, "optionId": 222621, "serviceName": "Dubai City Tour",
     "optionName": "Classic Dubai City Tour", "transferTypeId": 1, "transferType": "Sharing Transfer",
     "isOptional": False, "transportProvider": "1", "serviceType": "Activity"},
    {"dayNo": 3, "servicesId": 53672, "optionId": 222574, "serviceName": "Burj Khalifa Tickets",
     "optionName": "Levels 124 & 125", "transferTypeId": 1, "transferType": "Sharing Transfer",
     "isOptional": True, "transportProvider": "1", "serviceType": "Activity"},
    {"dayNo": 4, "servicesId": 53643, "optionId": 222664, "serviceName": "Dubai Airport Dropoff by Gujjutours",
     "optionName": "Zone 1 to Airport - 7 Seater", "transferTypeId": 4, "transferType": "Unit",
     "isOptional": False, "transportProvider": "1", "serviceType": "Activity"},
]
HOTELS = [
    {"hotelId": 384, "hotelName": "Howard Johnson by Wyndham Bur Dubai", "rating": 3, "noofnight": 3,
     "hotelPriority": 1, "address": "Khalid Bin Waleed Rd Bur Dubai", "hotelFrontImage": "/hotel-images/384/1.webp"},
    {"hotelId": 391, "hotelName": "Citymax Bur Dubai", "rating": 3, "noofnight": 3, "hotelPriority": 2},
]
RATES = {222644: 96.305, 222615: 79.31, 222621: 7.931, 222574: 187.0, 222664: 96.305}


def page_html(services=SERVICES, hotels=HOTELS) -> str:
    """The page shape: escaped JSON text pushed in script chunks."""
    obj = {"package": [{"packageId": 2, "noOfNights": 3, "checkInDate": "2026-10-23T00:00:00"}],
           "itineraryServices": services, "packageHotels": hotels}
    text = "noise" + json.dumps(obj) + "more"
    half = len(text) // 2
    chunks = [json.dumps(text[:half]), json.dumps(text[half:])]
    return "".join(f'<script>self.__next_f.push([1,{c}])</script>' for c in chunks)


def hotel_rs(prices: dict[int, float]) -> dict:
    return {"success": True, "data": [{"AvailabilityRS": {"Currency": "AED", "HotelResult": [
        {"HotelId": hid, "HotelOption": [{"HotelRooms": [[{"Price": p, "RoomTypeName": "Standard Room- Non Refundable",
                                                            "MealName": "Room Only"}]]}]}
        for hid, p in prices.items()]}}]}


@pytest.fixture
def live(monkeypatch):
    import fx

    monkeypatch.setattr(fx, "_roe_pair", lambda *_a, **_k: (0.0391703743, 26.0426410839))
    monkeypatch.setattr(endpoints, "call_package_page", lambda **_: page_html())
    monkeypatch.setattr(endpoints, "call_package_hotels", lambda **_: hotel_rs({391: 700.0, 384: 740.98}))
    monkeypatch.setattr(pp, "_option_meta", lambda *_a: (1, False))
    calls = []

    def rate(**kw):
        calls.append(kw)
        return {"result": [{"rate": RATES[kw["option_id"]], "currencyCode": "AED", "vehicleRates": []}]}

    monkeypatch.setattr(endpoints, "call_tour_option_rate", rate)
    pp._PAGES.clear()
    return calls


WHERE = dict(package_id=2, name="Dubai Budget Escape 3 Nights", country="United Arab Emirates",
             country_id=213, city="Dubai", city_id=244520)


def test_page_parts_are_read_in_day_and_priority_order():
    parts = parse_package_page(page_html())
    assert [s["tour_id"] for s in parts["services"]] == [53642, 53683, 53687, 53672, 53643]
    assert [h["hotel_id"] for h in parts["hotels"]] == [384, 391]
    assert parts["check_in"] == "2026-10-23" and parts["nights"] == 3


def test_page_without_package_data_gives_none():
    assert parse_package_page("<html>nothing</html>") is None


def test_hotel_rates_take_the_cheapest_room_per_hotel():
    rates = parse_package_hotel_rates(hotel_rs({384: 740.98}))
    assert rates[384]["price"] == 740.98 and rates[384]["refundable"] is False


def test_total_equals_the_website(live):
    r = pp.price_package(**WHERE, check_in="2026-10-23", nights=3, adults=2)
    assert r["price_display"] == "₹31,455.11"
    assert r["price_per_person_display"] == "₹15,727.55"
    # The first hotel in priority order, though a cheaper one is available.
    assert r["hotel"]["name"] == "Howard Johnson by Wyndham Bur Dubai"
    # Optional parts are in the total, as on the website.
    assert any(line["name"] == "Burj Khalifa Tickets" and line["optional"] for line in r["lines"])


def test_services_are_asked_as_the_website_asks(live):
    pp.price_package(**WHERE, check_in="2026-10-23", nights=3, adults=2)
    unit_calls = [c for c in live if c["transfer_id"] == 4]
    tour_calls = [c for c in live if c["transfer_id"] != 4]
    assert all(c["units"] == 1 for c in unit_calls)
    assert all(c["adults"] == 1 and c["units"] == 0 for c in tour_calls)
    # Each service on its own day.
    assert {c["option_id"]: c["travel_date"] for c in live}[222574] == "2026-10-25"


def test_a_part_the_supplier_will_not_price_is_left_out_and_named(live, monkeypatch):
    def rate(**kw):
        if kw["option_id"] == 222615:
            return {"result": []}
        return {"result": [{"rate": RATES[kw["option_id"]], "currencyCode": "AED"}]}

    monkeypatch.setattr(endpoints, "call_tour_option_rate", rate)
    r = pp.price_package(**WHERE, check_in="2026-10-23", nights=3, adults=2)
    assert r["unpriced_parts"] == ["Dhow Cruise Dubai Marina With Dinner"]
    assert r["pricing_available"] is True
    assert "not in the total" in r["pricing_note"]


def test_no_hotel_available_means_no_total(live, monkeypatch):
    monkeypatch.setattr(endpoints, "call_package_hotels", lambda **_: hotel_rs({}))
    r = pp.price_package(**WHERE, check_in="2026-10-23", nights=3, adults=2)
    assert r["price_inr"] is None and r["pricing_available"] is False


def test_page_path_is_the_website_url():
    assert pp.package_page_path(**WHERE) == (
        "/en/holidays/united-arab-emirates-213/dubai-244520/dubai-budget-escape-3-nights-2")


def test_rooms_spread_the_party():
    assert pp._rooms(4, [6], 2) == [{"adults": 2, "child_ages": [6]}, {"adults": 2, "child_ages": []}]
    assert pp._rooms(2, [], 5) == [{"adults": 1, "child_ages": []}, {"adults": 1, "child_ages": []}]


# --- Tours sold per vehicle (airport pickup / drop-off) ---------------------

def test_list_rate_type_11_marks_a_tour_as_per_vehicle():
    from parsers import UNIT_RATE_TYPE

    assert UNIT_RATE_TYPE == "11"


def test_per_vehicle_tour_total_is_not_multiplied_by_the_party():
    from core import TourOption
    from mcp_tools.search_tours import _party_total

    o = TourOption(tour_id=53642, name="Dubai Airport Pickup by Gujjutours",
                   price_per_adult_inr=2508.04, sold_per_vehicle=True)
    assert _party_total(o, 2, []) == "₹2,508.04 per vehicle"
    o.sold_per_vehicle = False
    assert _party_total(o, 2, []) == "₹5,016.08 total for 2 adults"


def test_option_with_no_transfer_types_is_priced_as_one_vehicle(monkeypatch):
    import fx
    import mcp_tools.tour_pricing as tp

    monkeypatch.setattr(fx, "_roe_pair", lambda *_a, **_k: (0.0391703743, 26.0426410839))
    calls = []

    def rate(**kw):
        calls.append(kw)
        return {"result": [{"rate": 96.305, "currencyCode": "AED"}]}

    monkeypatch.setattr(tp, "call_tour_option_rate", rate)
    out = tp.party_prices(53642, {"optionId": 222644, "supplierId": 1, "validateTourOption": []},
                          "2026-10-23", adults=2)
    assert out[0]["total_display"] == "₹2,508.04" and out[0]["priced_per"] == "unit"
    assert calls[0]["units"] == 1 and calls[0]["transfer_id"] == 4
