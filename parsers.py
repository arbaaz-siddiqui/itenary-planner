"""parsers — All API response parsers.

Pure functions. Input: raw API JSON. Output: Pydantic models or normalized
dicts. No I/O, no business logic, no side effects.

Sections (search by `# ===`):
    # === flight
    # === hotel
    # === tour
    # === transfer
    # === restaurant
    # === visa
    # === package
    # === hotel static content
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

from core import (
    CancellationTerm,
    FlightNormalizationError,
    FlightOption,
    FlightSegment,
    HotelNormalizationError,
    HotelOption,
    HotelRoom,
    HotelStaticNormalizationError,
    InvalidSettingError,
    PenaltyInfo,
    RestaurantNormalizationError,
    RestaurantOption,
    TourNormalizationError,
    TourOption,
    TransferNormalizationError,
    TransferOption,
    VisaDocument,
    VisaNormalizationError,
    VisaOption,
    to_inr,
)
from fx import live_rate_map

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(value: Any) -> str:
    if not value:
        return ""
    return html.unescape(_HTML_TAG_RE.sub("", str(value))).strip()


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _safe_to_inr(amount: Any, currency: str, rates: dict[str, float]) -> float | None:
    if amount is None:
        return None
    try:
        return to_inr(float(amount), str(currency), rates=rates)
    except (InvalidSettingError, ValueError, TypeError):
        return None


# =============================================================================
# === flight
# =============================================================================
# Sanity floor for flight prices. The Technoheaven supplier API occasionally
# returns INR-labeled fares that are clearly bogus:
#   - DEL→BOM at ₹269 (Air India)
#   - DEL→DXB at ₹3,967 (Saudi Arabian Airlines, "Nonstop 4h 20m")
# These are likely admin/test fares, fare differences, or AED prices that the
# supplier mislabeled as INR (4237 / 23 ≈ AED 184, plausible one-way only).
# The realistic minimum for any India→Dubai roundtrip in low season is around
# ₹10,000. Setting floor below that lets bogus fares pollute the cheapest
# results and drags down the floor-check estimate.
#
# Tune this if running for non-Dubai routes — ₹8,000 catches the egregious
# bogus pricing without filtering legitimate budget domestic fares.
FLIGHT_PRICE_INR_FLOOR: int = 8000

# Bogus supplier fares are almost always INR-LABELED and implausibly low — e.g. a
# DEL↔DXB round-trip for 2 adults coming back as ₹9,206 (₹4,603/adult) or even
# ₹2,801. A real India↔Gulf round-trip is ~₹18,000+/adult even in low season, so
# any INR-labeled fare under this PER-ADULT floor is a mislabeled/test fare and is
# dropped. USD/AED fares (converted via live FX) are trusted and skip this check —
# the mislabeling only happens on the INR-tagged ones.
FLIGHT_INR_LABELED_PER_ADULT_FLOOR: int = 12000


def _is_bogus_flight(opt: FlightOption) -> bool:
    """True if the fare looks like supplier test/mislabeled data, not a real fare."""
    # Whole-party floor (any currency) — catches absurdly low totals.
    if opt.price_inr < FLIGHT_PRICE_INR_FLOOR:
        return True
    # INR-labeled fares are the ones the supplier mislabels; hold them to a
    # realistic per-adult floor. Converted USD/AED fares are trusted.
    if opt.currency_original.upper() == "INR":
        per_adult = opt.price_per_adult_inr
        if per_adult is None and opt.pax_count > 0:
            per_adult = opt.price_inr / opt.pax_count
        if per_adult is not None and per_adult < FLIGHT_INR_LABELED_PER_ADULT_FLOOR:
            return True
    return False


def parse_flight_response(
    raw: dict[str, Any],
    *,
    expected_destination: str | None = None,
    expected_origin: str | None = None,
    max_results: int | None = None,
) -> list[FlightOption]:
    """Parse a FlightSearch response into ranked FlightOption objects.

    Args:
        raw: The full API response dict.
        expected_destination: If set, drop itineraries whose outbound LAST
            segment doesn't end at this IATA. The API occasionally returns
            related-route suggestions (e.g. DEL→NMI when you asked DEL→BOM).
        expected_origin: Same idea for origin.
        max_results: Cap on returned options.
    """
    if not isinstance(raw, dict):
        raise FlightNormalizationError("Expected dict response", missing_field="root")

    itineraries = (raw.get("data") or {}).get("pricedItineraries") or []
    if not isinstance(itineraries, list):
        return []

    expected_dest_upper = expected_destination.upper() if expected_destination else None
    expected_origin_upper = expected_origin.upper() if expected_origin else None

    rates = live_rate_map()
    options: list[FlightOption] = []
    for item in itineraries:
        opt = _parse_flight_itinerary(item, rates)
        if opt is None:
            continue
        if _is_bogus_flight(opt):
            continue
        # Drop itineraries to/from the wrong airport
        if expected_dest_upper and opt.segments_outbound:
            actual_dest = opt.segments_outbound[-1].to_airport.upper()
            if actual_dest != expected_dest_upper:
                continue
        if expected_origin_upper and opt.segments_outbound:
            actual_origin = opt.segments_outbound[0].from_airport.upper()
            if actual_origin != expected_origin_upper:
                continue
        options.append(opt)
    options.sort(key=lambda o: o.price_inr)
    return options[:max_results] if max_results else options


def _parse_flight_itinerary(item: dict[str, Any], rates: dict[str, float]) -> FlightOption | None:
    if not isinstance(item, dict):
        return None

    pricing = item.get("airItineraryPricingInfo") or {}
    itin = pricing.get("itinTotalFare") or {}
    total = itin.get("totalFare") or {}
    amount = total.get("amount")
    currency = total.get("currencyCode")
    if amount is None or currency is None:
        return None

    price_inr = _safe_to_inr(amount, currency, rates)
    if price_inr is None:
        return None  # skip exotic currencies rather than fabricate

    base_fare_inr = _safe_to_inr(itin.get("baseFare", {}).get("amount"), currency, rates)
    total_tax_inr = _safe_to_inr(itin.get("totalTax", {}).get("amount"), currency, rates)

    # Per-adult fare + total pax come straight from the supplier's per-passenger
    # breakdown — so we quote a REAL per-person number, never total ÷ pax guessed
    # by the agent. ptC_FareBreakdowns has one entry per passenger type (ADT/CHD/INF).
    price_per_adult_inr: float | None = None
    pax_count = 0
    for b in pricing.get("ptC_FareBreakdowns") or []:
        if not isinstance(b, dict):
            continue
        ptq = b.get("passengerTypeQuantity") or {}
        qty = _safe_int(ptq.get("quantity")) or 0
        pax_count += qty
        if str(ptq.get("code") or "").upper() == "ADT":
            adt_fare = (b.get("passengerFare") or {}).get("totalFare") or {}
            price_per_adult_inr = _safe_to_inr(adt_fare.get("amount"), currency, rates)

    od_options = item.get("originDestinationOptions") or []
    segs_out = _parse_segments(od_options[0]) if len(od_options) > 0 else []
    segs_ret = _parse_segments(od_options[1]) if len(od_options) > 1 else []

    airline = segs_out[0].marketing_airline if segs_out else ""
    airline_code = segs_out[0].marketing_airline_code if segs_out else ""
    stops = max(0, len(segs_out) - 1)
    route_out = f"{segs_out[0].from_airport} → {segs_out[-1].to_airport}" if segs_out else ""
    route_ret = f"{segs_ret[0].from_airport} → {segs_ret[-1].to_airport}" if segs_ret else ""
    duration_min = sum(s.duration_min for s in segs_out) if segs_out else 0

    is_refundable_label = pricing.get("isRefundable") or ""
    # New API uses "refundable"/"nonrefundable"; legacy used "Yes"/"No"
    refundable = is_refundable_label.lower() in {"yes", "refundable", "true"}

    fare_basis_codes: list[str] = []
    baggage_info: list[str] = []
    cabin_baggage_info: list[str] = []
    penalties: list[PenaltyInfo] = []
    breakdowns = pricing.get("ptC_FareBreakdowns") or []
    if breakdowns and isinstance(breakdowns[0], dict):
        b = breakdowns[0]
        fare_basis_codes = list(b.get("fareBasisCodes") or [])
        baggage_info = list(b.get("baggageInfo") or [])
        cabin_baggage_info = list(b.get("cabinBaggageInfo") or [])
        penalties = _parse_penalties(b.get("penaltiesInfo") or [])

    return FlightOption(
        fare_source_code=str(pricing.get("fareSourceCode") or ""),
        itinerary_source_code=str(item.get("itinerarySourceCode") or ""),
        price_inr=price_inr,
        price_per_adult_inr=price_per_adult_inr,
        pax_count=pax_count,
        price_original=float(amount),
        currency_original=str(currency),
        base_fare_inr=base_fare_inr,
        total_tax_inr=total_tax_inr,
        airline=airline,
        airline_code=airline_code,
        stops=stops,
        route_outbound=route_out,
        route_return=route_ret,
        duration_min=duration_min,
        segments_outbound=segs_out,
        segments_return=segs_ret,
        refundable=refundable,
        is_refundable_label=is_refundable_label,
        fare_type=pricing.get("fareType") or "",
        fare_basis_codes=fare_basis_codes,
        baggage_info=baggage_info,
        cabin_baggage_info=cabin_baggage_info,
        penalties=penalties,
        direction=str(item.get("directionInd") or "oneway"),
        provider=str(item.get("providerName") or ""),
    )


def _parse_segments(od_option: Any) -> list[FlightSegment]:
    if not isinstance(od_option, dict):
        return []
    out: list[FlightSegment] = []
    for s in od_option.get("flightSegments") or []:
        seg = _parse_flight_segment(s)
        if seg is not None:
            out.append(seg)
    return out


def _parse_flight_segment(s: Any) -> FlightSegment | None:
    if not isinstance(s, dict):
        return None
    departure = _parse_datetime(s.get("departureDateTime"))
    arrival = _parse_datetime(s.get("arrivalDateTime"))
    if departure is None or arrival is None:
        return None
    operating = s.get("operatingAirline") or {}
    return FlightSegment(
        from_airport=str(s.get("departureAirportLocationCode") or ""),
        from_airport_name=str(s.get("departureAirportName") or ""),
        from_city=str(s.get("departureAirportCity") or ""),
        from_country=str(s.get("departureAirportCountry") or ""),
        to_airport=str(s.get("arrivalAirportLocationCode") or ""),
        to_airport_name=str(s.get("arrivalAirportName") or ""),
        to_city=str(s.get("arrivalAirportCity") or ""),
        to_country=str(s.get("arrivalAirportCountry") or ""),
        departure=departure,
        arrival=arrival,
        duration_min=int(s.get("journeyDuration") or 0),
        layover_min=int(s.get("layoverTimeInMinute") or 0),
        marketing_airline=str(s.get("marketingAirlineName") or ""),
        marketing_airline_code=str(s.get("marketingAirlineCode") or ""),
        operating_airline=str(operating.get("operatingAirlineName") or ""),
        operating_airline_code=str(operating.get("code") or ""),
        airline_logo_path=str(s.get("airlinelogoPath") or operating.get("airlinelogoPath") or ""),
        flight_number=str(s.get("flightNumber") or ""),
        aircraft=str(s.get("aircraft") or ""),
        cabin_class=str(s.get("cabinClass") or "E"),
        cabin_class_text=str(s.get("cabinClassText") or ""),
        cabin_class_rbd=str(s.get("cabinClassRBD") or ""),
        departure_terminal=str(s.get("departureTerminal") or ""),
        arrival_terminal=str(s.get("arrivalTerminal") or ""),
        seats_remaining=_safe_int(s.get("seatsRemaining")),
        luggage_info=str(s.get("luggageInfo") or ""),
        cabin_luggage_info=str(s.get("cabinLuggageInfo") or ""),
    )


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_penalties(items: list[Any]) -> list[PenaltyInfo]:
    out: list[PenaltyInfo] = []
    for p in items:
        if not isinstance(p, dict):
            continue
        out.append(
            PenaltyInfo(
                allowed=bool(p.get("allowed", False)),
                amount=float(p.get("amount") or 0),
                currency_code=str(p.get("currencyCode") or "INR"),
                penalty_type=str(p.get("penaltyType") or ""),
                last_ticketing_date=p.get("lastTickitingDate"),
            )
        )
    return out


# =============================================================================
# === hotel
# =============================================================================
def parse_hotel_response(
    raw: dict[str, Any],
    *,
    nights: int,
    hotel_names: dict[str, str] | None = None,
    hotel_areas: dict[str, str] | None = None,
    hotel_stars: dict[str, float] | None = None,
    max_results: int | None = None,
) -> list[HotelOption]:
    if not isinstance(raw, dict):
        raise HotelNormalizationError("Expected dict response", missing_field="root")

    rs = raw.get("AvailabilityRS") or {}
    hotel_results = rs.get("HotelResult") or []
    if not isinstance(hotel_results, list):
        return []

    response_currency = rs.get("Currency") or "USD"
    rates = live_rate_map()
    names = hotel_names or {}
    areas = hotel_areas or {}
    star_map = hotel_stars or {}

    options: list[HotelOption] = []
    for h in hotel_results:
        opt = _parse_hotel(h, nights, response_currency, rates, names, areas, star_map)
        if opt is not None:
            options.append(opt)
    options.sort(key=lambda o: o.price_inr)
    return options[:max_results] if max_results else options


def _parse_hotel(
    h: Any,
    nights: int,
    response_currency: str,
    rates: dict[str, float],
    names: dict[str, str],
    areas: dict[str, str],
    star_map: dict[str, float] | None = None,
) -> HotelOption | None:
    if not isinstance(h, dict):
        return None
    hotel_id_int = _safe_int(h.get("HotelId"))
    if hotel_id_int is None:
        return None
    hotel_id_str = str(hotel_id_int)
    hotel_name = names.get(hotel_id_str, f"Hotel {hotel_id_int}")
    area = areas.get(hotel_id_str, "")

    start_price = float(h.get("StartPrice") or 0)
    # The availability API does NOT return star ratings (sends 0). Fall back to
    # the reference-data stars so the star filter actually works.
    stars = float(h.get("StarRating") or 0)
    if stars <= 0 and star_map:
        stars = float(star_map.get(hotel_id_str, 0) or 0)

    rooms: list[HotelRoom] = []
    for opt in h.get("HotelOption") or []:
        if not isinstance(opt, dict):
            continue
        supplier_name = str(opt.get("SupplierName") or "")
        for room_group in opt.get("HotelRooms") or []:
            if not isinstance(room_group, list):
                continue
            for room in room_group:
                parsed = _parse_room(room, response_currency, rates, supplier_name)
                if parsed is not None:
                    rooms.append(parsed)
    if not rooms:
        return None

    rooms.sort(key=lambda r: r.price_inr)
    cheapest = rooms[0]
    per_night = cheapest.price_inr / nights if nights > 0 else cheapest.price_inr

    return HotelOption(
        hotel_id=hotel_id_int,
        hotel_name=hotel_name,
        area=area,
        price_inr=cheapest.price_inr,
        per_night_inr=round(per_night, 2),
        nights=nights,
        currency_original=response_currency,
        start_price_original=start_price,
        stars=stars,
        rooms=rooms,
        cheapest_room_type=cheapest.room_type_name,
        cheapest_board=cheapest.mapped_meal_name or cheapest.meal_name,
        cheapest_room_supplier=cheapest.supplier_name,
        has_free_cancellation=cheapest.is_free_cancellation,
    )


def _parse_room(
    room: Any, currency: str, rates: dict[str, float], supplier_name: str
) -> HotelRoom | None:
    if not isinstance(room, dict):
        return None
    price = room.get("Price")
    if price is None:
        return None
    price_inr = _safe_to_inr(price, currency, rates)
    if price_inr is None:
        return None
    cancellation_policy: list[CancellationTerm] = []
    for cp in room.get("CancellationPolicy") or []:
        if not isinstance(cp, dict):
            continue
        cp_price = float(cp.get("CancellationPrice") or 0)
        cancellation_policy.append(
            CancellationTerm(
                from_date=cp.get("FromDate"),
                to_date=cp.get("ToDate"),
                cancellation_price=cp_price,
                currency=str(cp.get("Currency") or currency),
                is_free_cancellation=cp_price == 0,
            )
        )
    return HotelRoom(
        room_type_name=str(room.get("RoomTypeName") or ""),
        price_inr=price_inr,
        price_original=float(price),
        currency_original=str(currency),
        meal_name=str(room.get("MealName") or ""),
        mapped_meal_name=str(room.get("MappedMealName") or ""),
        booking_status=str(room.get("BookingStatus") or "Available"),
        cancellation_policy=cancellation_policy,
        supplier_currency=str(room.get("SupplierCurrency") or ""),
        supplier_name=supplier_name,
    )


# =============================================================================
# === tour
# =============================================================================
def parse_tour_response(
    list_raw: dict[str, Any],
    rate_raw: dict[str, Any],
    *,
    image_base_url: str = "https://stagingapi.gujjutours.com",
    max_results: int | None = None,
) -> list[TourOption]:
    if not isinstance(list_raw, dict):
        raise TourNormalizationError("Expected dict for list response", missing_field="root")

    tour_list = (list_raw.get("result") or {}).get("tourStaticlists") or []
    if not isinstance(tour_list, list):
        return []

    rate_result = rate_raw.get("result") if isinstance(rate_raw, dict) else None
    rate_map: dict[int, dict[str, Any]] = {}
    if isinstance(rate_result, list):
        for r in rate_result:
            if isinstance(r, dict):
                tid = _safe_int(r.get("tourID"))
                if tid is not None:
                    rate_map[tid] = r

    rates = live_rate_map()
    options: list[TourOption] = []
    for t in tour_list:
        opt = _parse_tour(t, rate_map, rates, image_base_url)
        # Drop tours with no rate (not bookable). These would otherwise
        # float to the top of the sorted-by-price list as price_inr=0.
        if opt is not None and opt.price_per_adult_inr > 0:
            options.append(opt)
    options.sort(key=lambda o: o.price_per_adult_inr)
    return options[:max_results] if max_results else options


def _parse_tour(
    t: Any,
    rate_map: dict[int, dict[str, Any]],
    rates: dict[str, float],
    image_base_url: str,
) -> TourOption | None:
    if not isinstance(t, dict):
        return None
    tour_id = _safe_int(t.get("tourID"))
    if tour_id is None:
        return None
    rate_entry = rate_map.get(tour_id, {})
    final_rate = rate_entry.get("finalRate")
    currency = rate_entry.get("currencyCode") or "AED"
    price_inr = _safe_to_inr(final_rate, currency, rates) or 0.0
    image_url = _resolve_image_url(t.get("imagePath"), image_base_url)
    return TourOption(
        tour_id=tour_id,
        name=str(t.get("tourName") or "").strip(),
        category=str(t.get("tourTypeName") or ""),
        price_per_adult_inr=price_inr,
        price_original=float(final_rate or 0),
        currency_original=str(currency),
        final_rate_original=float(final_rate or 0),
        discount_type=str(rate_entry.get("discountType") or ""),
        discount_value=float(rate_entry.get("discountValue") or 0),
        duration=str(t.get("duration") or ""),
        short_description=_strip_html(t.get("tourShortDescription")),
        full_description=_strip_html(t.get("tourDescription") or t.get("tourLongDescription")),
        inclusions=_parse_bullets(t.get("inclusion") or t.get("includes")),
        exclusions=_parse_bullets(t.get("exclusion") or t.get("excludes")),
        city_name=str(t.get("cityName") or ""),
        country_name=str(t.get("countryName") or ""),
        address=str(t.get("address") or ""),
        rating=float(t.get("tourrating") or 0),
        reviews_count=int(t.get("reviewsCount") or 0),
        is_recommended=bool(t.get("isRecommanded") or t.get("isRecommended")),
        supplier_name=str(t.get("supplierName") or ""),
        image_url=image_url,
    )


def _parse_bullets(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [_strip_html(v) for v in value if v]
    text = _strip_html(value)
    return [line.strip("•- ").strip() for line in text.split("\n") if line.strip()]


def _resolve_image_url(image_path: Any, base_url: str) -> str:
    if not image_path:
        return ""
    image_path = str(image_path)
    if image_path.startswith("http"):
        return image_path
    return base_url + ("" if image_path.startswith("/") else "/") + image_path


# =============================================================================
# === transfer
# =============================================================================
def parse_transfer_response(
    raw: dict[str, Any],
    *,
    image_base_url: str = "https://stagingapi.gujjutours.com",
    max_results: int | None = None,
) -> list[TransferOption]:
    if not isinstance(raw, dict):
        raise TransferNormalizationError("Expected dict response", missing_field="root")
    result = raw.get("result")
    if not isinstance(result, list):
        return []
    rates = live_rate_map()
    options: list[TransferOption] = []
    for t in result:
        opt = _parse_transfer(t, rates, image_base_url)
        if opt is not None:
            options.append(opt)
    options.sort(key=lambda o: o.price_inr)
    return options[:max_results] if max_results else options


def _parse_transfer(t: Any, rates: dict[str, float], image_base_url: str) -> TransferOption | None:
    if not isinstance(t, dict):
        return None
    price = t.get("totalPrice")
    if price is None:
        return None
    currency = (t.get("currencyCode") or "AED").strip()
    price_inr = _safe_to_inr(price, currency, rates)
    if price_inr is None:
        return None
    transfer_type = str(t.get("transferType") or "")
    vehicle_type = str(t.get("vehicleType") or "")
    vehicle_name = str(t.get("vehicleName") or "")
    # Real supplier transferType values (confirmed from B2C): "Standard", "Private Transfer", "Large"
    # None of the B2C results are shared — all are private vehicles.
    # Shared detection kept in case supplier ever returns shared options.
    name_lc = vehicle_name.lower()
    transfer_type_lc = transfer_type.lower()
    is_shared = "shared" in transfer_type_lc or "sharing" in transfer_type_lc or "shared" in name_lc
    # "Standard", "Private Transfer", "Large" all map to Private
    is_private = not is_shared
    badges: list[str] = []
    if is_shared:
        badges.append("Shared")
    else:
        badges.append("Private")
    if vehicle_type:
        badges.append(vehicle_type)
    # Normalize to clear Shared|Private label
    if is_shared:
        transfer_type = "Shared"
    else:
        transfer_type = "Private"
    return TransferOption(
        transfer_id=str(t.get("transferID") or t.get("uniqueKey") or t.get("vehicleId") or ""),
        unique_key=str(t.get("uniqueKey") or ""),
        vehicle_name=str(t.get("vehicleName") or ""),
        vehicle_type=vehicle_type,
        transfer_type=transfer_type,
        capacity=int(t.get("capacity") or 0),
        luggage_capacity=int(t.get("luggageCapacity") or 0),
        fuel_type=str(t.get("fuelType") or ""),
        price_inr=price_inr,
        price_original=float(price),
        currency_original=str(currency),
        distance_km=float(t.get("distanceKM") or 0),
        estimated_time=str(t.get("estimatedTime") or ""),
        policy_name=str(t.get("policyName") or ""),
        image_url=_resolve_image_url(t.get("imagePath"), image_base_url),
        supplier_name=str(t.get("supplierName") or ""),
        badges=badges,
    )


# =============================================================================
# === restaurant
# =============================================================================
def parse_restaurant_response(
    raw: dict[str, Any],
    *,
    image_base_url: str = "https://stagingapi.gujjutours.com",
    max_results: int | None = None,
) -> list[RestaurantOption]:
    if not isinstance(raw, dict):
        raise RestaurantNormalizationError("Expected dict response", missing_field="root")
    result = raw.get("result") or {}
    # List endpoint: result.list; Detail endpoint: result.detail (single-item array)
    if isinstance(result, dict):
        items = result.get("list") or result.get("detail")
    else:
        items = None
    if not isinstance(items, list):
        return []
    rates = live_rate_map()
    options: list[RestaurantOption] = []
    for r in items:
        opt = _parse_restaurant(r, rates, image_base_url)
        if opt is not None:
            options.append(opt)
    options.sort(key=lambda o: o.price_per_adult_inr)
    return options[:max_results] if max_results else options


def _parse_restaurant(
    r: Any, rates: dict[str, float], image_base_url: str
) -> RestaurantOption | None:
    if not isinstance(r, dict):
        return None
    restaurant_id = _safe_int(r.get("restaurantId"))
    if restaurant_id is None:
        return None
    price_obj = r.get("priceStarts") or {}
    price = price_obj.get("perPersonPrice")
    currency = (price_obj.get("currency") or "AED").strip()
    price_inr = _safe_to_inr(price, currency, rates) or 0.0
    food_type = r.get("foodType") or {}
    restaurant_type = r.get("restaurantType") or {}
    address = r.get("address") or {}
    review = r.get("review") or {}
    hours = r.get("operatingHours") or {}
    # Coordinates — nested under "coordinates" object in the real response
    coords = r.get("coordinates") or {}
    latitude = _try_float(coords.get("latitude")) if isinstance(coords, dict) else None
    longitude = _try_float(coords.get("longitude")) if isinstance(coords, dict) else None
    # Rating: top-level field is the primary; review sub-object is a fallback
    rating = _try_float(r.get("rating")) or _try_float(review.get("rating")) or 0.0
    # Images: imageInfoList has full list; restaurantImagePath is primary thumbnail
    primary_img = _resolve_image_url(r.get("restaurantImagePath"), image_base_url)
    image_urls: list[str] = []
    for img in r.get("imageInfoList") or []:
        if isinstance(img, dict):
            url = _resolve_image_url(img.get("url"), image_base_url)
        else:
            url = _resolve_image_url(img, image_base_url)
        if url and url not in image_urls:
            image_urls.append(url)
    if primary_img and primary_img not in image_urls:
        image_urls.insert(0, primary_img)
    return RestaurantOption(
        restaurant_id=restaurant_id,
        name=str(r.get("restaurantName") or "").strip(),
        price_per_adult_inr=price_inr,
        price_original=float(price or 0),
        currency_original=str(currency),
        cuisine=str(food_type.get("foodTypeName") or ""),
        veg_type=str(restaurant_type.get("restaurantTypeName") or ""),
        full_address=_strip_html(address.get("fullAddress")),
        city=str(address.get("city") or ""),
        opening_time=str(hours.get("openingTime") or ""),
        closing_time=str(hours.get("closingTime") or ""),
        seating_capacity=int(r.get("seatingCapacity") or 0),
        rating=rating,
        description=_strip_html(r.get("description")),
        image_url=primary_img,
        image_urls=image_urls,
        latitude=latitude,
        longitude=longitude,
    )


# =============================================================================
# === visa
# =============================================================================
def parse_visa_response(raw: dict[str, Any], *, max_results: int | None = None) -> list[VisaOption]:
    """Parse the new-API visa response.

    Real shape (from sample):
        result.visas[*] = {visaId, name, visaType, options[*]}
        result.visas[*].options[*] = {
            visaOptionId, visaOptionName, processingTime, entryType,
            validityPeriod, stayPeriod, isEvisa, visaRates[*], requiredDocuments[*]
        }

    Each (visa x option) pair becomes one VisaOption row.
    """
    if not isinstance(raw, dict):
        raise VisaNormalizationError("Expected dict response", missing_field="root")
    result = raw.get("result") or raw.get("data") or {}

    # Pull out the list of visa types (each with multiple sub-options)
    if isinstance(result, list):
        visas_list = result  # old shape, kept for back-compat
    elif isinstance(result, dict):
        visas_list = result.get("visas") or result.get("visaOptions") or result.get("list") or []
    else:
        visas_list = []

    rates = live_rate_map()
    options: list[VisaOption] = []
    for v in visas_list:
        if not isinstance(v, dict):
            continue
        # Each visa type can have sub-options (e.g. 30-day single, 60-day multi).
        # Flatten them into individual rows.
        sub_options = v.get("options") if isinstance(v.get("options"), list) else None
        if sub_options:
            parent_name = v.get("name") or v.get("visaType") or "Tourist Visa"
            visa_type_id = v.get("visaTypeId")
            for opt in sub_options:
                row = _parse_visa_option(opt, parent_name, visa_type_id, rates)
                if row is not None:
                    options.append(row)
        else:
            # Legacy flat-option shape
            row = _parse_visa_flat(v, rates)
            if row is not None:
                options.append(row)
    return options[:max_results] if max_results else options


def _parse_visa_option(
    opt: Any,
    parent_name: str,
    visa_type_id: Any,
    rates: dict[str, float],
) -> VisaOption | None:
    """Parse a single option inside a visa block (new nested shape)."""
    if not isinstance(opt, dict):
        return None
    option_id = opt.get("visaOptionId")
    if option_id is None:
        return None

    # Pricing comes via `visaRates[*].fareInfo[*]`. Empty when not enabled
    # for this agent — return as "On Request".
    price_original = 0.0
    currency_original = "INR"
    price_inr = 0.0
    pricing_available = False
    for rate in opt.get("visaRates") or []:
        if not isinstance(rate, dict):
            continue
        fare_info = rate.get("fareInfo") or []
        for fi in fare_info:
            if not isinstance(fi, dict):
                continue
            amount = fi.get("amount") or fi.get("rate") or fi.get("price")
            currency = fi.get("currency") or fi.get("currencyCode") or "AED"
            if amount and float(amount) > 0:
                pricing_available = True
                price_original = float(amount)
                currency_original = str(currency)
                price_inr = _safe_to_inr(price_original, currency_original, rates) or 0.0
                break
        if pricing_available:
            break

    # requiredDocuments[*] carries name + description + isRequired. Keep the
    # flat name list for back-compat AND the structured form, so callers can
    # show mandatory-vs-optional and the per-document guidance text.
    documents: list[str] = []
    documents_detailed: list[VisaDocument] = []
    for d in opt.get("requiredDocuments") or []:
        if isinstance(d, dict):
            name = d.get("applicantType") or d.get("documentName") or d.get("name")
            if name:
                clean_name = str(name).strip()
                documents.append(clean_name)
                documents_detailed.append(
                    VisaDocument(
                        name=clean_name,
                        description=_strip_html(d.get("description")),
                        is_required=bool(d.get("isRequired", True)),
                    )
                )
        elif isinstance(d, str):
            documents.append(d)
            documents_detailed.append(VisaDocument(name=d))

    # Processing tiers: the supplier exposes Normal / Express as separate rate
    # rows. Surfacing the choice matters even when the fares are empty.
    process_types: list[str] = []
    for rate in opt.get("visaRates") or []:
        if isinstance(rate, dict):
            pt = str(rate.get("processType") or "").strip()
            if pt and pt not in process_types:
                process_types.append(pt)

    # processingTime is free text ("3-4 Working Days") and is frequently "".
    processing_text = str(opt.get("processingTime") or "").strip()
    if not processing_text:
        for rate in opt.get("visaRates") or []:
            if isinstance(rate, dict):
                candidate = str(rate.get("processingTime") or "").strip()
                if candidate:
                    processing_text = candidate
                    break

    option_name = opt.get("visaOptionName") or parent_name
    return VisaOption(
        visa_id=option_id,
        visa_type=str(option_name),
        # New API uses validityPeriod/stayPeriod (camelCase); old used validity/stayDuration
        validity=str(opt.get("validityPeriod") or opt.get("validity") or ""),
        stay_duration=str(opt.get("stayPeriod") or opt.get("stayDuration") or ""),
        # processingTime is now a free-text string like "3-4 Working Days"; older API gave int days
        processing_days=_extract_processing_days(
            processing_text or opt.get("processingDays")
        ),
        processing_time_text=processing_text,
        entry_type=str(opt.get("entryType") or "Single"),
        is_evisa=bool(opt.get("isEvisa", True)),
        price_per_person_inr=price_inr,
        price_original=price_original,
        currency_original=currency_original,
        pricing_available=pricing_available,
        document_requirements=documents,
        documents=documents_detailed,
        process_types=process_types,
    )


def _parse_visa_flat(v: dict[str, Any], rates: dict[str, float]) -> VisaOption | None:
    """Parse the legacy flat-option shape (kept for back-compat / older fixtures)."""
    visa_id = v.get("visaId") or v.get("visaTypeId") or v.get("id")
    if visa_id is None:
        return None
    rates_list = v.get("visaRates") or []
    price_original = 0.0
    currency_original = "INR"
    price_inr = 0.0
    pricing_available = False
    if isinstance(rates_list, list) and rates_list:
        first_rate = rates_list[0]
        if isinstance(first_rate, dict):
            amount = first_rate.get("amount") or first_rate.get("rate")
            currency = first_rate.get("currency") or first_rate.get("currencyCode") or "AED"
            if amount and float(amount) > 0:
                pricing_available = True
                price_original = float(amount)
                currency_original = str(currency)
                price_inr = _safe_to_inr(price_original, currency_original, rates) or 0.0
    visa_type_name = v.get("visaTypeName") or v.get("visaType") or v.get("name") or "Tourist Visa"
    documents = v.get("documentRequirements") or v.get("documents") or []
    if not isinstance(documents, list):
        documents = []
    return VisaOption(
        visa_id=visa_id,
        visa_type=str(visa_type_name),
        validity=str(v.get("validity") or ""),
        stay_duration=str(v.get("stayDuration") or v.get("duration") or ""),
        processing_days=int(v.get("processingDays") or 0),
        entry_type=str(v.get("entryType") or "Single"),
        is_evisa=bool(v.get("isEvisa", True)),
        price_per_person_inr=price_inr,
        price_original=price_original,
        currency_original=currency_original,
        pricing_available=pricing_available,
        document_requirements=[str(d) for d in documents],
    )


def _extract_processing_days(value: Any) -> int:
    """Pull the first integer out of a string like '3-4 Working Days' or '5 days'."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        m = re.search(r"\d+", value)
        if m:
            return int(m.group(0))
    return 0


# =============================================================================
# === package
# =============================================================================
def parse_package_response(
    list_raw: dict[str, Any],
    rate_raw: dict[str, Any] | None = None,
    *,
    image_base_url: str = "https://stagingapi.gujjutours.com",
    max_results: int | None = None,
) -> list[dict[str, Any]]:
    """Parse the new-API package list response.

    Real shape (from sample):
        result.packages[*] = {
            packageId, packageName, duration, noOfNights,
            totalPrice, currencyName, buyingTotalPrice,
            countryListName, cityListName, packageType,
            isHotelIncluded, isTourIncluded, isFlightIncluded, ...,
            isFreeCancllation, review, reviewCount,
            imagePath, fromDate, toDate, checkInDate
        }

    NOTE: `totalPrice` of 0 means pricing-on-request for that package.
    We surface these as price_inr=0 with pricing_available=False so the
    agent can say "On Request" instead of "Free".

    The rate endpoint is called per-package-id and returns a different
    shape (`{package, packageHotel, packageAvailability}`); we don't use
    it here. Use call_package_rates for single-package detail enrichment.
    """
    if not isinstance(list_raw, dict):
        return []
    result = list_raw.get("result") or {}
    if isinstance(result, list):
        # Legacy shape (old API)
        list_items = result
    elif isinstance(result, dict):
        list_items = result.get("packages") or []
    else:
        return []
    if not isinstance(list_items, list):
        return []

    rates = live_rate_map()
    out: list[dict[str, Any]] = []
    for p in list_items:
        if not isinstance(p, dict):
            continue
        pid = _safe_int(p.get("packageId") or p.get("packageID"))
        if pid is None:
            continue

        # Prefer list-endpoint pricing fields, fall back to legacy
        total_price = p.get("totalPrice") or p.get("buyingTotalPrice") or p.get("finalRate") or 0
        currency = p.get("currencyName") or p.get("currency") or p.get("currencyCode") or "AED"
        try:
            price_original = float(total_price or 0)
        except (TypeError, ValueError):
            price_original = 0.0
        price_inr = _safe_to_inr(price_original, currency, rates) or 0.0
        pricing_available = price_inr > 0

        inclusions: list[str] = []
        for src_field in ("inclusions", "packageInclusions"):
            raw_inc = p.get(src_field)
            if isinstance(raw_inc, list):
                inclusions = [_strip_html(x) for x in raw_inc if x]
                break
            if isinstance(raw_inc, str):
                inclusions = [
                    line.strip("•- ").strip()
                    for line in _strip_html(raw_inc).split("\n")
                    if line.strip()
                ]
                break

        # What's included in the package (new boolean fields)
        included: list[str] = []
        for label, flag in [
            ("hotel", p.get("isHotelIncluded")),
            ("tours", p.get("isTourIncluded")),
            ("transfers", p.get("isTransferIncluded")),
            ("flights", p.get("isFlightIncluded")),
            ("meals", p.get("isMealIncluded")),
            ("visa", p.get("isVisaIncluded")),
            ("insurance", p.get("isInsuranceIncluded")),
        ]:
            if flag:
                included.append(label)

        out.append(
            {
                "package_id": pid,
                "name": str(p.get("packageName") or "").strip(),
                "category": str(p.get("packageType") or p.get("categoryNames") or ""),
                "duration": str(p.get("duration") or ""),
                "nights": int(p.get("noOfNights") or 0),
                "city": str(p.get("cityListName") or p.get("cityName") or ""),
                "country": str(p.get("countryListName") or p.get("countryName") or ""),
                "description": _strip_html(p.get("description")),
                "inclusions": inclusions,
                "includes": included,
                "is_free_cancellation": bool(p.get("isFreeCancllation")),
                "rating": float(p.get("review") or 0),
                "reviews_count": int(p.get("reviewCount") or 0),
                "valid_from": str(p.get("fromDate") or ""),
                "valid_to": str(p.get("toDate") or ""),
                "price_inr": price_inr,
                "price_original": price_original,
                "currency_original": str(currency),
                "pricing_available": pricing_available,
                "image_url": _resolve_image_url(
                    p.get("imagePath") or p.get("packageImagePath"), image_base_url
                ),
                "supplier_name": str(p.get("supplierName") or ""),
                "booking_status": str(p.get("bookingStatus") or ""),
            }
        )

    # Sort: priced packages first (cheapest → expensive), then on-request
    out.sort(key=lambda x: (not x["pricing_available"], x["price_inr"]))
    return out[:max_results] if max_results else out


# =============================================================================
# === hotel static content (cities, static data, descriptions, guest reviews)
# =============================================================================
# These six endpoints are detail/content surfaces, not priced search. Their
# exact JSON shapes vary by supplier and aren't pinned in the collection
# (empty `response: []`), so the parsers below are deliberately shape-tolerant:
# they probe the common envelope keys, normalize to plain dicts, and degrade
# gracefully on missing fields rather than raising. Callers get clean,
# predictable output regardless of which envelope the supplier returns.


def _first_present(obj: Any, *keys: str) -> Any:
    """Return obj[key] for the first key present (case-insensitive on dicts)."""
    if not isinstance(obj, dict):
        return None
    lower = {str(k).lower(): v for k, v in obj.items()}
    for key in keys:
        if key in obj:
            return obj[key]
        if key.lower() in lower:
            return lower[key.lower()]
    return None


def _unwrap_result(raw: Any) -> Any:
    """Peel the common response envelope to the meaningful payload.

    Hotel-static endpoints variously nest the body under Result/result/Data/
    Response, or under `raw` (our http_client wraps a top-level JSON LIST as
    {"raw": [...]}, which is exactly how GetCitiesWithHotel / GetStaticDataByCity
    return their arrays). Returns the innermost recognized payload, else input.
    """
    if not isinstance(raw, dict):
        return raw
    payload = _first_present(
        raw, "Result", "result", "Data", "data", "Response", "response", "raw"
    )
    return payload if payload is not None else raw


def parse_hotel_cities_response(
    raw: dict[str, Any], *, max_results: int | None = None
) -> list[dict[str, Any]]:
    """Normalize GetCitiesWithHotel into [{city_id, city_name, country_name, ...}]."""
    if not isinstance(raw, dict):
        raise HotelStaticNormalizationError("Expected dict response", missing_field="root")
    payload = _unwrap_result(raw)
    cities = _first_present(payload, "Cities", "CityList", "cities", "list") or payload
    if isinstance(cities, dict):
        cities = _first_present(cities, "Cities", "CityList", "list") or []
    if not isinstance(cities, list):
        return []
    out: list[dict[str, Any]] = []
    for c in cities:
        if not isinstance(c, dict):
            continue
        # Real GetCitiesWithHotel item shape (June 2026):
        #   {Id: 244520, LocationId: "6053839", FullName: "...", Type: "city",
        #    Name: "Dubai", Rank: 5}
        # The numeric `Id` IS the CityID used by HotelSearch; `LocationId` +
        # `Type` feed GetStaticDataByCity. Accept the legacy CityID keys too.
        city_id = _safe_int(_first_present(c, "CityID", "CityId", "cityId", "cityid", "Id", "id"))
        if city_id is None:
            continue
        out.append(
            {
                "city_id": city_id,
                "location_id": str(
                    _first_present(c, "LocationId", "locationId", "LocationID") or ""
                ).strip(),
                "type": str(_first_present(c, "Type", "type") or "").strip(),
                "city_name": str(
                    _first_present(c, "CityName", "cityName", "Name", "name") or ""
                ).strip(),
                "full_name": str(_first_present(c, "FullName", "fullName") or "").strip(),
                "country_name": str(
                    _first_present(c, "CountryName", "countryName", "country") or ""
                ).strip(),
                "country_id": _safe_int(_first_present(c, "CountryID", "CountryId", "countryId")),
                "state_name": str(_first_present(c, "StateName", "stateName") or "").strip(),
            }
        )
    # Rank so the agent picks the right entry: a bookable "city" beats a neighborhood,
    # which beats airports/POIs/stations. GetCitiesWithHotel returns ~100 mixed rows
    # (city, neighborhood, point_of_interest, airport, metro_station, ...) — the
    # numeric `Id` of the type=="city" row is the CityID hotel search needs, while
    # neighborhood rows' location_id feed GetStaticDataByCity(Type="location") for
    # area-scoped hotels (e.g. "hotels in Downtown Dubai").
    _rank = {"city": 0, "multi_city_vicinity": 1, "neighborhood": 2, "province_state": 3}
    out.sort(key=lambda c: _rank.get(c["type"].lower(), 9))
    return out[:max_results] if max_results else out


def parse_hotel_static_data_response(
    raw: dict[str, Any], *, max_results: int | None = None
) -> list[dict[str, Any]]:
    """Normalize hotel static-data list responses (GetHotelStaticDataOptimize,
    gethotelstaticdatalistsuboptimize_v1_Address, GetStaticDataByCity).

    Returns one normalized dict per hotel with the fields most useful to a
    detail surface: id, name, stars, address/coords, rating/review counts,
    images, amenities/facilities — all best-effort.
    """
    if not isinstance(raw, dict):
        raise HotelStaticNormalizationError("Expected dict response", missing_field="root")
    payload = _unwrap_result(raw)
    # Real shapes: GetHotelStaticDataOptimize -> {"PropertyInfo": [...]},
    # gethotelstaticdatalistsuboptimize_v1_Address -> {"PropertyAddressInfo": [...]}.
    hotels = _first_present(
        payload,
        "Hotels",
        "HotelList",
        "HotelStaticData",
        "PropertyInfo",
        "PropertyAddressInfo",
        "hotels",
        "list",
    )
    if hotels is None and isinstance(payload, list):
        hotels = payload
    if not isinstance(hotels, list):
        return []
    out: list[dict[str, Any]] = []
    for h in hotels:
        norm = _parse_hotel_static_record(h)
        if norm is not None:
            out.append(norm)
    return out[:max_results] if max_results else out


def _parse_hotel_static_record(h: Any) -> dict[str, Any] | None:
    if not isinstance(h, dict):
        return None
    hotel_id = _safe_int(_first_present(h, "HotelId", "HotelID", "hotelId", "hotelid", "id"))
    if hotel_id is None:
        return None
    address = _first_present(h, "Address", "address") or {}
    if isinstance(address, dict):
        full_address = _strip_html(
            _first_present(address, "FullAddress", "fullAddress", "Address", "address")
        )
        city = str(_first_present(address, "City", "city") or "")
        country = str(_first_present(address, "Country", "country") or "")
        latitude = _first_present(address, "Latitude", "latitude", "Lat")
        longitude = _first_present(address, "Longitude", "longitude", "Long", "Lng")
    else:
        full_address = _strip_html(address)
        city = str(_first_present(h, "City", "city") or "")
        country = str(_first_present(h, "Country", "country") or "")
        latitude = _first_present(h, "Latitude", "latitude")
        longitude = _first_present(h, "Longitude", "longitude")
    # Flat shape (HotelPropertyInfo / PropertyAddressInfo): hotel_address + lat/long
    if not full_address:
        full_address = _strip_html(_first_present(h, "hotel_address", "HotelAddress"))
    if latitude is None:
        latitude = _first_present(h, "lat", "Lat")
    if longitude is None:
        longitude = _first_present(h, "long", "Long", "lng")

    facilities = _first_present(h, "Facilities", "Amenities", "facilities", "amenities") or []
    if isinstance(facilities, str):
        facilities = [f.strip() for f in facilities.split(",") if f.strip()]
    elif isinstance(facilities, list):
        facilities = [
            (
                _strip_html(_first_present(f, "Name", "name") or "")
                if isinstance(f, dict)
                else _strip_html(f)
            )
            for f in facilities
        ]
        facilities = [f for f in facilities if f]
    else:
        facilities = []

    images = _first_present(h, "Images", "ImageList", "images", "HotelImages") or []
    image_urls: list[str] = []
    if isinstance(images, list):
        for img in images:
            if isinstance(img, dict):
                url = _first_present(img, "Url", "URL", "url", "ImagePath", "imagePath", "Path")
            else:
                url = img
            if url:
                image_urls.append(str(url))

    return {
        "hotel_id": hotel_id,
        "hotel_name": str(
            _first_present(h, "HotelName", "hotelName", "Name", "name") or f"Hotel {hotel_id}"
        ).strip(),
        "stars": float(_first_present(h, "StarRating", "starRating", "Stars", "Rating") or 0),
        "rating": float(_first_present(h, "Rating", "GuestRating", "rating", "ReviewRating") or 0),
        "reviews_count": int(
            _first_present(h, "ReviewCount", "reviewCount", "ReviewsCount", "TotalReviews") or 0
        ),
        "full_address": full_address,
        "city": city,
        "country": country,
        "latitude": _try_float(latitude),
        "longitude": _try_float(longitude),
        "description": _strip_html(
            _first_present(h, "Description", "description", "HotelDescription", "ShortDescription")
        ),
        "facilities": facilities,
        "image_urls": image_urls,
        "phone": str(_first_present(h, "Phone", "PhoneNo", "phone", "ContactNo") or ""),
    }


def parse_hotel_descriptions_response(
    raw: dict[str, Any], *, max_results: int | None = None
) -> list[dict[str, Any]]:
    """Normalize GetPropertyDescriptions into [{hotel_id, description, sections}]."""
    if not isinstance(raw, dict):
        raise HotelStaticNormalizationError("Expected dict response", missing_field="root")
    payload = _unwrap_result(raw)
    items = _first_present(
        payload, "PropertyDescriptions", "Descriptions", "Hotels", "list", "descriptions"
    )
    if items is None and isinstance(payload, list):
        items = payload
    if not isinstance(items, list):
        # Some suppliers return a single object rather than a list. Only treat
        # it as one description when it actually carries description content —
        # an empty/unrecognized envelope must yield [] (not a phantom record).
        if isinstance(payload, dict) and (
            _first_present(payload, "HotelId", "HotelID", "hotelId", "id") is not None
            or _first_present(payload, "Description", "description", "PropertyDescription", "Text")
        ):
            items = [payload]
        else:
            items = []
    # Real GetPropertyDescriptions shape (confirmed live): PropertyDescriptions is a
    # FLAT list of sections, each {ID, Name, Description} — where Name is the section
    # title ("Amenities", "Dining", "Location", ...) and Description is the body text.
    # It is NOT one record-per-hotel with nested Sections. So we fold the flat
    # sections into a single hotel record.
    sections: list[dict[str, str]] = []
    hotel_id: int | None = None
    for d in items:
        if not isinstance(d, dict):
            continue
        if hotel_id is None:
            hotel_id = _safe_int(_first_present(d, "HotelId", "HotelID", "hotelId"))
        title = str(_first_present(d, "Name", "Title", "Type", "name", "title") or "").strip()
        text = _strip_html(
            _first_present(d, "Description", "description", "Text", "text", "value")
        )
        if text:
            sections.append({"title": title, "text": text})
    combined = " ".join(s["text"] for s in sections).strip()
    record = {
        "hotel_id": hotel_id,
        "description": combined,
        "sections": sections,
    }
    out = [record] if sections else []
    return out[:max_results] if max_results else out


def parse_hotel_guest_review_response(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize GetHotelGuestReview into a single review-summary dict."""
    if not isinstance(raw, dict):
        raise HotelStaticNormalizationError("Expected dict response", missing_field="root")
    payload = _unwrap_result(raw)
    # Real GetHotelGuestReview shape (confirmed live): the review array comes back
    # as a top-level JSON list, which our http_client wraps as {"raw": [...]}.
    # Each item: {ReviewerName, Rating (str), ReviewText, StayDate, TravelCompanion,
    # TripReason, ReviewSubmitDate, ManagementResponses[], VerificationSource}.
    if isinstance(payload, list):
        reviews_raw = payload
    else:
        reviews_raw = (
            _first_present(payload, "Reviews", "GuestReviews", "reviews", "list", "raw") or []
        )
    reviews: list[dict[str, Any]] = []
    if isinstance(reviews_raw, list):
        for r in reviews_raw:
            if not isinstance(r, dict):
                continue
            reviews.append(
                {
                    "rating": _try_float(
                        _first_present(r, "Rating", "rating", "Score")
                    ) or 0.0,
                    "title": str(_first_present(r, "Title", "title") or "").strip(),
                    "comment": _strip_html(
                        _first_present(r, "ReviewText", "Comment", "Review", "comment", "Text")
                    ),
                    "reviewer": str(
                        _first_present(r, "ReviewerName", "GuestName", "reviewer", "Name") or ""
                    ).strip(),
                    "date": str(
                        _first_present(r, "StayDate", "ReviewDate", "ReviewSubmitDate", "Date", "date")
                        or ""
                    ),
                    "travel_companion": str(_first_present(r, "TravelCompanion") or "").strip(),
                    "source": str(_first_present(r, "VerificationSource") or "").strip(),
                }
            )
    # Compute average from the review ratings when there's no summary field
    ratings = [rv["rating"] for rv in reviews if rv["rating"] > 0]
    avg_from_reviews = round(sum(ratings) / len(ratings), 1) if ratings else 0.0
    summary_avg = _try_float(
        _first_present(payload, "AverageRating", "OverallRating", "averageRating")
        if isinstance(payload, dict) else None
    )
    return {
        "hotel_id": _safe_int(
            _first_present(payload, "HotelId", "HotelID", "hotelId")
            if isinstance(payload, dict) else None
        ),
        "average_rating": summary_avg or avg_from_reviews,
        "total_reviews": (
            int(_first_present(payload, "TotalReviews", "ReviewCount", "totalReviews", "Count") or 0)
            if isinstance(payload, dict) else 0
        ) or len(reviews),
        "reviews": reviews,
    }


def _try_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# =============================================================================
# === currency (rate of exchange)
# =============================================================================
def parse_currency_roe_response(raw: Any) -> dict[str, Any]:
    """Normalize an /api/Currency/ROE/{code} response.

    The live supplier shape (confirmed against staging) is a single record for
    the requested currency, carrying buying/selling rates:

        {"statusCode": 200, "result": {
            "currencyId": 2, "currencyCode": "INR",
            "buyingROE": 0.0388350711, "sellingROE": 26.2674940975}}

    Here `sellingROE` is INR-per-1-unit-of-the-base-supplier-currency (i.e. the
    AED→INR sell rate the customer is charged at), and `buyingROE` is its
    reciprocal (≈ 1/26.27). Returns a normalized dict:

        {"currency_code": "INR", "buying_roe": 0.0388, "selling_roe": 26.27,
         "rate": 26.27}

    `rate` is the customer-facing INR-per-base-unit figure (sellingROE, falling
    back to 1/buyingROE). Unparseable / empty responses yield {} so the caller
    can fall back to the manual FX rate rather than crash.
    """
    payload = _unwrap_result(raw)

    # Some shapes wrap the record in a list — take the first usable entry.
    if isinstance(payload, list):
        for entry in payload:
            parsed = parse_currency_roe_response(entry)
            if parsed:
                return parsed
        return {}

    if not isinstance(payload, dict):
        return {}

    selling = _try_float(
        _first_present(payload, "sellingROE", "SellingROE", "sellingRoe", "sellRate")
    )
    buying = _try_float(_first_present(payload, "buyingROE", "BuyingROE", "buyingRoe", "buyRate"))
    # Generic single-rate shapes ({"rate": ...} / {"roe": ...}) as a fallback.
    flat = _try_float(_first_present(payload, "rate", "Rate", "roe", "ROE", "exchangeRate", "value"))

    rate: float | None = None
    if selling is not None and selling > 0:
        rate = selling
    elif buying is not None and buying > 0:
        rate = 1.0 / buying
    elif flat is not None and flat > 0:
        rate = flat

    if rate is None:
        return {}

    code = _first_present(payload, "currencyCode", "CurrencyCode", "currency", "code")
    return {
        "currency_code": str(code).strip().upper() if code else None,
        "buying_roe": buying,
        "selling_roe": selling,
        "rate": rate,
    }
