"""Client packages report (October 2026): the fixes, one class per finding.

Every test pins a behaviour the report found wrong, using the supplier's own
data shapes, so a regression shows up as the exact claim the customer saw.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

# The visa block the supplier returns, trimmed to the fields the fixes read.
# Text is the supplier's own process-step article and cancellation policy,
# the same text gujjutours.com renders.
VISA_ARTICLE = (
    "<p>Applying for a Dubai tourist visa from India is straightforward.</p>"
    "<h3>Important — Please Read Before Paying</h3>"
    "<p>Dubai tourist visa fees are non-refundable once your application is submitted to "
    "UAE immigration authorities — regardless of the visa outcome. Please confirm your "
    "travel dates before completing payment.</p>"
    "<table><tr><th>Track</th><th>Processing Time</th><th>Extra Fee</th></tr>"
    "<tr><td>Normal</td><td>2–4 Working Days</td><td>None</td></tr>"
    "<tr><td>Express</td><td>24–48 Hours</td><td>Yes — additional charges apply</td></tr>"
    "</table><p>Processing time is counted from the working day your verified application "
    "is submitted — not from payment. UAE weekends (Sat–Sun) and public holidays are "
    "excluded. Apply at least 7–10 days before departure.</p>"
)
VISA_BLOCK = {
    "name": "Dubai Tourist Visa",
    "processSteps": [{"processStepDescription": VISA_ARTICLE}],
    "cancellationPolicies": [{
        "cpName": "Non-Refundable",
        "description": "Visa fees are <span>non-refundable</span> once the application has "
                       "been submitted for processing.",
        "isRefundable": False,
        "chargablePercentage": 100.0,
    }],
    "holidays": [{"name": "", "fromDate": "", "toDate": ""}],
    "options": [{
        "visaOptionId": 1,
        "visaOptionName": "30 Days Single Entry Tourist Visa",
        "visaRates": [
            {"processType": "Normal", "processingTime": "3-4 Working Days",
             "fareInfo": [{"paxType": "Adult", "price": 302.434681, "currency": "INR"}]},
            {"processType": "Express", "processingTime": "1 Working Day",
             "fareInfo": [{"paxType": "Adult", "price": 380.448468, "currency": "INR"}]},
        ],
    }],
}


# ======================================================================= visa
class TestVisaPolicy:
    def test_refund_policy_is_the_published_one(self) -> None:
        """Was: "a partial refund is likely". The site: non-refundable once
        submitted, regardless of the outcome."""
        from parsers import parse_visa_policy

        p = parse_visa_policy(VISA_BLOCK)
        assert p["refundable"] is False
        assert p["refund_policy"].startswith("Dubai tourist visa fees are non-refundable")
        assert "regardless of the visa outcome" in p["refund_policy"]
        assert "partial" not in p["refund_policy"].lower()

    def test_track_times_match_the_website(self) -> None:
        """Was: Express "1 Working Day" (the per-rate supplier field). The site
        says 24–48 Hours; Normal 2–4 Working Days."""
        from parsers import parse_visa_policy

        p = parse_visa_policy(VISA_BLOCK)
        assert p["track_times"] == {"Normal": "2–4 Working Days", "Express": "24–48 Hours"}
        assert "additional charges" in p["express_note"]
        assert p["apply_before_text"] == "Apply at least 7–10 days before departure."
        assert p["weekend_days"] == ["Sat", "Sun"]

    def test_fare_lines_use_the_website_times(self) -> None:
        from parsers import parse_visa_response

        with patch("parsers.convert_supplier_price", side_effect=lambda a, **k: (round(a * 25.6, 2), "INR")):
            opts = parse_visa_response({"result": {"visas": [VISA_BLOCK]}})
        lines = opts[0].fare_lines
        assert lines[0].startswith("Normal (2–4 Working Days)")
        assert lines[1].startswith("Express (24–48 Hours)")
        assert not any("1 Working Day" in line for line in lines)
        assert opts[0].processing_display == "2–4 Working Days"

    def test_price_kept_to_the_paisa(self) -> None:
        """₹15,252 vs the site's ₹15,510.60: whole-rupee rounding can never
        match a paisa figure. Visa fares are converted with places=2."""
        from parsers import parse_visa_response

        calls = []

        def fake(amount, **kw):
            calls.append(kw.get("places"))
            return (7755.30, "INR")

        with patch("parsers.convert_supplier_price", side_effect=fake):
            opts = parse_visa_response({"result": {"visas": [VISA_BLOCK]}})
        assert set(calls) == {2}
        assert opts[0].price_per_person_inr == 7755.30


class TestVisaTimeline:
    TRACKS = {"Normal": "2–4 Working Days", "Express": "24–48 Hours"}

    def _tl(self, travel: str, today: date):
        from mcp_tools.get_visa_info import visa_timeline

        return visa_timeline(
            travel, track_times=self.TRACKS, weekend_days=["Sat", "Sun"], holidays=[],
            apply_before_text="Apply at least 7–10 days before departure.",
            express_note="Express costs extra (additional charges apply).", today=today,
        )

    def test_report_case_four_days_out_recommends_express(self) -> None:
        """Asked on 28 Sep for a 2 Oct flight: was "yes, definitely ready" with no
        Express mention."""
        tl = self._tl("2026-10-02", date(2026, 9, 28))
        assert tl["recommended_track"] == "Express"
        assert "may not finish in time" in tl["note"]
        assert "Express (24–48 Hours) fits" in tl["note"]
        assert "7–10 days" in tl["note"]
        assert "cannot be guaranteed" in tl["note"]
        assert "definitely" not in tl["note"].lower()

    def test_plenty_of_time_is_normal(self) -> None:
        tl = self._tl("2026-12-20", date(2026, 10, 16))
        assert tl["recommended_track"] == "Normal"
        assert "Normal processing (2–4 Working Days) fits" in tl["note"]

    def test_weekend_is_not_a_working_day(self) -> None:
        # Fri 16 Oct -> Tue 20 Oct: Sat and Sun excluded, Mon 19 is the only
        # working day before departure.
        tl = self._tl("2026-10-20", date(2026, 10, 16))
        assert tl["working_days"] == 1
        assert tl["recommended_track"] == ""
        assert "Neither Normal" in tl["note"]

    def test_past_or_bad_date_gives_no_timeline(self) -> None:
        assert self._tl("2026-01-01", date(2026, 10, 16)) is None
        assert self._tl("not a date", date(2026, 10, 16)) is None


# ===================================================================== hotels
class TestHotelArea:
    @pytest.mark.parametrize(("address", "area", "district"), [
        ("Khalid Bin Al Waleed Road, Al Nahdha St Bur Dubai", "Bur Dubai", "Bur Dubai"),
        ("Al Muteena Street", "Al Muteena", "Deira"),
        ("Port Saeed Road, Deira", "Port Saeed", "Deira"),
        ("19th Street Oud Metha Area", "Oud Metha", "Bur Dubai"),
        ("Barsha Heights", "Barsha Heights", "Barsha Heights"),
    ])
    def test_area_from_the_supplier_address(self, address, area, district) -> None:
        from reference_data_loader import resolve_dubai_area

        assert resolve_dubai_area(address) == (area, district)

    def test_admiral_plaza_is_not_deira(self) -> None:
        """Was: "Al Karama... a district within the larger Deira/Bur Dubai region"."""
        from reference_data_loader import area_matches, resolve_dubai_area

        a, d = resolve_dubai_area("Khalid Bin Al Waleed Road, Al Nahdha St Bur Dubai")
        assert not area_matches("Deira", a, d)
        assert area_matches("Bur Dubai", a, d)

    def test_coordinates_when_the_address_names_no_area(self) -> None:
        from reference_data_loader import resolve_dubai_area

        # Marco Polo, Al Muteena Street, by coordinates alone.
        assert resolve_dubai_area("", 25.2735, 55.3262)[1] == "Deira"
        # Far outside every area: say nothing rather than guess.
        assert resolve_dubai_area("", 24.4539, 54.3773) == ("", "")

    def test_a_deira_request_accepts_every_deira_locality(self) -> None:
        from reference_data_loader import area_matches

        assert area_matches("Deira", "Al Rigga", "Deira")
        assert area_matches("deira", "Port Saeed", "Deira")
        assert not area_matches("Al Rigga", "Port Saeed", "Deira")
        assert area_matches("Marina", "Dubai Marina", "Dubai Marina")


# =================================================================== itinerary
class TestPlanMatchesThePackage:
    def _plan(self, **kw):
        from agent_tools import plan_itinerary_tool

        args = {"start_date": "2026-12-02", "nights": 6, "adults": 2,
                "flight_total_inr": 99365.87, "hotel_total_inr": 31285.96,
                "visa_per_adult_inr": 7755.30}
        args.update(kw)
        with patch("agent_tools._tour_facts_lookup", return_value={}), \
             patch("agent_tools._tour_price_lookup", return_value=0.0):
            return plan_itinerary_tool.invoke(args)

    def test_no_unrequested_tours_by_default(self) -> None:
        """Was: a Dhow Cruise nobody asked for appeared in the day plan."""
        out = self._plan(tours=[{"name": "Desert Safari", "price_per_adult_inr": 6693,
                                 "duration": "0-Days 6-Hours 0-Minutes"}])
        assert out["auto_filled_tours"] == []
        titles = {i["title"] for d in out["days"] for i in d["items"] if i["kind"] == "tour"}
        assert titles == {"Desert Safari"}

    def test_tour_lands_on_the_date_it_was_priced_for(self) -> None:
        """Was: the safari priced for 3 Oct was scheduled on Day 4."""
        out = self._plan(tours=[
            {"name": "Dubai Frame", "date": "2026-12-05", "price_per_adult_inr": 2508,
             "duration": "0-Days 2-Hours 0-Minutes"},
            {"name": "Desert Safari", "date": "2026-12-03", "price_per_adult_inr": 6693,
             "duration": "0-Days 6-Hours 0-Minutes"},
        ])
        on = {i["title"]: d["date"] for d in out["days"] for i in d["items"] if i["kind"] == "tour"}
        assert on == {"Desert Safari": "2026-12-03", "Dubai Frame": "2026-12-05"}
        lines = {line["item"]: line["date"] for line in out["package_lines"]}
        assert lines["Desert Safari"] == "2026-12-03"

    def test_unpriced_transfer_is_marked_not_included(self) -> None:
        """Was: airport transfers in the plan, missing from the price."""
        with patch("mcp_tools.search_transfers._impl", return_value={"options": []}):
            out = self._plan(arrival_time="06:20", hotel_name="Marco Polo Hotel")
        transfers = [i for d in out["days"] for i in d["items"] if i["kind"] == "transfer"]
        assert transfers and all("not included in the package price" in t["title"] for t in transfers)
        assert out["cost_breakdown"]["transfers"] == 0
        assert not any(line["item"].startswith("Airport transfer") for line in out["package_lines"])

    def test_package_lines_add_up_to_the_total(self) -> None:
        out = self._plan(tours=[{"name": "Desert Safari", "date": "2026-12-03",
                                 "price_per_adult_inr": 6693, "duration": "0-Days 6-Hours 0-Minutes"}])
        assert round(sum(line["amount_inr"] for line in out["package_lines"]), 2) == out["total_inr"]

    def test_flight_details_come_from_the_fare_shown(self) -> None:
        """Was: both legs "1h 55m", baggage "0kg", refundability missing."""
        shown = {
            "airline": "flydubai", "price_inr": 99365.87,
            "outbound": {"flight_numbers": ["FZ438"], "departure_time": "04:25", "arrival_time": "06:20"},
            "inbound": {"flight_numbers": ["FZ437"], "departure_time": "22:45", "arrival_time": "03:25"},
            "duration_display": "3h 25m", "duration_return_display": "3h 10m",
            "baggage_display": "30kg check-in + 7kg cabin", "is_refundable_label": "nonrefundable",
        }
        with patch("flight_cache.find_option", return_value=shown):
            out = self._plan(flight_total_inr=0.0, flight_fare_ref="abc123")
        f = out["flight"]
        assert f["outbound_duration"] == "3h 25m" and f["return_duration"] == "3h 10m"
        assert f["baggage"].startswith("30kg")
        assert f["refundable"] == "nonrefundable"
        assert out["cost_breakdown"]["flights"] == 99365.87

    def test_an_expired_fare_says_to_search_again(self) -> None:
        with patch("flight_cache.find_option", return_value=None):
            out = self._plan(flight_fare_ref="gone")
        assert "search_flights again" in out["flight"]["note"]


# ================================================================ cancellation
class TestCancellationBreakdown:
    def test_report_package_splits_exactly(self) -> None:
        """Was: lost ₹1,36,664.23 / refunded ₹28,639.00 (each off by ₹1)."""
        from rules import cancellation_breakdown

        out = cancellation_breakdown([
            {"label": "Flights", "amount_inr": 103049.37, "refund_pct": 0},
            {"label": "Hotel", "amount_inr": 28599.86, "refund_pct": 0},
            {"label": "Dubai Frame", "amount_inr": 5016.00, "refund_pct": 0},
            {"label": "Desert Safari", "amount_inr": 13386.00, "refund_pct": 100},
            {"label": "Visa", "amount_inr": 15252.00, "refund_pct": 100},
        ])
        assert out["lost_inr"] == 136665.23
        assert out["refunded_inr"] == 28638.00
        assert round(out["lost_inr"] + out["refunded_inr"], 2) == out["total_inr"] == 165303.23

    def test_partial_refund_and_bad_lines(self) -> None:
        from rules import cancellation_breakdown

        out = cancellation_breakdown([
            {"label": "Tour", "amount_inr": 1000.01, "refund_pct": 50},
            {"label": "junk", "amount_inr": "abc"},
            "not a line",
        ])
        assert len(out["lines"]) == 1
        assert out["refunded_inr"] + out["lost_inr"] == 1000.01

    def test_tool_is_registered(self) -> None:
        import agent_tools

        assert "cancellation_breakdown_tool" in {t.name for t in agent_tools._build_all_tools()}


# ===================================================================== prompt
def test_prompt_carries_the_new_rules() -> None:
    from agent import load_system_prompt

    p = load_system_prompt(surface="streamlit")
    for needle in ("refund_policy", "timeline.note", "search_hotels(area=", "flight_fare_ref",
                   "package_lines", "cancellation_breakdown_tool"):
        assert needle in p, needle


# ============================================== found in the live retest
class TestRetestFindings:
    @pytest.mark.parametrize(("given", "code"), [
        ("'Y'", "Y"), ('"C"', "C"), ("economy", "Y"), ("Economy class", "Y"),
        ("business", "C"), ("premium economy", "S"), ("first", "F"), ("", "Y"), (None, "Y"),
    ])
    def test_cabin_is_normalised(self, given, code) -> None:
        """The model sent cabin="'Y'" and the supplier silently returned no
        fares: "couldn't pull live flight prices from Ahmedabad"."""
        from mcp_tools.search_flights import normalize_cabin

        assert normalize_cabin(given) == code

    @pytest.mark.parametrize(("said", "exact"), [
        ("I want a 4-star hotel in Deira", True),
        ("a 4★ hotel please", True),
        ("4 star or above", False),
        ("at least 4 star", False),
        ("4+ star", False),
        ("a 5 star hotel", False),
    ])
    def test_a_four_star_ask_means_four_star(self, said, exact) -> None:
        """Asked for "a 4-star hotel", the model searched 4-and-up and led with
        5-star hotels."""
        from rules import exact_star_request, set_conversation_text

        set_conversation_text(said)
        assert exact_star_request(4) is exact

    def test_every_visa_processing_field_reads_like_the_website(self) -> None:
        """The reply quoted the supplier's "3-4 Working Days" from
        processing_time_text while the website says 2–4."""
        from parsers import parse_visa_response

        with patch("parsers.convert_supplier_price", side_effect=lambda a, **k: (round(a * 25.6, 2), "INR")):
            o = parse_visa_response({"result": {"visas": [VISA_BLOCK]}})[0]
        assert o.processing_time_text == "2–4 Working Days"
        assert {f.process_type: f.processing_time_text for f in o.fares} == {
            "Normal": "2–4 Working Days", "Express": "24–48 Hours"}

    def test_plan_refuses_to_guess_the_party_size(self) -> None:
        """Without `adults` the package was priced for one person."""
        from agent_tools import plan_itinerary_tool

        out = plan_itinerary_tool.invoke({"start_date": "2026-12-02", "nights": 6})
        assert out["error_type"] == "MissingPartySize"

    def test_no_transfer_is_added_unasked(self) -> None:
        """A transfer nobody asked for was self-priced into the total."""
        out = TestPlanMatchesThePackage()._plan(arrival_time="06:20", hotel_name="Crowne Plaza Dubai Deira")
        assert out["cost_breakdown"]["transfers"] == 0


class TestCancellationRefundFlag:
    def test_refund_flag_beats_an_ambiguous_percent(self) -> None:
        """The model sent refund_pct "1" meaning "refundable": refund 1%."""
        from rules import cancellation_breakdown

        out = cancellation_breakdown([
            {"label": "Safari", "amount_inr": 5350.66, "refund": "full"},
            {"label": "Hotel", "amount_inr": 50972.79, "refund": "none"},
            {"label": "Old caller", "amount_inr": 100, "refund_pct": "1"},
            {"label": "Half", "amount_inr": 1000, "refund": "partial", "refund_pct": 50},
        ])
        by = {line["label"]: line["refunded_inr"] for line in out["lines"]}
        assert by == {"Safari": 5350.66, "Hotel": 0.0, "Old caller": 100.0, "Half": 500.0}
        assert round(out["refunded_inr"] + out["lost_inr"], 2) == out["total_inr"]


class TestOnlyQuotedFiguresEnterThePackage:
    def test_a_tour_price_passed_as_a_transfer_is_dropped(self) -> None:
        """The model passed the Desert Safari's Rs 5,350.66 as transfer_total_inr."""
        from rules import set_current_thread

        set_current_thread("pkg-test-1")
        try:
            out = TestPlanMatchesThePackage()._plan(
                arrival_time="18:35", hotel_name="Avenue Hotel", transfer_total_inr=5350.66)
        finally:
            set_current_thread("")
        assert out["cost_breakdown"]["transfers"] == 0
        assert "ignored" in out["transfer_note"]

    def test_a_quoted_airport_transfer_is_kept(self) -> None:
        from mcp_tools.quote_registry import remember
        from rules import set_current_thread

        set_current_thread("pkg-test-2")
        try:
            remember("airport_transfer", [2536.72, 8200.76])
            out = TestPlanMatchesThePackage()._plan(
                arrival_time="18:35", hotel_name="Avenue Hotel", transfer_total_inr=2536.72)
        finally:
            set_current_thread("")
        assert out["cost_breakdown"]["transfers"] == 2536.72

    def test_quotes_never_cross_conversations(self) -> None:
        from mcp_tools.quote_registry import remember, was_quoted
        from rules import set_current_thread

        set_current_thread("customer-a")
        remember("airport_transfer", [1234.5])
        set_current_thread("customer-b")
        try:
            assert not was_quoted("airport_transfer", 1234.5)
        finally:
            set_current_thread("")


def test_check_in_is_never_before_landing() -> None:
    """"Check in 14:00" was printed under an 18:35 landing."""
    from scheduling import build_itinerary

    out = build_itinerary(start_date="2026-12-02", nights=6, arrival_time="18:35",
                          hotel_name="Avenue Hotel", hotel_checkin_time="14:00")
    day1 = out["days"][0]["items"]
    rest = next(i for i in day1 if i["title"] == "Check in and rest")
    assert rest["start"] >= "18:35"
