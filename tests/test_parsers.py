"""Tests for parsers.py — all 7 API response parsers.

Fixtures are auto-derived from real staging-API responses by running:
    python -m scripts.refresh_test_fixtures

These tests assert against actual API data shapes (the new Gujju Tours /
Technoheaven backend). Refresh fixtures whenever the API changes; tests
will surface drift immediately.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from parsers import (
    parse_flight_response,
    parse_hotel_response,
    parse_restaurant_response,
    parse_tour_response,
    parse_transfer_response,
    parse_visa_response,
)


# =============================================================================
# Flight
# =============================================================================
class TestFlightParser:
    """Real fixture contains 4 itineraries: bogus ₹270 INR DEL→BOM (Air India),
    USD ~$279 DEL→BOM IndiGo nonrefundable, USD ~$371 DEL→BOM Air India
    refundable, USD ~$279 DEL→NMI IndiGo nonrefundable.
    """

    @pytest.fixture
    def raw(self) -> dict:
        from tests.factories import load_fixture

        return load_fixture("flight_response.json")

    def test_floor_filter_drops_bogus_inr_prices(self, raw: dict) -> None:
        """Suspiciously low INR-labeled fares from the supplier must be dropped."""
        from parsers import FLIGHT_PRICE_INR_FLOOR

        options = parse_flight_response(raw)
        assert all(o.price_inr >= FLIGHT_PRICE_INR_FLOOR for o in options)

    def test_destination_filter_drops_wrong_route(self, raw: dict) -> None:
        """When asking for DEL→BOM, NMI (Saipan) results should not appear."""
        options = parse_flight_response(raw, expected_origin="DEL", expected_destination="BOM")
        for o in options:
            assert o.segments_outbound[-1].to_airport.upper() == "BOM"

    def test_sorted_by_price(self, raw: dict) -> None:
        options = parse_flight_response(raw)
        for a, b in pairwise(options):
            assert a.price_inr <= b.price_inr

    def test_segments_parsed(self, raw: dict) -> None:
        options = parse_flight_response(raw)
        assert options, "Expected at least one itinerary after filters"
        seg = options[0].segments_outbound[0]
        assert seg.from_airport == "DEL"
        assert seg.marketing_airline  # non-empty
        assert seg.flight_number  # non-empty

    def test_refundable_flag(self, raw: dict) -> None:
        """New API uses 'refundable'/'nonrefundable' labels (lowercase string).
        Old API used 'Yes'/'No'. Both should parse correctly.
        """
        options = parse_flight_response(raw, expected_origin="DEL", expected_destination="BOM")
        labels = [(o.airline, o.refundable) for o in options]
        # At least one nonrefundable and one refundable in the fixture
        assert any(not r for _, r in labels)
        assert any(r for _, r in labels)

    def test_currency_conversion_from_usd(self, raw: dict) -> None:
        """USD-priced itineraries should convert to realistic INR amounts."""
        options = parse_flight_response(raw)
        usd_opts = [o for o in options if o.currency_original == "USD"]
        assert usd_opts, "Expected at least one USD-priced itinerary"
        # $279 * 84 = ~₹23,400. Sanity check the conversion.
        for o in usd_opts:
            ratio = o.price_inr / o.price_original
            assert 70 <= ratio <= 100, f"FX ratio out of range: {ratio}"

    def test_max_results(self, raw: dict) -> None:
        options = parse_flight_response(raw, max_results=1)
        assert len(options) == 1

    def test_baggage(self, raw: dict) -> None:
        options = parse_flight_response(raw)
        assert options, "Expected at least one itinerary"
        # Baggage info is a list of strings (e.g. ['0pc', '0pc'] or ['7kg'])
        assert isinstance(options[0].baggage_info, list)

    def test_route_outbound(self, raw: dict) -> None:
        options = parse_flight_response(raw)
        assert options
        assert "DEL" in options[0].route_outbound

    def test_empty_response(self) -> None:
        assert parse_flight_response({"data": {"pricedItineraries": []}}) == []

    def test_malformed(self) -> None:
        from core import FlightNormalizationError

        with pytest.raises(FlightNormalizationError):
            parse_flight_response("not a dict")  # type: ignore[arg-type]


# =============================================================================
# Hotel
# =============================================================================
class TestHotelParser:
    """Real fixture: Rove Downtown (id 509) + Citymax Bur Dubai (id 206),
    both with multiple HotelOptions and a mix of refundable rooms.
    """

    @pytest.fixture
    def raw(self) -> dict:
        from tests.factories import load_fixture

        return load_fixture("hotel_response.json")

    def test_basic_count(self, raw: dict) -> None:
        options = parse_hotel_response(raw, nights=2)
        assert len(options) == 2

    def test_sorted_by_price(self, raw: dict) -> None:
        options = parse_hotel_response(raw, nights=2)
        assert options[0].price_inr <= options[1].price_inr

    def test_per_night_calculated(self, raw: dict) -> None:
        options = parse_hotel_response(raw, nights=2)
        cheapest = options[0]
        assert cheapest.per_night_inr == round(cheapest.price_inr / 2, 2)

    def test_hotel_names_resolved(self, raw: dict) -> None:
        options = parse_hotel_response(
            raw,
            nights=2,
            hotel_names={"206": "Citymax Bur Dubai", "509": "Rove Downtown"},
        )
        names = {o.hotel_name for o in options}
        assert "Citymax Bur Dubai" in names
        assert "Rove Downtown" in names

    def test_default_name_when_unknown(self, raw: dict) -> None:
        options = parse_hotel_response(raw, nights=2)
        names = {o.hotel_name for o in options}
        assert "Hotel 206" in names or "Hotel 509" in names

    def test_at_least_one_has_free_cancellation(self, raw: dict) -> None:
        """At least one hotel option in the fixture must offer free cancellation."""
        options = parse_hotel_response(raw, nights=2)
        assert any(o.has_free_cancellation for o in options)

    def test_empty(self) -> None:
        assert parse_hotel_response({"AvailabilityRS": {"HotelResult": []}}, nights=2) == []


# =============================================================================
# Tour
# =============================================================================
class TestTourParser:
    """Real fixture: 5 tours, 4 with matching rates (priced) + 1 without (filtered)."""

    @pytest.fixture
    def list_raw(self) -> dict:
        from tests.factories import load_fixture

        return load_fixture("tour_list_response.json")

    @pytest.fixture
    def rate_raw(self) -> dict:
        from tests.factories import load_fixture

        return load_fixture("tour_rate_response.json")

    def test_filters_out_tours_without_rates(self, list_raw: dict, rate_raw: dict) -> None:
        """The 1 unpriced tour in the fixture must be dropped, leaving 4 priced."""
        options = parse_tour_response(list_raw, rate_raw)
        assert len(options) == 4
        assert all(o.price_per_adult_inr > 0 for o in options)

    def test_sorted_by_price(self, list_raw: dict, rate_raw: dict) -> None:
        options = parse_tour_response(list_raw, rate_raw)
        for a, b in pairwise(options):
            assert a.price_per_adult_inr <= b.price_per_adult_inr

    def test_currency_converted_from_aed(self, list_raw: dict, rate_raw: dict) -> None:
        """Tours come in AED; should convert to INR at AED rate (~23x)."""
        options = parse_tour_response(list_raw, rate_raw)
        assert options
        for o in options:
            ratio = o.price_per_adult_inr / o.price_original
            assert 20 <= ratio <= 26, f"AED→INR ratio off: {ratio}"

    def test_max_results(self, list_raw: dict, rate_raw: dict) -> None:
        options = parse_tour_response(list_raw, rate_raw, max_results=2)
        assert len(options) == 2

    def test_tour_id_and_name_present(self, list_raw: dict, rate_raw: dict) -> None:
        options = parse_tour_response(list_raw, rate_raw)
        for o in options:
            assert o.tour_id > 0
            assert o.name  # non-empty


# =============================================================================
# Transfer
# =============================================================================
class TestTransferParser:
    """Real fixture: empty result (the Postman coords didn't match any
    available transfer at the time of sampling). Tests empty-case handling.
    """

    @pytest.fixture
    def raw(self) -> dict:
        from tests.factories import load_fixture

        return load_fixture("transfer_response.json")

    def test_empty_case_returns_empty_list(self, raw: dict) -> None:
        """Body statusCode 404 with empty result must produce []."""
        assert parse_transfer_response(raw) == []

    def test_synthetic_transfer_parses(self) -> None:
        """Sanity-test against a hand-crafted minimal valid response."""
        raw = {
            "statusCode": 200,
            "error": None,
            "result": [
                {
                    "transferID": "T1",
                    "vehicleName": "Toyota Camry",
                    "vehicleType": "Sedan",
                    "transferType": "Private Transfer",
                    "capacity": 3,
                    "luggageCapacity": 3,
                    "totalPrice": 100.0,
                    "currencyCode": "AED",
                    "estimatedTime": "30 mins",
                }
            ],
        }
        options = parse_transfer_response(raw)
        assert len(options) == 1
        o = options[0]
        assert o.vehicle_name == "Toyota Camry"
        assert "Private" in o.badges
        assert "Sedan" in o.badges
        assert o.price_inr == pytest.approx(2300.0, rel=0.01)


# =============================================================================
# Restaurant
# =============================================================================
class TestRestaurantParser:
    """Real fixture: 3 Dubai restaurants — Atithi Veg (Italian), Rangoli (Indian),
    Kathiyawadi Thali (Gujarati)."""

    @pytest.fixture
    def raw(self) -> dict:
        from tests.factories import load_fixture

        return load_fixture("restaurant_response.json")

    def test_basic_count(self, raw: dict) -> None:
        options = parse_restaurant_response(raw)
        assert len(options) == 3

    def test_sorted_by_price(self, raw: dict) -> None:
        options = parse_restaurant_response(raw)
        for a, b in pairwise(options):
            assert a.price_per_adult_inr <= b.price_per_adult_inr

    def test_veg_type_present(self, raw: dict) -> None:
        """All sampled restaurants are vegetarian."""
        options = parse_restaurant_response(raw)
        veg_types = {o.veg_type for o in options}
        assert all("Vegetarian" in vt for vt in veg_types)

    def test_cuisine_extracted(self, raw: dict) -> None:
        options = parse_restaurant_response(raw)
        cuisines = {o.cuisine for o in options}
        # Real fixture has 'Italian', 'Indian Main Course', 'Kathiyawadi Gujarati'
        assert "Italian" in cuisines or any("Indian" in c for c in cuisines)

    def test_currency_converted_from_aed(self, raw: dict) -> None:
        """Restaurant prices come in AED; convert at ~23x."""
        options = parse_restaurant_response(raw)
        for o in options:
            if o.price_original > 0:
                ratio = o.price_per_adult_inr / o.price_original
                assert 20 <= ratio <= 26


# =============================================================================
# Visa
# =============================================================================
class TestVisaParser:
    """Real fixture: 1 visa type (UAE Tourist Visa) with 4 nested sub-options
    (30-day single/multi, 60-day single/multi). All have pricing_available=False
    because the agent's pricing isn't enabled.
    """

    @pytest.fixture
    def raw(self) -> dict:
        from tests.factories import load_fixture

        return load_fixture("visa_response.json")

    def test_nested_options_flattened(self, raw: dict) -> None:
        """visas[0].options[*] should produce 4 separate VisaOption rows."""
        options = parse_visa_response(raw)
        assert len(options) == 4

    def test_option_ids_distinct(self, raw: dict) -> None:
        options = parse_visa_response(raw)
        ids = {o.visa_id for o in options}
        assert len(ids) == 4

    def test_entry_types_extracted(self, raw: dict) -> None:
        options = parse_visa_response(raw)
        entry_types = {o.entry_type for o in options}
        assert "Single" in entry_types
        assert "Multiple" in entry_types

    def test_stay_durations_extracted(self, raw: dict) -> None:
        options = parse_visa_response(raw)
        stays = {o.stay_duration for o in options}
        # New API field is `stayPeriod`. Real values are '30 Days', '60 Days'.
        assert "30 Days" in stays
        assert "60 Days" in stays

    def test_validity_periods_extracted(self, raw: dict) -> None:
        options = parse_visa_response(raw)
        for o in options:
            assert "Days" in o.validity  # e.g. "58 Days From Date Of Issue"

    def test_processing_days_extracted(self, raw: dict) -> None:
        """processingTime is now a string like '3-4 Working Days' — must parse the first int."""
        options = parse_visa_response(raw)
        for o in options:
            assert o.processing_days >= 1

    def test_pricing_unavailable_for_disabled_agent(self, raw: dict) -> None:
        """Sample agent doesn't have pricing enabled, so all options are 'On Request'."""
        options = parse_visa_response(raw)
        for o in options:
            assert o.pricing_available is False
            assert o.price_display == "On Request"

    def test_required_documents_collected(self, raw: dict) -> None:
        options = parse_visa_response(raw)
        for o in options:
            assert len(o.document_requirements) >= 1

    def test_legacy_flat_shape_still_parsed(self) -> None:
        """Back-compat: old shape `result.visaOptions[*]` should still work."""
        legacy = {
            "result": {
                "visaOptions": [
                    {
                        "visaId": 99,
                        "visaTypeName": "Old-Style Visa",
                        "validity": "30 days",
                        "stayDuration": "30 days",
                        "processingDays": 4,
                        "entryType": "Single",
                        "isEvisa": True,
                        "visaRates": [{"amount": 350, "currency": "AED"}],
                    }
                ],
            },
        }
        options = parse_visa_response(legacy)
        assert len(options) == 1
        assert options[0].visa_id == 99
        assert options[0].pricing_available is True