"""All booking API endpoint functions.

20 endpoints total:
  Search/List (9):
    - call_flight_search
    - call_hotel_availability
    - call_tour_search
    - call_tour_rates
    - call_transfer_search
    - call_restaurant_search
    - call_visa_info
    - call_list_packages
    - call_package_rates
  Detail (5):
    - call_flight_details
    - call_tour_details
    - call_transfer_details
    - call_restaurant_details
    - call_package_static_data
  Hotel static content (6) — separate Hotels-only token:
    - call_hotel_cities
    - call_hotel_static_by_city
    - call_hotel_static_data
    - call_hotel_property_info
    - call_hotel_descriptions
    - call_hotel_guest_review

Each function:
- Builds payload matching the client's Postman collection EXACTLY
- Calls the HTTP client with the right header set
- Wraps unexpected errors in the appropriate *Failed subclass
- Lets typed BookingApiError subclasses (Unauthorized/NotFound/etc) bubble through
- Returns raw JSON (parsing happens in `parsers.py`)
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from booking_api.headers import (
    base_headers,
    currency_roe_headers,
    flight_list_headers,
    flight_search_headers,
    hotel_static_headers,
)
from booking_api.http_client import get_b2c_client, get_client
from core import (
    BookingApiError,
    CurrencyRoeFailed,
    FlightDetailsFailed,
    FlightSearchFailed,
    HotelSearchFailed,
    HotelStaticDataFailed,
    PackageDetailsFailed,
    PackageSearchFailed,
    RestaurantDetailsFailed,
    RestaurantSearchFailed,
    TourDetailsFailed,
    TourSearchFailed,
    TransferDetailsFailed,
    TransferSearchFailed,
    VisaInfoFailed,
    to_dd_mm_yyyy,
    to_mm_dd_yyyy,
)

# --- Paths ---
FLIGHT_SEARCH_PATH = "/api/Flight/search"
FLIGHT_DETAILS_PATH = "/api/Flight/getflightdetails"
HOTEL_AVAILABILITY_PATH = "/api/xconnect/Availabilitywithcancellation"
TOUR_LIST_PATH = "/api/v1/tourservices/TourSearch/toursearchlist"
TOUR_RATE_PATH = "/api/v1/tourservices/TourSearch/toursearchlistrate"
TOUR_DETAILS_PATH = "/api/v1/tourservices/TourSearch/Tourdetails"
TOUR_TIMESLOT_PATH = "/api/v1/tourservices/TourSearch/Timeslot"
# B2C host (stagingb2c) — see get_b2c_client().
TOUR_OPTIONS_PATH = "/api/tours/options"
TOUR_PRICE_CALENDAR_PATH = "/api/tours/tour-price-check-calender"  # supplier's spelling
TOUR_OPTION_DETAILS_PATH = "/api/tours/option-details"
TRANSFER_LIST_PATH = "/api/transferservices/TransferList"
TRANSFER_DETAILS_PATH = "/api/transferservices/TransferDetail"
RESTAURANT_LIST_PATH = "/api/restaurant/v1/restaurants"
RESTAURANT_DETAILS_PATH_TPL = "/api/restaurant/v1/restaurants/{id}"
VISA_LIST_PATH = "/api/visa/v1/visas"
PACKAGE_LIST_PATH = "/api/staticpackageservices/staticpackage/packagelist"
PACKAGE_RATE_PATH = "/api/staticpackageservices/staticpackage/packagerate"
PACKAGE_STATIC_DATA_PATH = "/api/staticpackageservices/staticpackage/packagestaticdata"
# Hotel static-content endpoints (separate Hotels-only token; see headers.py).
HOTEL_CITIES_PATH = "/api/xconnect/GetCitiesWithHotel"
HOTEL_STATIC_BY_CITY_PATH = "/api/xconnect/GetStaticDataByCity"
HOTEL_STATIC_OPTIMIZE_PATH = "/api/xconnect/GetHotelStaticDataOptimize"
HOTEL_STATIC_LIST_ADDRESS_PATH = "/api/xconnect/gethotelstaticdatalistsuboptimize_v1_Address"
HOTEL_DESCRIPTIONS_PATH = "/api/xconnect/GetPropertyDescriptions"
HOTEL_GUEST_REVIEW_PATH = "/api/xconnect/GetHotelGuestReview"
CURRENCY_ROE_PATH_TPL = "/api/Currency/ROE/{code}"


# =============================================================================
# Flights
# =============================================================================
def call_flight_search(
    *,
    origin_iata: str,
    destination_iata: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    children: int = 0,
    child_ages: list[int] | None = None,
    cabin: str = "Y",
    max_stops: int = 1,
) -> dict[str, Any]:
    """Search flights. Payload mirrors client's FlightSearch sample exactly."""
    od_infos: list[dict[str, Any]] = [
        {
            "DepartureDateTime": to_dd_mm_yyyy(departure_date),
            "OriginLocationCode": origin_iata.upper(),
            "DestinationLocationCode": destination_iata.upper(),
        }
    ]
    trip_type = "oneway"
    if return_date:
        od_infos.append(
            {
                "DepartureDateTime": to_dd_mm_yyyy(return_date),
                "OriginLocationCode": destination_iata.upper(),
                "DestinationLocationCode": origin_iata.upper(),
            }
        )
        trip_type = "return"

    pax_quantities: list[dict[str, Any]] = [{"Code": "ADT", "Quantity": adults}]
    if children > 0:
        pax_quantities.append({"Code": "CHD", "Quantity": children})

    payload: dict[str, Any] = {
        "OriginDestinationInformations": od_infos,
        "TravelPreferences": {
            "MaxStopsQuantity": str(max_stops),
            "CabinPreference": cabin,
            "AirTripType": trip_type,
        },
        "PricingSourceType": "all",
        "PassengerTypeQuantities": pax_quantities,
        "childAge": ",".join(str(a) for a in (child_ages or [])),
        "infantAge": "",
        "Target": "test",
        "agentID": 0,
        "rateCategoryId": 0,
        "supplierTime": "6",
        "supplierId": 0,
        "suppliers": [],
        "isMobile": 0,
        "AirlineName": "",
    }
    try:
        return get_client().post(FLIGHT_SEARCH_PATH, json=payload, headers=flight_search_headers())
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise FlightSearchFailed(
            f"Flight search call failed: {e}", endpoint=FLIGHT_SEARCH_PATH
        ) from e


def call_flight_details(
    *,
    fare_source_code: str,
    itinerary_source_code: str | None = None,
    conversation_id: str = "",
    target: str = "test",
    guest_user_id: int = 0,
) -> dict[str, Any]:
    """Get full details for a specific flight option (from search result)."""
    payload: dict[str, Any] = {
        "serviceName": "flightdetails",
        "itinerarySourceCode": itinerary_source_code or fare_source_code,
        "fareSourceCode": fare_source_code,
        "conversationId": conversation_id,
        "target": target,
        "guestUserId": guest_user_id,
    }
    try:
        return get_client().post(FLIGHT_DETAILS_PATH, json=payload, headers=flight_list_headers())
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise FlightDetailsFailed(
            f"Flight details call failed: {e}", endpoint=FLIGHT_DETAILS_PATH
        ) from e


# =============================================================================
# Hotels
# =============================================================================
def _build_hotel_rooms(
    *,
    rooms: list[dict[str, Any]] | None,
    adults: int,
    children: int,
    child_ages: list[int] | None,
) -> list[dict[str, Any]]:
    """Build the supplier's Rooms[] array.

    Accepts a structured `rooms` list (per-room occupancy) and normalizes each
    entry to {RoomNo, NoofAdults, NoOfChild, ChildAge}. Tolerates either
    snake_case keys (adults/children/child_ages) or the supplier's own keys.
    Falls back to a single room from the flat args when `rooms` is empty.
    """
    if not rooms:
        return [
            {
                "RoomNo": 1,
                "NoofAdults": adults,
                "NoOfChild": children,
                "ChildAge": list(child_ages or []),
            }
        ]

    api_rooms: list[dict[str, Any]] = []
    for idx, r in enumerate(rooms, start=1):
        r = r or {}
        room_adults = int(r.get("adults", r.get("NoofAdults", r.get("noofAdults", 2))) or 0)
        ages_raw = r.get("child_ages", r.get("ChildAge", r.get("childAge", []))) or []
        ages = [int(a) for a in ages_raw]
        # children count: explicit value wins, else infer from the ages list
        room_children = int(r.get("children", r.get("NoOfChild", r.get("noOfChild", len(ages)))) or 0)
        api_rooms.append(
            {
                "RoomNo": idx,
                "NoofAdults": room_adults,
                "NoOfChild": room_children,
                "ChildAge": ages,
            }
        )
    return api_rooms


def call_hotel_availability(
    *,
    hotel_ids: list[int],
    city_id: int,
    check_in: str,
    check_out: str,
    rooms: list[dict[str, Any]] | None = None,
    adults: int = 2,
    children: int = 0,
    child_ages: list[int] | None = None,
    nationality: str = "India",
    currency: str = "AED",
    star_min: int = 1,
    star_max: int = 5,
) -> dict[str, Any]:
    """Search hotels. Payload mirrors client's HotelSearch sample exactly:
    - CityID as string
    - HotelIDs as comma-separated string
    - Nationality as country name (not country_id)
    - IsMobile/IsSearch as int
    - mm-dd-yyyy dates

    Occupancy:
    - Pass a structured `rooms` list to book multiple rooms with different
      per-room occupancy, e.g.
        [{"adults": 2, "children": 1, "child_ages": [5]}, {"adults": 2}]
      Each entry maps to the supplier's Rooms[] shape (RoomNo / NoofAdults /
      NoOfChild / ChildAge). RoomNo is assigned automatically (1-based).
    - If `rooms` is omitted, falls back to a single room built from the flat
      `adults` / `children` / `child_ages` args (backward compatible).
    """
    from core import nights_between

    nights = nights_between(check_in, check_out)
    hotel_ids_str = ",".join(str(hid) for hid in hotel_ids)
    api_rooms = _build_hotel_rooms(
        rooms=rooms, adults=adults, children=children, child_ages=child_ages
    )
    payload: dict[str, Any] = {
        "Token": "",
        "Request": {
            "Rooms": api_rooms,
            "CityID": str(city_id),
            "CheckInDate": to_mm_dd_yyyy(check_in),
            "CheckOutDate": to_mm_dd_yyyy(check_out),
            "NoofNights": str(nights),
            "Nationality": nationality,
            "Filters": {
                "IsRecommendedOnly": "0",
                "IsShowRooms": "1",
                "IsOnlyAvailable": "1",
                "StarRating": {"Min": star_min, "Max": star_max},
                "HotelIDs": hotel_ids_str,
            },
        },
        "AdvancedOptions": {
            "Currency": currency,
            "CustomerIpAddress": "111",
            "HotelName": "",
        },
        "IsMobile": 1,
        "IsSearch": 1,
    }
    try:
        return get_client().post(HOTEL_AVAILABILITY_PATH, json=payload, headers=base_headers())
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise HotelSearchFailed(
            f"Hotel availability call failed: {e}", endpoint=HOTEL_AVAILABILITY_PATH
        ) from e


# =============================================================================
# Tours (list + rate + details)
# =============================================================================
def _tour_payload(
    *,
    country_id: int,
    city_id: int,
    travel_date: str,
    tour_category_id: int = 1,
    transfer_type_id: int = 0,
    tour_ids: str = "",
) -> dict[str, Any]:
    """Shared payload shape for tour list/rate/details."""
    return {
        "countryId": country_id,
        "cityID": city_id,
        "tourCategoryId": tour_category_id,
        "transferTypeID": transfer_type_id,
        "tourIDs": tour_ids,
        "travelDate": travel_date,
    }


def call_tour_search(
    *,
    country_id: int,
    city_id: int,
    travel_date: str,
    tour_category_id: int = 1,
) -> dict[str, Any]:
    """List available tours."""
    try:
        return get_client().post(
            TOUR_LIST_PATH,
            json=_tour_payload(
                country_id=country_id,
                city_id=city_id,
                travel_date=travel_date,
                tour_category_id=tour_category_id,
            ),
            headers=base_headers(),
        )
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise TourSearchFailed(f"Tour search list call failed: {e}", endpoint=TOUR_LIST_PATH) from e


def call_tour_rates(
    *,
    country_id: int,
    city_id: int,
    travel_date: str,
    tour_category_id: int = 1,
) -> dict[str, Any]:
    """Rates for the same tour list (call after tour_search)."""
    try:
        return get_client().post(
            TOUR_RATE_PATH,
            json=_tour_payload(
                country_id=country_id,
                city_id=city_id,
                travel_date=travel_date,
                tour_category_id=tour_category_id,
            ),
            headers=base_headers(),
        )
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise TourSearchFailed(f"Tour rate call failed: {e}", endpoint=TOUR_RATE_PATH) from e


def call_tour_details(*, tour_id: int) -> dict[str, Any]:
    """Get full details for a specific tour. GET with query param."""
    try:
        return get_client().get(
            TOUR_DETAILS_PATH,
            params={"TourId": tour_id},
            headers=base_headers(),
        )
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise TourDetailsFailed(f"Tour details call failed: {e}", endpoint=TOUR_DETAILS_PATH) from e


# =============================================================================
# Tour booking-flow endpoints (time slots, options, price calendar, option detail)
# =============================================================================
# These drill into a chosen tour: TourList/Rate gives tourId + supplierId, then
# options -> time slots / price calendar / option details. Three of them live on
# the B2C host (get_b2c_client); TourTimeSlot is on the main B2B host.


def call_tour_options(*, tour_id: int, travel_date: str, lang: str = "en") -> dict[str, Any]:
    """Available options/variants for a tour (B2C). Returns tourOptionId/ratePlanId."""
    payload: dict[str, Any] = {"tourID": tour_id, "travelDate": travel_date, "lang": lang}
    try:
        return get_b2c_client().post(TOUR_OPTIONS_PATH, json=payload, headers=base_headers())
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise TourDetailsFailed(f"Tour options call failed: {e}", endpoint=TOUR_OPTIONS_PATH) from e


def call_tour_timeslots(
    *,
    tour_id: int,
    tour_option_id: str,
    travel_date: str,
    supplier_id: int,
    transfer_id: int = 0,
    adults: int = 1,
    adult_age: int = 30,
    lang: str = "en",
) -> dict[str, Any]:
    """Available time slots for a tour option on a date. Returns timeslotId(s)."""
    payload: dict[str, Any] = {
        "tourId": tour_id,
        "transferId": transfer_id,
        "tourOptionId": str(tour_option_id),
        "travelDate": travel_date,
        "supplierId": supplier_id,
        "paxDetails": [{"Label": "Adult", "Value": str(adults), "Age": str(adult_age)}],
        "lang": lang,
    }
    try:
        return get_client().post(TOUR_TIMESLOT_PATH, json=payload, headers=base_headers())
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise TourDetailsFailed(
            f"Tour timeslot call failed: {e}", endpoint=TOUR_TIMESLOT_PATH
        ) from e


def call_tour_price_calendar(
    *,
    tour_id: int,
    tour_option_id: int,
    start_month: int,
    end_month: int,
    rate_plan_id: int = 0,
    timeslot_id: int = 0,
    lang: str = "en",
) -> dict[str, Any]:
    """Price-availability calendar for a tour option across a month range (B2C)."""
    payload: dict[str, Any] = {
        "tourId": tour_id,
        "tourOptionId": tour_option_id,
        "ratePlanId": rate_plan_id,
        "timeslotId": timeslot_id,
        "startMonth": start_month,
        "endMonth": end_month,
        "lang": lang,
    }
    try:
        return get_b2c_client().post(
            TOUR_PRICE_CALENDAR_PATH, json=payload, headers=base_headers()
        )
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise TourDetailsFailed(
            f"Tour price-calendar call failed: {e}", endpoint=TOUR_PRICE_CALENDAR_PATH
        ) from e


def call_tour_option_details(
    *, tour_id: int, tour_option_id: str, supplier_id: int, lang: str = "en"
) -> dict[str, Any]:
    """Full detail for a tour option: pricing, inclusions, cancellation (B2C).

    The collection sends NO auth header for this endpoint, but passing the Bearer
    token (as get_b2c_client does) is harmless and consistent.
    """
    payload: dict[str, Any] = {
        "tourId": tour_id,
        "tourOptionId": str(tour_option_id),
        "supplierId": supplier_id,
        "lang": lang,
    }
    try:
        return get_b2c_client().post(
            TOUR_OPTION_DETAILS_PATH, json=payload, headers=base_headers()
        )
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise TourDetailsFailed(
            f"Tour option-details call failed: {e}", endpoint=TOUR_OPTION_DETAILS_PATH
        ) from e


# =============================================================================
# Transfers (list + details)
# =============================================================================
# fromType / toType single-letter codes per client's Postman:
#   A = Airport, O = Other (hotel/general location). Use these.
TRANSFER_TYPE_AIRPORT = "A"
TRANSFER_TYPE_OTHER = "O"


def _transfer_payload(
    *,
    from_lat: float,
    from_lng: float,
    to_lat: float,
    to_lng: float,
    from_place_id: str,
    to_place_id: str,
    departure_date: str,
    departure_time: str = "07:00:00",
    return_date: str | None = None,
    return_time: str = "07:00:00",
    is_round_trip: bool = False,
    from_type: str = TRANSFER_TYPE_AIRPORT,
    to_type: str = TRANSFER_TYPE_OTHER,
    from_location_name: str = "",
    to_location_name: str = "",
    adults: int = 1,
    unique_key: str | None = None,
) -> dict[str, Any]:
    # Payload mirrors the N8N-Technoheven V1 collection's TransferList/Detail
    # bodies EXACTLY: the supplier expects CAPITALIZED DepartureDate/ReturnDate/
    # IsRoundTrip and the from/to location-name fields. The earlier lowercase
    # keys (departureDate/returnDate) + agtMkp/agtMkpType were why transfer
    # search failed; the new collection dropped agtMkp* and capitalized the dates.
    #
    # The API still rejects an empty ReturnDate even for one-way searches, so
    # fall back to the departure date; IsRoundTrip=0 keeps it one-way.
    effective_return_date = return_date or departure_date
    payload: dict[str, Any] = {
        "fromLongitude": from_lng,
        "fromLatitude": from_lat,
        "toLongitude": to_lng,
        "toLatitude": to_lat,
        "DepartureDate": departure_date,
        "departureTime": departure_time,
        "ReturnDate": effective_return_date,
        "returnTime": return_time,
        "isRoundTrip": 1 if is_round_trip else 0,
        "fromLocationName": from_location_name,
        "toLocationName": to_location_name,
        "fromType": from_type,
        "toType": to_type,
        "fromPlaceId": from_place_id,
        "toPlaceId": to_place_id,
        "TransferRateTypes": [
            {
                "TransferRateTypeId": 1,
                "Count": adults,
                "transferRateTypeName": "Adult",
            }
        ],
    }
    if unique_key is not None:
        payload["uniqueKey"] = unique_key
    return payload


def call_transfer_search(
    *,
    from_lat: float,
    from_lng: float,
    to_lat: float,
    to_lng: float,
    from_place_id: str,
    to_place_id: str,
    departure_date: str,
    departure_time: str = "07:00:00",
    return_date: str | None = None,
    return_time: str = "07:00:00",
    is_round_trip: bool = False,
    from_type: str = TRANSFER_TYPE_AIRPORT,
    to_type: str = TRANSFER_TYPE_OTHER,
    from_location_name: str = "",
    to_location_name: str = "",
    adults: int = 1,
) -> dict[str, Any]:
    """List available transfers between two points."""
    try:
        return get_client().post(
            TRANSFER_LIST_PATH,
            json=_transfer_payload(
                from_lat=from_lat,
                from_lng=from_lng,
                to_lat=to_lat,
                to_lng=to_lng,
                from_place_id=from_place_id,
                to_place_id=to_place_id,
                departure_date=departure_date,
                departure_time=departure_time,
                return_date=return_date,
                return_time=return_time,
                is_round_trip=is_round_trip,
                from_type=from_type,
                to_type=to_type,
                from_location_name=from_location_name,
                to_location_name=to_location_name,
                adults=adults,
            ),
            headers=base_headers(),
        )
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise TransferSearchFailed(
            f"Transfer search call failed: {e}", endpoint=TRANSFER_LIST_PATH
        ) from e


def call_transfer_details(
    *,
    from_lat: float,
    from_lng: float,
    to_lat: float,
    to_lng: float,
    from_place_id: str,
    to_place_id: str,
    departure_date: str,
    unique_key: str,
    departure_time: str = "07:00:00",
    return_date: str | None = None,
    return_time: str = "07:00:00",
    is_round_trip: bool = False,
    from_type: str = TRANSFER_TYPE_AIRPORT,
    to_type: str = TRANSFER_TYPE_OTHER,
    from_location_name: str = "",
    to_location_name: str = "",
    adults: int = 1,
) -> dict[str, Any]:
    """Get details for a specific transfer. uniqueKey comes from TransferList result."""
    try:
        return get_client().post(
            TRANSFER_DETAILS_PATH,
            json=_transfer_payload(
                from_lat=from_lat,
                from_lng=from_lng,
                to_lat=to_lat,
                to_lng=to_lng,
                from_place_id=from_place_id,
                to_place_id=to_place_id,
                departure_date=departure_date,
                departure_time=departure_time,
                return_date=return_date,
                return_time=return_time,
                is_round_trip=is_round_trip,
                from_type=from_type,
                to_type=to_type,
                from_location_name=from_location_name,
                to_location_name=to_location_name,
                adults=adults,
                unique_key=unique_key,
            ),
            headers=base_headers(),
        )
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise TransferDetailsFailed(
            f"Transfer details call failed: {e}", endpoint=TRANSFER_DETAILS_PATH
        ) from e


# =============================================================================
# Restaurants (list + details)
# =============================================================================
def call_restaurant_search(
    *, city_id: int, search_date: str, adults: int = 1, children: int = 0
) -> dict[str, Any]:
    """List restaurants for a city + date.

    SearchDate format: MM-DD-YYYY. The staging API validates this strictly
    ("SearchDate is required, must be in MM-DD-YYYY format ...") — the Postman
    sample '06-06-2026' only worked because day==month made it ambiguous.
    Caller passes ISO yyyy-mm-dd; we convert.
    """
    payload: dict[str, Any] = {
        "cityid": city_id,
        "GuestInfo": {"Adults": adults, "Children": children},
        "SearchDate": to_mm_dd_yyyy(search_date),
    }
    try:
        return get_client().post(RESTAURANT_LIST_PATH, json=payload, headers=base_headers())
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise RestaurantSearchFailed(
            f"Restaurant search call failed: {e}", endpoint=RESTAURANT_LIST_PATH
        ) from e


def call_restaurant_details(
    *, restaurant_id: int, city_id: int, search_date: str, adults: int = 1, children: int = 0
) -> dict[str, Any]:
    """Get details for a specific restaurant. ID is in the URL path."""
    path = RESTAURANT_DETAILS_PATH_TPL.format(id=restaurant_id)
    payload: dict[str, Any] = {
        "cityid": city_id,
        "GuestInfo": {"Adults": adults, "Children": children},
        "SearchDate": to_mm_dd_yyyy(search_date),
    }
    try:
        return get_client().post(path, json=payload, headers=base_headers())
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise RestaurantDetailsFailed(f"Restaurant details call failed: {e}", endpoint=path) from e


# =============================================================================
# Visa
# =============================================================================
def call_visa_info(
    *,
    country_id: int,
    nationality_id: int,
    travel_date: str,
    visa_type_id: int = 1,
    citizen_id: int = 0,
    adults: int = 1,
    children: int = 0,
    url_path: str = "",
) -> dict[str, Any]:
    """Visa list/details. Payload mirrors client's VisaList/Details sample:
    - guestInfo uses lowercase 'adults' / 'children'
    - agentMarkupType is int 0
    - checkInDate is MM-DD-YYYY: the API validates strictly ("CheckInDate ...
      must be in MM-DD-YYYY format ..."); the Postman sample '10-10-2026' only
      passed because day==month made it ambiguous.
    """
    if not citizen_id:
        citizen_id = country_id  # client sample sets it equal to countryId
    payload: dict[str, Any] = {
        "countryId": country_id,
        "nationalityId": nationality_id,
        "citizenId": citizen_id,
        "visaTypeId": visa_type_id,
        "checkInDate": to_mm_dd_yyyy(travel_date),
        "guestInfo": {"adults": adults, "children": children},
        "agentMarkupType": 0,
        "agentMarkup": 0,
        "UrlPath": url_path,
    }
    try:
        return get_client().post(VISA_LIST_PATH, json=payload, headers=base_headers())
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise VisaInfoFailed(f"Visa info call failed: {e}", endpoint=VISA_LIST_PATH) from e


# =============================================================================
# Packages (list + rate + static data)
# =============================================================================
def _package_payload(
    *,
    country_id: int,
    city_id: int,
    check_in: str,
    check_out: str,
    nights: int,
    package_id: int = 0,
    region_id: int = 0,
    nationality: str = "India",
    residency: str = "India",
    rooms: list[dict[str, Any]] | None = None,
    living_name: str = "",
    agent_markup: int = 0,
) -> dict[str, Any]:
    if rooms is None:
        rooms = [
            {
                "roomNo": 1,
                "roomName": "",
                "roomType": "",
                "noofAdults": 2,
                "noofChild": 0,
                "child1Age": 0,
                "child2Age": 0,
            }
        ]
    return {
        "cityID": city_id,
        "nationality": nationality,
        "residency": residency,
        "checkInDate": check_in,
        "checkOutDate": check_out,
        "noofNights": nights,
        "rooms": rooms,
        "countryId": str(country_id),
        "packageId": package_id,
        "livingName": living_name,
        "regionId": region_id,
        "AgentMarkup": agent_markup,
    }


def call_list_packages(
    *,
    country_id: int,
    city_id: int = 0,
    check_in: str = "",
    check_out: str = "",
    nights: int = 0,
    adults: int = 2,
    children: int = 0,
    nationality: str = "India",
    residency: str = "India",
) -> dict[str, Any]:
    """List packages for a country/city window.

    Note: the client's sample uses ISO yyyy-mm-dd for these dates
    (different from flight/hotel formats). Don't pre-convert.
    """
    rooms = [
        {
            "roomNo": 1,
            "roomName": "",
            "roomType": "",
            "noofAdults": adults,
            "noofChild": children,
            "child1Age": 0,
            "child2Age": 0,
        }
    ]
    try:
        return get_client().post(
            PACKAGE_LIST_PATH,
            json=_package_payload(
                country_id=country_id,
                city_id=city_id,
                check_in=check_in,
                check_out=check_out,
                nights=nights,
                nationality=nationality,
                residency=residency,
                rooms=rooms,
            ),
            headers=base_headers(),
        )
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise PackageSearchFailed(
            f"Package list call failed: {e}", endpoint=PACKAGE_LIST_PATH
        ) from e


def call_package_rates(
    *,
    package_id: int,
    country_id: int = 0,
    city_id: int = 0,
    check_in: str = "",
    check_out: str = "",
    nights: int = 0,
    adults: int = 2,
    children: int = 0,
    nationality: str = "India",
    residency: str = "India",
) -> dict[str, Any]:
    """Get the rate for a single package.

    Note: client's sample takes ONE packageId at a time (no list).
    """
    rooms = [
        {
            "roomNo": 1,
            "roomName": "",
            "roomType": "",
            "noofAdults": adults,
            "noofChild": children,
            "child1Age": 0,
            "child2Age": 0,
        }
    ]
    try:
        return get_client().post(
            PACKAGE_RATE_PATH,
            json=_package_payload(
                country_id=country_id,
                city_id=city_id,
                check_in=check_in,
                check_out=check_out,
                nights=nights,
                package_id=package_id,
                nationality=nationality,
                residency=residency,
                rooms=rooms,
            ),
            headers=base_headers(),
        )
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise PackageSearchFailed(
            f"Package rate call failed: {e}", endpoint=PACKAGE_RATE_PATH
        ) from e


def call_package_static_data(*, package_id: int) -> dict[str, Any]:
    """Get static (no-rate) content for a package. GET with query param."""
    try:
        return get_client().get(
            PACKAGE_STATIC_DATA_PATH,
            params={"PackageId": package_id},
            headers=base_headers(),
        )
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise PackageDetailsFailed(
            f"Package static-data call failed: {e}", endpoint=PACKAGE_STATIC_DATA_PATH
        ) from e


# =============================================================================
# Hotel static content (cities, static data, descriptions, guest reviews)
# =============================================================================
# These six endpoints back the hotel detail surfaces. They are signed with the
# dedicated Hotels-only account token (`hotel_static_headers`), which carries
# the booking permissions the static endpoints require. Payloads mirror the
# N8N-Technoheven V1 collection exactly — note the `Request` envelope and the
# `IsMobile` int that wraps most of them.


def call_hotel_cities(*, city_name: str) -> dict[str, Any]:
    """Look up bookable cities by name (GetCitiesWithHotel).

    Returns the supplier's city records (incl. CityID) so callers can resolve
    a free-text city to the numeric CityID the search/static endpoints need.
    """
    payload: dict[str, Any] = {"Request": {"CityName": city_name}}
    try:
        return get_client().post(
            HOTEL_CITIES_PATH, json=payload, headers=hotel_static_headers()
        )
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise HotelStaticDataFailed(
            f"GetCitiesWithHotel call failed: {e}", endpoint=HOTEL_CITIES_PATH
        ) from e


def call_hotel_static_by_city(
    *, city_id: int, location_id: str = "", lookup_type: str = "city"
) -> dict[str, Any]:
    """List hotel IDs for a city (or location) — GetStaticDataByCity.

    `lookup_type` is "city" (use city_id) or "location" (use location_id).
    """
    payload: dict[str, Any] = {
        "Request": {
            "CityID": str(city_id),
            "Type": lookup_type,
            "LocationId": location_id,
        }
    }
    try:
        return get_client().post(
            HOTEL_STATIC_BY_CITY_PATH, json=payload, headers=hotel_static_headers()
        )
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise HotelStaticDataFailed(
            f"GetStaticDataByCity call failed: {e}", endpoint=HOTEL_STATIC_BY_CITY_PATH
        ) from e


@lru_cache(maxsize=8)
def discover_city_hotel_ids(city_id: int) -> tuple[tuple[int, float], ...]:
    """Live (hotel_id, star_rating) list for a city, from GetStaticDataByCity.

    The supplier exposes the full inventory here (thousands of hotels with star
    ratings); search_hotels uses this to look beyond the small curated set.
    Cached for the process lifetime — the list is large and effectively static
    per city. Returns () on failure so the caller can fall back gracefully.
    Sorted star-desc so a star-filtered batch favours rated properties.
    """
    try:
        raw = call_hotel_static_by_city(city_id=city_id, lookup_type="city")
    except Exception:
        return ()
    # GetStaticDataByCity returns {"CountryId": ..., "Hotels": [{HotelId, StarRating,
    # Category, ...}, ...]} — thousands of hotels. Older code looked for "raw"/
    # a top-level list and silently got nothing (→ only the 2 curated hotels showed).
    items = None
    if isinstance(raw, dict):
        items = raw.get("Hotels") or raw.get("raw") or raw.get("result")
    if not isinstance(items, list):
        items = raw if isinstance(raw, list) else []
    out: list[tuple[int, float]] = []
    for h in items:
        if not isinstance(h, dict):
            continue
        hid = h.get("HotelId") or h.get("hotelId")
        if hid is None:
            continue
        try:
            out.append((int(hid), float(h.get("StarRating") or 0)))
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda t: t[1], reverse=True)
    return tuple(out)


def call_hotel_static_data(
    *,
    hotel_ids: list[int] | str,
    city_id: int = 0,
    language_id: int = 0,
    show_rooms: bool = False,
) -> dict[str, Any]:
    """Rating/review + core static data for one or more hotels.

    GetHotelStaticDataOptimize. `hotel_ids` accepts a list or a pre-joined
    comma-separated string ("176,177,...").
    """
    hotel_ids_str = (
        hotel_ids if isinstance(hotel_ids, str) else ",".join(str(h) for h in hotel_ids)
    )
    payload: dict[str, Any] = {
        "IsMobile": 1,
        "Request": {
            "CityId": str(city_id),
            "HotelIDs": hotel_ids_str,
            "LanguageId": language_id,
            "IsShowRooms": 1 if show_rooms else 0,
        },
    }
    try:
        return get_client().post(
            HOTEL_STATIC_OPTIMIZE_PATH, json=payload, headers=hotel_static_headers()
        )
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise HotelStaticDataFailed(
            f"GetHotelStaticDataOptimize call failed: {e}", endpoint=HOTEL_STATIC_OPTIMIZE_PATH
        ) from e


def call_hotel_property_info(
    *,
    hotel_ids: list[int] | str,
    city_id: int = 0,
    language_id: int = 0,
    show_rooms: bool = False,
) -> dict[str, Any]:
    """Property info incl. addresses for a batch of hotels.

    gethotelstaticdatalistsuboptimize_v1_Address.
    """
    hotel_ids_str = (
        hotel_ids if isinstance(hotel_ids, str) else ",".join(str(h) for h in hotel_ids)
    )
    payload: dict[str, Any] = {
        "IsMobile": 1,
        "Request": {
            "CityId": str(city_id),
            "HotelIDs": hotel_ids_str,
            "LanguageId": language_id,
            "IsShowRooms": 1 if show_rooms else 0,
        },
    }
    try:
        return get_client().post(
            HOTEL_STATIC_LIST_ADDRESS_PATH, json=payload, headers=hotel_static_headers()
        )
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise HotelStaticDataFailed(
            f"HotelPropertyInfo call failed: {e}", endpoint=HOTEL_STATIC_LIST_ADDRESS_PATH
        ) from e


def call_hotel_descriptions(
    *,
    hotel_ids: list[int] | str,
    city_id: int = 0,
    language_id: int = 0,
    show_rooms: bool = True,
) -> dict[str, Any]:
    """Long-form property descriptions — GetPropertyDescriptions.

    NOTE: this endpoint's payload uses `HotelIds` (lowercase 's'), unlike the
    sibling endpoints which use `HotelIDs`. Mirrors the collection exactly.
    """
    hotel_ids_str = (
        hotel_ids if isinstance(hotel_ids, str) else ",".join(str(h) for h in hotel_ids)
    )
    payload: dict[str, Any] = {
        "IsMobile": 1,
        "Request": {
            "CityId": str(city_id),
            "HotelIds": hotel_ids_str,
            "LanguageId": language_id,
            "IsShowRooms": 1 if show_rooms else 0,
        },
        "token": "",
    }
    try:
        return get_client().post(
            HOTEL_DESCRIPTIONS_PATH, json=payload, headers=hotel_static_headers()
        )
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise HotelStaticDataFailed(
            f"GetPropertyDescriptions call failed: {e}", endpoint=HOTEL_DESCRIPTIONS_PATH
        ) from e


def call_hotel_guest_review(*, hotel_id: int) -> dict[str, Any]:
    """Aggregated guest reviews for a single hotel — GetHotelGuestReview."""
    payload: dict[str, Any] = {"Request": {"HotelId": hotel_id}}
    try:
        return get_client().post(
            HOTEL_GUEST_REVIEW_PATH, json=payload, headers=hotel_static_headers()
        )
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise HotelStaticDataFailed(
            f"GetHotelGuestReview call failed: {e}", endpoint=HOTEL_GUEST_REVIEW_PATH
        ) from e


# =============================================================================
# Currency — rate of exchange (ROE)
# =============================================================================
def call_currency_roe(*, target_currency: str = "INR") -> dict[str, Any]:
    """Live rate of exchange for `target_currency` — GET /api/Currency/ROE/{code}.

    Mirrors the N8N-Technoheven V1 collection's ROE request: a GET signed with
    the antiforgery `RequestVerificationToken` (see currency_roe_headers). The
    `{code}` is the customer-facing currency (INR for Indian customers); the
    response carries the supplier-currency-to-`code` rate(s).

    Returns the raw JSON; rate extraction happens in parsers. Raises
    CurrencyRoeFailed on unexpected errors — callers that have a manual FX rate
    to fall back to should catch it rather than surface it to the customer.
    """
    code = (target_currency or "INR").strip().upper()
    path = CURRENCY_ROE_PATH_TPL.format(code=code)
    try:
        return get_client().get(path, headers=currency_roe_headers())
    except Exception as e:
        if isinstance(e, BookingApiError):
            raise
        raise CurrencyRoeFailed(f"Currency ROE call failed: {e}", endpoint=path) from e
