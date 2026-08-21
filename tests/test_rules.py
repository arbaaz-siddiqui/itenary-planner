"""Tests for rules.py — pricing rules, policies, budget logic."""

from __future__ import annotations

import pytest

from rules import (
    BUDGET_HANDOFF_THRESHOLD_INR,
    BUDGET_SCOPE_ALL_INCLUSIVE,
    BUDGET_SCOPE_EXCLUDES_FLIGHTS,
    BUDGET_SCOPE_EXCLUDES_FLIGHTS_AND_HOTEL,
    GROUP_SIZE_HANDOFF_THRESHOLD,
    add_selection,
    apply_agency_markup,
    apply_child_discount,
    apply_group_discount,
    apply_gst,
    apply_infant_pricing,
    apply_peak_season_surcharge,
    apply_tourism_dirham,
    compute_floor_price,
    compute_hotel_block_cost,
    compute_refund,
    compute_remaining_budget,
    floor_for_scope,
    gst_breakdown,
    is_budget_feasible,
    is_peak_date,
    price_group,
    remove_selection,
    resolve_party,
    should_hand_off,
    suggest_restaurants,
    suggest_uae_airport,
    sum_trip_total,
    total_spent,
)


# =============================================================================
# Pricing rules
# =============================================================================
class TestChildDiscount:
    def test_infant_zero(self) -> None:
        assert apply_child_discount(10000, age=1) == 0.0

    def test_young_child_half(self) -> None:
        assert apply_child_discount(10000, age=4) == 5000.0

    def test_older_child_75pct(self) -> None:
        assert apply_child_discount(10000, age=8) == 7500.0

    def test_teen_full_fare(self) -> None:
        assert apply_child_discount(10000, age=13) == 10000.0

    def test_negative_age_zero(self) -> None:
        assert apply_child_discount(10000, age=-1) == 0.0


class TestInfantPricing:
    def test_zero_default(self) -> None:
        assert apply_infant_pricing(36000) == 0.0


class TestGroupDiscount:
    def test_small_group_no_discount(self) -> None:
        assert apply_group_discount(100000, group_size=5) == 100000.0

    def test_10pax_5pct(self) -> None:
        assert apply_group_discount(100000, group_size=10) == 95000.0

    def test_20pax_8pct(self) -> None:
        assert apply_group_discount(100000, group_size=20) == 92000.0


class TestPeakSeason:
    @pytest.mark.parametrize("month", ["11", "12", "01", "02"])
    def test_peak_months(self, month: str) -> None:
        assert is_peak_date(f"2026-{month}-15") is True

    @pytest.mark.parametrize("month", ["03", "06", "08", "10"])
    def test_off_peak_months(self, month: str) -> None:
        assert is_peak_date(f"2026-{month}-15") is False

    def test_surcharge_applies_in_peak(self) -> None:
        assert apply_peak_season_surcharge(10000, "2026-12-15") == 11500.0

    def test_no_surcharge_off_peak(self) -> None:
        assert apply_peak_season_surcharge(10000, "2026-06-15") == 10000.0


class TestAgencyMarkup:
    def test_default(self) -> None:
        assert apply_agency_markup(10000, "default") == 11200.0

    def test_flight_8pct(self) -> None:
        assert apply_agency_markup(10000, "flight") == 10800.0

    def test_tour_20pct(self) -> None:
        assert apply_agency_markup(10000, "tour") == 12000.0


class TestGst:
    def test_apply_5pct(self) -> None:
        assert apply_gst(10000) == 10500.0

    def test_breakdown(self) -> None:
        b = gst_breakdown(10000)
        assert b["subtotal"] == 10000.0
        assert b["gst"] == 500.0
        assert b["total"] == 10500.0


class TestTourismDirham:
    def test_5_star_4_nights_1_room(self) -> None:
        # 20 AED/night * 4 nights * 1 room * 23 INR/AED = 1840
        assert apply_tourism_dirham(4, 1, 5) == 1840.0

    def test_zero_nights(self) -> None:
        assert apply_tourism_dirham(0, 1, 5) == 0.0


# =============================================================================
# Policies
# =============================================================================
class TestCancellation:
    def test_45_days_80pct_refund(self) -> None:
        result = compute_refund(100000, "2026-07-19", today_iso="2026-05-25")
        assert result["refund_percent"] == 80.0
        assert result["refund_amount"] == 80000.0

    def test_30_days_50pct(self) -> None:
        result = compute_refund(100000, "2026-06-25", today_iso="2026-05-25")
        assert result["refund_percent"] == 50.0

    def test_15_days_25pct(self) -> None:
        result = compute_refund(100000, "2026-06-10", today_iso="2026-05-25")
        assert result["refund_percent"] == 25.0

    def test_7_days_10pct(self) -> None:
        result = compute_refund(100000, "2026-06-02", today_iso="2026-05-25")
        assert result["refund_percent"] == 10.0

    def test_past_date_zero_refund(self) -> None:
        result = compute_refund(100000, "2026-05-26", today_iso="2026-05-25")
        assert result["refund_percent"] == 0.0


class TestAirportRouting:
    def test_default_dxb(self) -> None:
        assert suggest_uae_airport(budget_inr=100000) == "DXB"

    def test_low_budget_shj(self) -> None:
        assert suggest_uae_airport(budget_inr=20000) == "SHJ"

    def test_area_overrides_budget(self) -> None:
        assert suggest_uae_airport(budget_inr=100000, stay_area="Abu Dhabi") == "AUH"
        assert suggest_uae_airport(budget_inr=100000, stay_area="Sharjah") == "SHJ"


class TestFoodSuggestions:
    def test_jain(self) -> None:
        assert suggest_restaurants("Jain") != []

    def test_swaminarayan(self) -> None:
        assert suggest_restaurants("swaminarayan") != []

    def test_halal(self) -> None:
        assert suggest_restaurants("halal") != []

    def test_no_diet(self) -> None:
        assert suggest_restaurants("") == []


class TestHandoff:
    def test_no_handoff_normal(self) -> None:
        should, _ = should_hand_off(group_size=2, budget_inr=50000, user_message="hi")
        assert should is False

    def test_large_group(self) -> None:
        should, reason = should_hand_off(group_size=GROUP_SIZE_HANDOFF_THRESHOLD + 1)
        assert should is True
        assert "Group size" in reason

    def test_high_budget(self) -> None:
        should, reason = should_hand_off(budget_inr=BUDGET_HANDOFF_THRESHOLD_INR + 1)
        assert should is True
        assert "Budget" in reason

    def test_complaint_keyword(self) -> None:
        should, reason = should_hand_off(user_message="I have a complaint")
        assert should is True
        assert "complaint" in reason

    def test_refund_keyword(self) -> None:
        should, _ = should_hand_off(user_message="I want a refund")
        assert should is True


# =============================================================================
# Budget
# =============================================================================
class TestBudget:
    def test_floor_basic(self) -> None:
        f = compute_floor_price(
            cheapest_flight_inr=30000,
            cheapest_hotel_inr=20000,
            visa_inr=5000,
            transfer_inr=2000,
        )
        # 57000 * 1.05 = 59850
        assert f == 59850.0

    def test_feasible(self) -> None:
        assert is_budget_feasible(budget_inr=100000, floor_inr=80000) is True
        assert is_budget_feasible(budget_inr=70000, floor_inr=80000) is False

    def test_add_selection_replaces(self) -> None:
        from tests.factories import make_selection

        s1 = make_selection(component="flight", item_id="A", price_inr=30000)
        s2 = make_selection(component="flight", item_id="B", price_inr=40000)
        result = add_selection([s1], s2, replace_existing_component=True)
        assert len(result) == 1
        assert result[0].item_id == "B"

    def test_add_selection_keeps_other_components(self) -> None:
        from tests.factories import make_selection

        flight = make_selection(component="flight", item_id="F1", price_inr=30000)
        hotel = make_selection(component="hotel", item_id="H1", price_inr=20000)
        result = add_selection([flight], hotel)
        assert len(result) == 2

    def test_remove_selection(self) -> None:
        from tests.factories import make_selection

        s = make_selection(component="flight", item_id="A")
        result = remove_selection([s], component="flight")
        assert result == []

    def test_total_spent(self) -> None:
        from tests.factories import make_selection

        sels = [
            make_selection(component="flight", price_inr=30000),
            make_selection(component="hotel", price_inr=20000),
        ]
        assert total_spent(sels) == 50000.0

    def test_compute_remaining(self) -> None:
        from tests.factories import make_budget, make_selection

        budget = make_budget(total=100000)
        sels = [make_selection(component="flight", price_inr=30000)]
        assert compute_remaining_budget(budget, sels) == 70000.0


# =============================================================================
# Cancellation — supplier-driven (client wanted hardcoded tiers removed)
# =============================================================================
class TestSupplierCancellation:
    def test_real_hotel_policy_with_free_window(self) -> None:
        """Mirrors the actual hotel API shape we receive."""
        from rules import parse_supplier_cancellation_terms

        raw = [
            {
                "FromDate": "05-26-2026",
                "ToDate": "11-12-2026",
                "CancellationPrice": 0,
                "daysBeforeCheckIn": None,
                "isNRF": False,
            },
            {
                "FromDate": "11-13-2026",
                "ToDate": "11-20-2026",
                "CancellationPrice": 584.9,
                "daysBeforeCheckIn": None,
                "isNRF": False,
            },
        ]
        terms, free_until = parse_supplier_cancellation_terms(raw)
        assert len(terms) == 2
        assert terms[0].is_free_cancellation is True
        assert terms[1].is_free_cancellation is False
        # Free until 2026-11-12 (the to_date of the free window, converted to ISO)
        assert free_until == "2026-11-12"

    def test_nrf_room_has_no_free_window(self) -> None:
        from rules import parse_supplier_cancellation_terms

        raw = [
            {
                "FromDate": "05-26-2026",
                "ToDate": "11-20-2026",
                "CancellationPrice": 526.4,
                "daysBeforeCheckIn": None,
                "isNRF": True,
            },
        ]
        terms, free_until = parse_supplier_cancellation_terms(raw)
        assert terms[0].is_nrf is True
        assert terms[0].is_free_cancellation is False
        assert free_until is None

    def test_empty_input(self) -> None:
        from rules import parse_supplier_cancellation_terms

        terms, free_until = parse_supplier_cancellation_terms(None)
        assert terms == []
        assert free_until is None


# =============================================================================
# Payment schedule — dynamic based on travel date + cancellation cutoff
# =============================================================================
class TestPaymentSchedule:
    def test_more_than_120_days_two_installments(self) -> None:
        """Travel 200 days out → 20% deposit, balance later."""
        from rules import compute_payment_schedule

        sched = compute_payment_schedule(
            total_inr=100000,
            travel_date_iso="2027-12-15",
            today_iso="2027-05-01",
        )
        assert sched.bucket == ">120 days"
        assert len(sched.installments) == 2
        assert sched.installments[0].label == "Deposit today"
        assert sched.installments[0].amount_inr == 20000.0
        assert sched.installments[1].amount_inr == 80000.0

    def test_30_to_120_days_higher_deposit(self) -> None:
        from rules import compute_payment_schedule

        sched = compute_payment_schedule(
            total_inr=100000,
            travel_date_iso="2026-09-01",
            today_iso="2026-07-01",  # 62 days out
        )
        assert sched.bucket == "30-120 days"
        assert sched.installments[0].amount_inr == 50000.0
        assert sched.installments[1].amount_inr == 50000.0

    def test_within_30_days_full_payment(self) -> None:
        from rules import compute_payment_schedule

        sched = compute_payment_schedule(
            total_inr=100000,
            travel_date_iso="2026-06-15",
            today_iso="2026-06-01",  # 14 days
        )
        assert sched.bucket == "<30 days"
        assert len(sched.installments) == 1
        assert sched.installments[0].label == "Full payment"
        assert sched.installments[0].amount_inr == 100000.0

    def test_customer_cutoff_subtracts_buffer(self) -> None:
        """Supplier free-cancel until 2026-09-30, buffer 3 days → customer deadline 2026-09-27."""
        from rules import compute_payment_schedule

        sched = compute_payment_schedule(
            total_inr=50000,
            travel_date_iso="2026-10-15",
            cancellation_cutoff_iso="2026-09-30",
            today_iso="2026-06-01",
        )
        assert sched.customer_payment_cutoff_iso == "2026-09-27"


# =============================================================================
# TCS — Indian Tax Collected at Source (Section 206C(1G))
# =============================================================================
class TestTcs:
    def test_overseas_package_20pct(self) -> None:
        from rules import compute_tcs

        tcs = compute_tcs(100000, is_overseas_tour_package=True)
        assert tcs.applicable is True
        assert tcs.rate_pct == 20.0
        assert tcs.amount_inr == 20000.0
        assert "PAN Card" in tcs.required_documents

    def test_non_package_below_threshold(self) -> None:
        from rules import compute_tcs

        tcs = compute_tcs(500000, is_overseas_tour_package=False)
        assert tcs.applicable is False

    def test_non_package_above_threshold_taxes_only_excess(self) -> None:
        """₹10L total, ₹7L threshold → 5% on ₹3L = ₹15,000."""
        from rules import compute_tcs

        tcs = compute_tcs(1_000_000, is_overseas_tour_package=False)
        assert tcs.applicable is True
        assert tcs.rate_pct == 5.0
        assert tcs.amount_inr == 15_000.0


# =============================================================================
# Customer payment summary — the sales-focused aggregator
# =============================================================================
class TestCustomerSummary:
    def test_includes_emi_options(self) -> None:
        from rules import compose_customer_payment_summary

        summary = compose_customer_payment_summary(
            total_inr_inclusive=120_000,
            travel_date_iso="2027-01-15",
            today_iso="2026-08-01",  # 167 days away
        )
        assert summary.emi_starting_inr_per_month == 10_000.0  # 120k / 12 months
        assert 3 in summary.emi_tenures_available
        assert 12 in summary.emi_tenures_available

    def test_international_requires_pan(self) -> None:
        from rules import compose_customer_payment_summary

        summary = compose_customer_payment_summary(
            total_inr_inclusive=150_000,
            travel_date_iso="2027-01-15",
            today_iso="2026-08-01",
            is_international=True,
        )
        assert "PAN Card" in summary.compliance_documents_required

    def test_no_per_component_breakdown_exposed(self) -> None:
        """Per client direction: customer view has total + schedule, NOT line items."""
        from rules import compose_customer_payment_summary

        summary = compose_customer_payment_summary(
            total_inr_inclusive=100_000,
            travel_date_iso="2027-01-15",
            today_iso="2026-08-01",
        )
        # The model must not have any per-component cost fields
        assert not hasattr(summary, "hotel_cost")
        assert not hasattr(summary, "flight_cost")
        assert not hasattr(summary, "gst_amount")
        # It has only the inclusive total and the schedule
        assert summary.total_inr_inclusive == 100_000


# =============================================================================
# Currency conversion sanity tests — addresses client concern that
# AED/USD prices should reach the customer as INR
# =============================================================================
class TestCurrencyConversion:
    def test_usd_to_inr_flight(self) -> None:
        """Flight comes in USD; parser must convert to realistic INR (~84x)."""
        import json
        from pathlib import Path

        from parsers import parse_flight_response

        raw = json.loads((Path(__file__).parent / "fixtures" / "flight_response.json").read_text())
        opts = parse_flight_response(raw)
        usd_opts = [o for o in opts if o.currency_original == "USD"]
        assert usd_opts, "Expected USD-priced itineraries in fixture"
        for o in usd_opts:
            ratio = o.price_inr / o.price_original
            assert 70 <= ratio <= 100, f"USD→INR ratio off: {ratio}"

    def test_aed_to_inr_restaurant(self) -> None:
        """Restaurant prices come in AED; must reach customer as INR (~23x)."""
        import json
        from pathlib import Path

        from parsers import parse_restaurant_response

        raw = json.loads(
            (Path(__file__).parent / "fixtures" / "restaurant_response.json").read_text()
        )
        opts = parse_restaurant_response(raw)
        for o in opts:
            if o.price_original > 0 and o.currency_original == "AED":
                ratio = o.price_per_adult_inr / o.price_original
                assert 20 <= ratio <= 26, f"AED→INR ratio off: {ratio}"

    def test_aed_to_inr_tour(self) -> None:
        """Tours priced in AED → INR."""
        import json
        from pathlib import Path

        from parsers import parse_tour_response

        list_raw = json.loads(
            (Path(__file__).parent / "fixtures" / "tour_list_response.json").read_text()
        )
        rate_raw = json.loads(
            (Path(__file__).parent / "fixtures" / "tour_rate_response.json").read_text()
        )
        opts = parse_tour_response(list_raw, rate_raw)
        for o in opts:
            assert o.currency_original == "AED"
            ratio = o.price_per_adult_inr / o.price_original
        # Upper bound is 30, not 26: AED/INR is a LIVE rate (26.3 as of
        # Aug 2026) and the old 26 ceiling failed on a normal market move.
            assert 20 <= ratio <= 30


# =============================================================================
# Party resolution — the "6 people, 2 kids" bug
# =============================================================================
class TestResolveParty:
    def test_total_minus_children_is_adults(self) -> None:
        """'6 people, 2 children' = 4 adults + 2 children — NOT 6 adults + 2."""
        r = resolve_party(total_people=6, children=2, child_ages=[5, 7])
        assert r["adults"] == 4
        assert r["children"] == 2
        assert r["party_total"] == 6

    def test_summary_reads_back_the_split(self) -> None:
        r = resolve_party(total_people=6, children=2, child_ages=[5, 7])
        assert "4 adults" in r["summary"]
        assert "2 children" in r["summary"]
        assert "6 travellers" in r["summary"]

    def test_explicit_adults_count(self) -> None:
        r = resolve_party(adults=4, children=2, child_ages=[5, 7])
        assert r["adults"] == 4
        assert r["party_total"] == 6

    def test_infants_counted_separately(self) -> None:
        r = resolve_party(total_people=4, children=2, child_ages=[1, 6])
        assert r["infants"] == 1
        assert "infant" in r["summary"]

    def test_all_adults(self) -> None:
        r = resolve_party(total_people=3)
        assert r["adults"] == 3
        assert r["children"] == 0

    def test_children_exceed_total_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_party(total_people=2, children=3)

    def test_total_mismatch_with_explicit_adults_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_party(total_people=6, adults=6, children=2)

    def test_zero_adults_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_party(total_people=2, children=2, child_ages=[5, 7])

    def test_ages_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_party(total_people=6, children=2, child_ages=[5])


# =============================================================================
# Per-adult → group total (with child discounts)
# =============================================================================
class TestPriceGroup:
    def test_adults_only(self) -> None:
        r = price_group(per_adult_inr=2500, adults=4)
        assert r["group_total_inr"] == 10000.0

    def test_applies_child_discounts(self) -> None:
        """4 adults + child(5)@50% + child(7)@75% at ₹2500/adult."""
        r = price_group(per_adult_inr=2500, adults=4, children=2, child_ages=[5, 7])
        # 4*2500 + 1250 + 1875 = 13125
        assert r["adults_subtotal_inr"] == 10000.0
        assert r["children_subtotal_inr"] == 3125.0
        assert r["group_total_inr"] == 13125.0

    def test_missing_ages_charges_full_fare_and_flags(self) -> None:
        r = price_group(per_adult_inr=2500, adults=2, children=1)
        assert r["group_total_inr"] == 7500.0
        assert r["ages_assumed_full_fare"] is True

    def test_negative_price_raises(self) -> None:
        with pytest.raises(ValueError):
            price_group(per_adult_inr=-1, adults=1)


# =============================================================================
# Hotel room-block cost
# =============================================================================
class TestHotelBlockCost:
    def test_three_rooms_three_nights(self) -> None:
        r = compute_hotel_block_cost(per_room_per_night_inr=3727.56, rooms=3, nights=3)
        # 3727.56 * 3 = 11182.68 per room; * 3 rooms = 33548.04
        assert r["per_room_total_inr"] == 11182.68
        assert r["block_total_inr"] == 33548.04

    def test_zero_rooms_raises(self) -> None:
        with pytest.raises(ValueError):
            compute_hotel_block_cost(per_room_per_night_inr=1000, rooms=0, nights=3)

    def test_zero_nights_raises(self) -> None:
        with pytest.raises(ValueError):
            compute_hotel_block_cost(per_room_per_night_inr=1000, rooms=1, nights=0)


# =============================================================================
# Line-item trip total
# =============================================================================
class TestSumTripTotal:
    def test_sums_components(self) -> None:
        r = sum_trip_total(
            [
                {"label": "Flights", "amount_inr": 224934},
                {"label": "Hotel", "amount_inr": 33548},
            ]
        )
        assert r["total_inr"] == 258482.0
        assert len(r["line_items"]) == 2

    def test_empty_is_zero(self) -> None:
        assert sum_trip_total([])["total_inr"] == 0.0

    def test_ignores_malformed_amounts(self) -> None:
        r = sum_trip_total([{"label": "X", "amount_inr": "oops"}, {"label": "Y", "amount_inr": 100}])
        assert r["total_inr"] == 100.0


# =============================================================================
# Budget scope → floor
# =============================================================================
class TestFloorForScope:
    def test_all_inclusive_includes_everything(self) -> None:
        floor = floor_for_scope(
            cheapest_flight_inr=224934,
            cheapest_hotel_inr=33548,
            visa_inr=0,
            transfer_inr=0,
            budget_scope=BUDGET_SCOPE_ALL_INCLUSIVE,
        )
        # (224934 + 33548) * 1.05 = 271406.1
        assert floor == 271406.1

    def test_excludes_flights_drops_flight(self) -> None:
        floor = floor_for_scope(
            cheapest_flight_inr=224934,
            cheapest_hotel_inr=33548,
            visa_inr=0,
            transfer_inr=0,
            budget_scope=BUDGET_SCOPE_EXCLUDES_FLIGHTS,
        )
        # 33548 * 1.05 = 35225.4
        assert floor == 35225.4

    def test_excludes_flights_and_hotel(self) -> None:
        floor = floor_for_scope(
            cheapest_flight_inr=224934,
            cheapest_hotel_inr=33548,
            visa_inr=5000,
            transfer_inr=2000,
            budget_scope=BUDGET_SCOPE_EXCLUDES_FLIGHTS_AND_HOTEL,
        )
        # (5000 + 2000) * 1.05 = 7350.0
        assert floor == 7350.0

    def test_unknown_scope_falls_back_to_all_inclusive(self) -> None:
        floor = floor_for_scope(
            cheapest_flight_inr=100000,
            cheapest_hotel_inr=20000,
            visa_inr=0,
            transfer_inr=0,
            budget_scope="garbage",
        )
        assert floor == round(120000 * 1.05, 2)
