"""All booking API endpoint functions.

14 endpoints total:
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

Each function:
- Builds payload matching the client's Postman collection EXACTLY
- Calls the HTTP client with the right header set
- Wraps unexpected errors in the appropriate *Failed subclass
- Lets typed BookingApiError subclasses (Unauthorized/NotFound/etc) bubble through
- Returns raw JSON (parsing happens in `parsers.py`)
"""

from __future__ import annotations

from typing import Any

from booking_api.headers import base_headers, flight_list_headers, flight_search_headers
from booking_api.http_client import get_client
from core import (
    BookingApiError,
    FlightDetailsFailed,
    FlightSearchFailed,
    HotelSearchFailed,
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
TRANSFER_LIST_PATH = "/api/transferservices/TransferList"
TRANSFER_DETAILS_PATH = "/api/transferservices/TransferDetail"
RESTAURANT_LIST_PATH = "/api/restaurant/v1/restaurants"
RESTAURANT_DETAILS_PATH_TPL = "/api/restaurant/v1/restaurants/{id}"
VISA_LIST_PATH = "/api/visa/v1/visas"
PACKAGE_LIST_PATH = "/api/staticpackageservices/staticpackage/packagelist"
PACKAGE_RATE_PATH = "/api/staticpackageservices/staticpackage/packagerate"
PACKAGE_STATIC_DATA_PATH = "/api/staticpackageservices/staticpackage/packagestaticdata"


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
def call_hotel_availability(
    *,
    hotel_ids: list[int],
    city_id: int,
    check_in: str,
    check_out: str,
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
    """
    from core import nights_between

    nights = nights_between(check_in, check_out)
    hotel_ids_str = ",".join(str(hid) for hid in hotel_ids)
    rooms = [
        {
            "RoomNo": 1,
            "NoofAdults": adults,
            "NoOfChild": children,
            "ChildAge": child_ages or [],
        }
    ]
    payload: dict[str, Any] = {
        "Token": "",
        "Request": {
            "Rooms": rooms,
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
    adults: int = 1,
    unique_key: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "fromLongitude": from_lng,
        "fromLatitude": from_lat,
        "toLongitude": to_lng,
        "toLatitude": to_lat,
        "departureDate": departure_date,
        "departureTime": departure_time,
        "returnDate": return_date or "",
        "returnTime": return_time,
        "isRoundTrip": 1 if is_round_trip else 0,
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
        "agtMkp": 0,
        "agtMkpType": 0,
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

    search_date format from client: dd-mm-yyyy (e.g. '06-06-2026').
    Caller passes ISO yyyy-mm-dd; we convert.
    """
    payload: dict[str, Any] = {
        "cityid": city_id,
        "GuestInfo": {"Adults": adults, "Children": children},
        "SearchDate": to_dd_mm_yyyy(search_date),
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
        "SearchDate": to_dd_mm_yyyy(search_date),
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
    """
    if not citizen_id:
        citizen_id = country_id  # client sample sets it equal to countryId
    payload: dict[str, Any] = {
        "countryId": country_id,
        "nationalityId": nationality_id,
        "citizenId": citizen_id,
        "visaTypeId": visa_type_id,
        "checkInDate": to_dd_mm_yyyy(travel_date),
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
