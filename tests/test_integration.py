"""Integration tests for booking_api endpoints using `responses` HTTP mocking.

Verifies:
- Endpoints build the correct payload shape (matching client's Postman collection)
- HTTP error codes map to typed exceptions (401→Unauthorized, 404→NotFound, 5xx→ServerError)
- Date formats are converted correctly per endpoint
"""

from __future__ import annotations

import json

import pytest
import responses

from booking_api import (
    call_flight_details,
    call_flight_search,
    call_hotel_availability,
    call_list_packages,
    call_package_rates,
    call_package_static_data,
    call_restaurant_search,
    call_tour_details,
    call_tour_search,
    call_transfer_search,
    call_visa_info,
)
from booking_api.http_client import get_client
from core import (
    BookingApiNotFound,
    BookingApiServerError,
    BookingApiUnauthorized,
)
from settings import clear_all_caches

BASE_URL = "https://stagingapi.gujjutours.com"


@pytest.fixture(autouse=True)
def _setup_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOKING_BASE_URL", BASE_URL)
    monkeypatch.setenv("BOOKING_TOKEN", "test-token-12345")
    monkeypatch.setenv("BOOKING_TENANT_ID", "test-account-tenant")
    monkeypatch.setenv("FLIGHT_LIST_TENANT_ID", "test-flight-list")
    monkeypatch.setenv("FLIGHT_SEARCH_TENANT_ID", "test-flight-search")
    monkeypatch.setenv("HTTP_TIMEOUT_SECS", "5")
    monkeypatch.setenv("HTTP_MAX_RETRIES", "0")
    clear_all_caches()
    get_client.cache_clear()


# =============================================================================
# Error-code → typed-exception mapping
# =============================================================================
@responses.activate
def test_flight_search_401_raises_unauthorized() -> None:
    responses.post(
        f"{BASE_URL}/api/Flight/search",
        json={"message": "Invalid token"},
        status=401,
    )
    with pytest.raises(BookingApiUnauthorized) as exc_info:
        call_flight_search(
            origin_iata="DEL",
            destination_iata="DXB",
            departure_date="2026-07-19",
        )
    assert exc_info.value.fields["status_code"] == 401


@responses.activate
def test_hotel_search_404_raises_not_found() -> None:
    responses.post(
        f"{BASE_URL}/api/xconnect/Availabilitywithcancellation",
        json={"message": "No record found"},
        status=404,
    )
    with pytest.raises(BookingApiNotFound):
        call_hotel_availability(
            hotel_ids=[206],
            city_id=244520,
            check_in="2026-07-19",
            check_out="2026-07-23",
        )


@responses.activate
def test_500_raises_server_error() -> None:
    responses.post(
        f"{BASE_URL}/api/Flight/search",
        json={"message": "Server error"},
        status=500,
    )
    with pytest.raises(BookingApiServerError):
        call_flight_search(
            origin_iata="DEL",
            destination_iata="DXB",
            departure_date="2026-07-19",
        )


# =============================================================================
# Success-path payload shape verification
# =============================================================================
@responses.activate
def test_flight_search_payload_matches_postman() -> None:
    """FlightSearch payload should match the client's Postman sample exactly."""
    responses.post(
        f"{BASE_URL}/api/Flight/search",
        json={"data": {"pricedItineraries": []}},
        status=200,
    )
    call_flight_search(
        origin_iata="DEL",
        destination_iata="BOM",
        departure_date="2026-07-11",
        adults=1,
        max_stops=1,
        cabin="Y",
    )
    sent = json.loads(responses.calls[0].request.body)

    # Per client's Postman sample:
    assert sent["TravelPreferences"]["AirTripType"] == "oneway"
    assert sent["TravelPreferences"]["MaxStopsQuantity"] == "1"  # string
    assert sent["TravelPreferences"]["CabinPreference"] == "Y"
    assert sent["OriginDestinationInformations"][0]["DepartureDateTime"] == "11-07-2026"
    assert sent["OriginDestinationInformations"][0]["OriginLocationCode"] == "DEL"
    assert sent["OriginDestinationInformations"][0]["DestinationLocationCode"] == "BOM"
    assert sent["PassengerTypeQuantities"] == [{"Code": "ADT", "Quantity": 1}]
    assert sent["PricingSourceType"] == "all"
    assert sent["Target"] == "test"
    assert sent["supplierTime"] == "6"


@responses.activate
def test_flight_search_return_trip() -> None:
    responses.post(
        f"{BASE_URL}/api/Flight/search",
        json={"data": {"pricedItineraries": []}},
        status=200,
    )
    call_flight_search(
        origin_iata="DEL",
        destination_iata="DXB",
        departure_date="2026-07-19",
        return_date="2026-07-23",
        adults=2,
        children=1,
        child_ages=[5],
    )
    sent = json.loads(responses.calls[0].request.body)
    assert sent["TravelPreferences"]["AirTripType"] == "return"
    assert len(sent["OriginDestinationInformations"]) == 2
    assert sent["OriginDestinationInformations"][0]["DepartureDateTime"] == "19-07-2026"
    assert sent["OriginDestinationInformations"][1]["DepartureDateTime"] == "23-07-2026"
    assert sent["childAge"] == "5"


@responses.activate
def test_flight_search_sends_correct_tenant_header() -> None:
    responses.post(
        f"{BASE_URL}/api/Flight/search",
        json={"data": {"pricedItineraries": []}},
        status=200,
    )
    call_flight_search(
        origin_iata="DEL",
        destination_iata="DXB",
        departure_date="2026-07-19",
    )
    headers = responses.calls[0].request.headers
    assert headers["X-Tenant-Id"] == "test-flight-search"
    assert "X-Trace-Id" in headers
    assert headers["X-Site-Type"] == "B2B"


@responses.activate
def test_flight_details_uses_list_tenant() -> None:
    """getflightdetails should use the LIST tenant + custom host, not search tenant."""
    responses.post(
        f"{BASE_URL}/api/Flight/getflightdetails",
        json={"data": "..."},
        status=200,
    )
    call_flight_details(fare_source_code="FSC123")
    headers = responses.calls[0].request.headers
    assert headers["X-Tenant-Id"] == "test-flight-list"
    assert headers["X-Custom-Host"] == "newinstance.activitylinker.com"


@responses.activate
def test_hotel_payload_matches_postman() -> None:
    """HotelSearch payload should match the client's literal shape."""
    responses.post(
        f"{BASE_URL}/api/xconnect/Availabilitywithcancellation",
        json={"AvailabilityRS": {"HotelResult": []}},
        status=200,
    )
    call_hotel_availability(
        hotel_ids=[509, 206],
        city_id=244520,
        check_in="2026-11-20",
        check_out="2026-11-22",
        adults=2,
        children=0,
        nationality="India",
        currency="AED",
    )
    sent = json.loads(responses.calls[0].request.body)

    # Per Postman sample:
    assert sent["Token"] == ""
    assert sent["IsMobile"] == 1  # int
    assert sent["IsSearch"] == 1  # int
    assert sent["Request"]["CityID"] == "244520"  # STRING
    assert sent["Request"]["CheckInDate"] == "11-20-2026"
    assert sent["Request"]["CheckOutDate"] == "11-22-2026"
    assert sent["Request"]["NoofNights"] == "2"  # STRING
    assert sent["Request"]["Nationality"] == "India"  # STRING
    assert sent["Request"]["Rooms"][0]["NoofAdults"] == 2
    assert sent["Request"]["Rooms"][0]["NoOfChild"] == 0
    assert sent["Request"]["Rooms"][0]["ChildAge"] == []
    assert sent["Request"]["Filters"]["HotelIDs"] == "509,206"  # STRING
    assert sent["Request"]["Filters"]["StarRating"]["Min"] == 1
    assert sent["Request"]["Filters"]["StarRating"]["Max"] == 5
    assert sent["AdvancedOptions"]["Currency"] == "AED"


@responses.activate
def test_restaurant_payload_uses_dd_mm_yyyy() -> None:
    responses.post(
        f"{BASE_URL}/api/restaurant/v1/restaurants",
        json={"result": {"list": []}},
        status=200,
    )
    call_restaurant_search(
        city_id=244520,
        search_date="2026-06-06",
        adults=1,
        children=0,
    )
    sent = json.loads(responses.calls[0].request.body)
    assert sent["cityid"] == 244520
    assert sent["GuestInfo"]["Adults"] == 1
    assert sent["GuestInfo"]["Children"] == 0
    assert sent["SearchDate"] == "06-06-2026"  # dd-mm-yyyy


@responses.activate
def test_visa_payload_uses_lowercase_guest_info() -> None:
    responses.post(
        f"{BASE_URL}/api/visa/v1/visas",
        json={"result": {"visaOptions": []}},
        status=200,
    )
    call_visa_info(
        country_id=213,
        nationality_id=213,
        travel_date="2026-10-10",
        visa_type_id=1,
        adults=1,
        children=1,
    )
    sent = json.loads(responses.calls[0].request.body)
    assert sent["countryId"] == 213
    assert sent["nationalityId"] == 213
    assert sent["citizenId"] == 213  # defaults to country_id
    assert sent["visaTypeId"] == 1
    assert sent["checkInDate"] == "10-10-2026"
    assert sent["guestInfo"]["adults"] == 1  # lowercase
    assert sent["guestInfo"]["children"] == 1  # lowercase
    assert sent["agentMarkupType"] == 0  # int


@responses.activate
def test_tour_search_payload() -> None:
    responses.post(
        f"{BASE_URL}/api/v1/tourservices/TourSearch/toursearchlist",
        json={"result": {"tourStaticlists": []}},
        status=200,
    )
    call_tour_search(
        country_id=213,
        city_id=244520,
        travel_date="2026-05-20",
        tour_category_id=1,
    )
    sent = json.loads(responses.calls[0].request.body)
    assert sent["countryId"] == 213
    assert sent["cityID"] == 244520
    assert sent["tourCategoryId"] == 1
    assert sent["tourIDs"] == ""
    assert sent["travelDate"] == "2026-05-20"


@responses.activate
def test_tour_details_uses_get_with_query_param() -> None:
    responses.get(
        f"{BASE_URL}/api/v1/tourservices/TourSearch/Tourdetails",
        json={"result": "..."},
        status=200,
    )
    call_tour_details(tour_id=1347)
    # GET with TourId=1347 in query string
    assert "TourId=1347" in responses.calls[0].request.url


@responses.activate
def test_transfer_search_uses_a_o_codes() -> None:
    responses.post(
        f"{BASE_URL}/api/transferservices/TransferList",
        json={"result": []},
        status=200,
    )
    call_transfer_search(
        from_lat=25.2515,
        from_lng=55.3683,
        to_lat=25.2145,
        to_lng=55.3032,
        from_place_id="ChIJaQ4...",
        to_place_id="ChIJB1z...",
        departure_date="2026-06-25",
        return_date="2026-06-27",
        is_round_trip=False,
        from_type="A",
        to_type="O",
        adults=1,
    )
    sent = json.loads(responses.calls[0].request.body)
    assert sent["fromType"] == "A"  # single letter
    assert sent["toType"] == "O"
    assert sent["isRoundTrip"] == 0  # int
    assert sent["TransferRateTypes"] == [
        {"TransferRateTypeId": 1, "Count": 1, "transferRateTypeName": "Adult"}
    ]
    assert sent["agtMkpType"] == 0


@responses.activate
def test_package_list_payload_has_rooms_and_dates() -> None:
    responses.post(
        f"{BASE_URL}/api/staticpackageservices/staticpackage/packagelist",
        json={"result": []},
        status=200,
    )
    call_list_packages(
        country_id=213,
        city_id=0,
        check_in="2026-06-19",
        check_out="2026-06-19",
        nights=0,
        adults=2,
        children=0,
    )
    sent = json.loads(responses.calls[0].request.body)
    assert sent["countryId"] == "213"  # STRING
    assert sent["cityID"] == 0
    assert sent["checkInDate"] == "2026-06-19"
    assert sent["nationality"] == "India"
    assert sent["rooms"][0]["roomNo"] == 1
    assert sent["rooms"][0]["noofAdults"] == 2
    assert sent["rooms"][0]["noofChild"] == 0


@responses.activate
def test_package_rates_takes_one_package_id() -> None:
    responses.post(
        f"{BASE_URL}/api/staticpackageservices/staticpackage/packagerate",
        json={"result": []},
        status=200,
    )
    call_package_rates(package_id=2, adults=2, children=0)
    sent = json.loads(responses.calls[0].request.body)
    assert sent["packageId"] == 2


@responses.activate
def test_package_static_data_uses_get() -> None:
    responses.get(
        f"{BASE_URL}/api/staticpackageservices/staticpackage/packagestaticdata",
        json={"result": "..."},
        status=200,
    )
    call_package_static_data(package_id=1)
    assert "PackageId=1" in responses.calls[0].request.url
