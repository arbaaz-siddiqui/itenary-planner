"""Flight answers checked against the client's QA report (October 2026).

The report compared the chatbot's AMD-DXB answers with the gujjutours.com site.
Every test here pins one finding, on frozen real supplier responses:

  tests/fixtures/flight_amd_dxb_economy_roundtrip.json   (CabinPreference Y)
  tests/fixtures/flight_amd_dxb_business_roundtrip.json  (CabinPreference C)

Both are trimmed copies of live AMD-DXB round-trip searches for 2 adults and
contain the report's own flights: FZ 438/437 and AI 2546 + FZ 442 / FZ 437, each
sold by two providers.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

import flight_cache
import parsers
from mcp_tools import get_flight_details as details_mod
from mcp_tools import search_flights as sf
from parsers import (
    flight_cabin_code,
    flight_layovers,
    flight_leg_duration_min,
    parse_flight_response,
)

FIXTURES = Path(__file__).parent / "fixtures"
ECONOMY = FIXTURES / "flight_amd_dxb_economy_roundtrip.json"
BUSINESS = FIXTURES / "flight_amd_dxb_business_roundtrip.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _flights(segments: list[Any]) -> str:
    return "+".join(f"{s.marketing_airline_code}{s.flight_number}" for s in segments)


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """No FX network call, empty cache."""
    monkeypatch.setattr(parsers, "live_rate_map",
                        lambda: {"INR": 1.0, "USD": 84.0, "AED": 23.0})
    flight_cache._CACHE.clear()
    yield
    flight_cache._CACHE.clear()


@pytest.fixture
def economy() -> list[Any]:
    return parse_flight_response(_load(ECONOMY), expected_origin="AMD",
                                 expected_destination="DXB", cabin="Y")


_ANY = object()


def _find(options: list[Any], out: str, provider: Any = _ANY) -> Any:
    """First option flying `out`; provider "" is the public fare (providerName null)."""
    hits = [o for o in options if _flights(o.segments_outbound) == out
            and (provider is _ANY or o.provider == provider)]
    assert hits, f"{out} not in fixture"
    return hits[0]


# =============================================================================
# Issue 1 — every duration was short by exactly 1h 30m
# =============================================================================
class TestDurationIncludesTimeZones:
    def test_fz438_matches_the_site(self, economy: list[Any]) -> None:
        """Site: FZ 438 takes 3h 25m. Clock subtraction gave the bot's 1h 55m."""
        fz = _find(economy, "FZ438")
        assert fz.duration_min == 205
        assert fz.duration_return_min == 155

    def test_one_stop_includes_the_layover(self, economy: list[Any]) -> None:
        """Site: AI 2546 + FZ 442 takes 8h 45m (1h 45m + 3h 25m stop + 3h 35m)."""
        combo = _find(economy, "AI2546+FZ442")
        assert combo.duration_min == 525

    @pytest.mark.parametrize("path", [ECONOMY, BUSINESS])
    def test_every_leg_equals_the_supplier_elapsed_time(self, path: Path) -> None:
        """Flying time + connections, leg by leg, across both fixtures."""
        raw = _load(path)
        options = parse_flight_response(raw)
        by_code = {o.fare_source_code: o for o in options}
        checked = 0
        for item in raw["data"]["pricedItineraries"]:
            option = by_code.get(item["airItineraryPricingInfo"]["fareSourceCode"])
            if option is None:
                continue
            legs = item["originDestinationOptions"]
            for parsed, leg in zip((option.duration_min, option.duration_return_min), legs):
                segs = leg["flightSegments"]
                expected = (sum(s["journeyDuration"] for s in segs)
                            + sum(s["layoverTimeInMinute"] or 0 for s in segs))
                assert parsed == expected
                checked += 1
        assert checked >= 10

    def test_layover_is_measured_at_the_stop(self, economy: list[Any]) -> None:
        """The supplier files the layover on the segment ARRIVING at the stop;
        reading it from the next segment showed no layovers at all."""
        combo = _find(economy, "AI2546+FZ442")
        assert flight_layovers(combo.segments_outbound) == [("DEL", 205)]
        assert combo.segments_outbound[0].layover_min == 205

    def test_missing_flying_time_is_unknown_not_guessed(self) -> None:
        segs = [{"duration_min": 0, "departure": "2026-10-20 04:25:00",
                 "arrival": "2026-10-20 06:20:00"}]
        assert flight_leg_duration_min(segs) == 0
        assert flight_leg_duration_min([]) == 0

    def test_dict_and_model_segments_agree(self, economy: list[Any]) -> None:
        combo = _find(economy, "AI2546+FZ442")
        dumped = [s.model_dump() for s in combo.segments_outbound]
        assert flight_leg_duration_min(dumped) == flight_leg_duration_min(combo.segments_outbound)
        assert flight_layovers(dumped) == flight_layovers(combo.segments_outbound)

    def test_leg_summary_carries_duration_and_layover(self, economy: list[Any]) -> None:
        combo = sf._to_dict(_find(economy, "AI2546+FZ442"), 2)
        assert combo["outbound"]["duration_display"] == "8h 45m"
        assert combo["outbound"]["layovers"] == ["DEL 3h 25m"]
        assert combo["outbound"]["stops"] == 1
        assert combo["inbound"]["duration_display"] == "2h 35m"
        assert combo["duration_return_display"] == "2h 35m"


# =============================================================================
# Issue 3 — the business answer was the economy answer
# =============================================================================
class TestCabin:
    def test_business_search_drops_economy_rows(self) -> None:
        """The supplier's business search returned economy itineraries too."""
        raw = _load(BUSINESS)
        everything = parse_flight_response(raw)
        business = parse_flight_response(raw, cabin="C")
        assert len(everything) == 10
        assert len(business) == 5
        assert all(o.cabin == "C" for o in business)
        cheapest_economy = min(o.price_inr for o in everything if o.cabin == "Y")
        assert min(o.price_inr for o in business) > cheapest_economy

    @pytest.mark.parametrize("text,code", [
        ("ECONOMY", "Y"), ("Economy Standard", "Y"), ("Economy Premium", "Y"),
        ("Premium Economy", "S"), ("Business", "C"), ("BUSINESS", "C"),
        ("First", "F"), ("", ""), (None, ""), ("Q", ""),
    ])
    def test_cabin_text_is_normalised(self, text: Any, code: str) -> None:
        assert flight_cabin_code(text) == code

    def test_unstated_cabin_only_passes_economy(self, economy: list[Any]) -> None:
        unknown = economy[0].model_copy(update={"cabin": ""})
        assert parsers._meets_cabin(unknown, "Y")
        assert not parsers._meets_cabin(unknown, "C")

    def test_cache_does_not_serve_economy_to_business(self) -> None:
        flight_cache.set_entry("AMD", "DXB", "2026-11-02", "2026-11-08", 2, 0,
                               options=[{"price_inr": 1}], providers_done=["AI"],
                               providers_pending=[], search_params={}, cabin="Y")
        assert flight_cache.get_entry("AMD", "DXB", "2026-11-02", "2026-11-08", 2, 0,
                                      cabin="Y") is not None
        assert flight_cache.get_entry("AMD", "DXB", "2026-11-02", "2026-11-08", 2, 0,
                                      cabin="C") is None
        assert flight_cache.get_entry("AMD", "DXB", "2026-11-02", "2026-11-08", 2, 0,
                                      cabin="Y", infants=1) is None


# =============================================================================
# Issue 4 — airline name, refundability, baggage
# =============================================================================
class TestAirlineAndBaggage:
    def test_every_carrier_is_named(self, economy: list[Any]) -> None:
        assert _find(economy, "AI2546+FZ442").airline == "Air India Limited + Flydubai"
        assert _find(economy, "FZ438").airline == "Flydubai"

    def test_refundable_is_per_fare(self, economy: list[Any]) -> None:
        assert _find(economy, "FZ438", "").refundable is True
        assert _find(economy, "FZ438", "Techno_Flight").refundable is False

    def test_zero_kg_leg_is_not_confirmed_never_zero(self, economy: list[Any]) -> None:
        """One provider lists FZ 437 (the return) as 0kg; the bot quoted it."""
        techno = sf._to_dict(_find(economy, "FZ438", "Techno_Flight"), 2)
        assert techno["outbound"]["checked_baggage"] == "30kg"
        assert techno["inbound"]["checked_baggage"] == "not confirmed"
        assert not re.search(r"(?<!\d)0\s*kg", techno["baggage_display"])
        assert techno["baggage_display"] == (
            "Outbound: 30kg check-in + 1 piece cabin · "
            "Return: check-in not confirmed + 1 piece cabin")

    def test_same_allowance_both_ways_reads_once(self, economy: list[Any]) -> None:
        public = sf._to_dict(_find(economy, "FZ438", ""), 2)
        assert public["baggage_display"] == "30kg check-in + 7kg cabin"

    @pytest.mark.parametrize("raw,shown", [
        ("30 kg", "30kg"), ("30KG", "30kg"), ("1 piece", "1 piece"), ("2PC", "2 pieces"),
        ("0kg", ""), ("SB", ""), ("", ""), (None, ""),
    ])
    def test_bag_text(self, raw: Any, shown: str) -> None:
        assert sf._bag_text(raw) == shown

    @pytest.mark.parametrize("raw,zero", [("0kg", True), ("0 kg", True), ("0pc", True),
                                          ("0", True), ("30kg", False), ("SB", False)])
    def test_zero_bag(self, raw: str, zero: bool) -> None:
        assert sf._is_zero_bag(raw) is zero

    def test_airline_filter_matches_any_flight(self, economy: list[Any]) -> None:
        combo = sf._to_dict(_find(economy, "AI2546+FZ442"), 2)
        assert sf._matches_airline(combo, "FZ")
        assert sf._matches_airline(combo, "AI")
        assert not sf._matches_airline(combo, "EK")


# =============================================================================
# Issue 5 — the same flight listed twice
# =============================================================================
class TestOneRowPerItinerary:
    def test_two_providers_collapse_to_the_cheaper(self, economy: list[Any]) -> None:
        rows = [sf._to_dict(o, 2) for o in economy]
        shown = sf._diversify(rows, 20)
        keys = [sf._itinerary_key(o) for o in shown]
        assert len(keys) == len(set(keys))
        fz = [o for o in shown if o["outbound"]["flight_numbers"] == ["438"]]
        assert len(fz) == 1
        assert fz[0]["price_inr"] == min(
            o.price_inr for o in economy if _flights(o.segments_outbound) == "FZ438")

    def test_different_flights_at_the_same_price_both_stay(self, economy: list[Any]) -> None:
        a = sf._to_dict(_find(economy, "FZ438", ""), 2)
        b = sf._to_dict(_find(economy, "AI2546+FZ442", ""), 2)
        b = {**b, "price_inr": a["price_inr"], "airline": a["airline"]}
        assert len(sf._diversify([a, b], 5)) == 2


# =============================================================================
# Issue 2 — prices: the supplier's own figures
# =============================================================================
class TestPrice:
    def test_price_is_the_supplier_total(self, economy: list[Any]) -> None:
        """The chatbot quotes the fare unchanged. gujjutours.com shows exactly
        5% more by design: its token (IsFromApi = false) carries a tariff-wise
        markup that the chatbot's API token (IsFromApi = true) does not —
        confirmed by the supplier, 2026-10-15."""
        raw = _load(ECONOMY)
        totals = {i["airItineraryPricingInfo"]["fareSourceCode"]:
                  float(i["airItineraryPricingInfo"]["itinTotalFare"]["totalFare"]["amount"])
                  for i in raw["data"]["pricedItineraries"]}
        for o in economy:
            assert o.price_inr == pytest.approx(totals[o.fare_source_code], abs=0.01)

    def test_conversation_id_is_kept(self, economy: list[Any]) -> None:
        assert all(o.conversation_id == _load(ECONOMY)["conversationId"] for o in economy)


# =============================================================================
# Dates, party size, infants
# =============================================================================
class _Today(date):
    fixed = date(2026, 10, 13)

    @classmethod
    def today(cls) -> date:  # type: ignore[override]
        return cls.fixed


class TestDatesAndParty:
    def test_past_trip_moves_as_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Forwarding each date alone put the return a year before the departure."""
        monkeypatch.setattr(sf, "date", _Today)
        dep, ret, adjusted = sf._forward_trip_dates("2026-10-10", "2026-10-16")
        assert (dep, ret) == ("2027-10-10", "2027-10-16")
        assert adjusted == {
            "requested": {"departure_date": "2026-10-10", "return_date": "2026-10-16"},
            "searched": {"departure_date": "2027-10-10", "return_date": "2027-10-16"},
        }

    def test_future_trip_is_untouched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sf, "date", _Today)
        assert sf._forward_trip_dates("2026-10-20", "2026-10-26") == ("2026-10-20", "2026-10-26", None)
        assert sf._forward_trip_dates("2026-10-20", None) == ("2026-10-20", None, None)

    def test_leap_day_return(self) -> None:
        assert sf._shift_years("2028-02-29", 1) == "2029-02-28"

    @pytest.mark.parametrize("adults,children,infants,error", [
        (10, 0, 0, None),               # searched as two bookings
        (7, 1, 1, None),
        (8, 1, 2, None),
        (36, 0, 0, None),
        (37, 0, 0, "GroupBookingRequired"),
        (1, 0, 2, "TooManyInfants"),
        (2, 2, 2, None),
    ])
    def test_party_limits(self, adults: int, children: int, infants: int, error: str | None) -> None:
        result = sf._validate_party(adults, children, infants)
        assert (result or {}).get("error_type") == error

    def test_supplier_rejection_text_is_read(self) -> None:
        raw = {"success": False, "error": [{"code": "INVALID_REQUEST",
                                            "description": "Maximum 9 Passengers are allowed."}]}
        assert sf._supplier_error(raw) == "Maximum 9 Passengers are allowed."
        assert sf._supplier_error({"success": True, "error": []}) == ""

    def test_payload_sends_infants_as_inf(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from booking_api import endpoints

        sent: dict[str, Any] = {}

        class _Client:
            def post(self, path: str, json: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
                sent.update(json)
                return {"success": True, "data": {"pricedItineraries": []}}

        monkeypatch.setattr(endpoints, "get_client", lambda: _Client())
        endpoints.call_flight_search(origin_iata="AMD", destination_iata="DXB",
                                     departure_date="2026-10-20", adults=2,
                                     infants=1, infant_ages=[0])
        assert {"Code": "INF", "Quantity": 1} in sent["PassengerTypeQuantities"]
        assert sent["infantAge"] == "0"
        endpoints.call_flight_search(origin_iata="AMD", destination_iata="DXB",
                                     departure_date="2026-10-20", adults=2)
        assert sent["infantAge"] == ""
        assert all(p["Code"] != "INF" for p in sent["PassengerTypeQuantities"])


# =============================================================================
# The search tool end to end, against a fake supplier
# =============================================================================
class _Supplier:
    """Answers the all-airlines pass from a fixture; single codes from `by_code`."""

    def __init__(self, everything: dict[str, Any], by_code: dict[str, Any] | None = None,
                 delay: dict[str, float] | None = None) -> None:
        self.everything = everything
        self.by_code = by_code or {}
        self.delay = delay or {}
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        code = kw.get("airline_code") or ""
        time.sleep(self.delay.get(code, 0))
        if code == "":
            return self.everything
        return self.by_code.get(code, {"success": True, "data": {"pricedItineraries": []}})


ROUTE = dict(origin_city="Ahmedabad", destination_city="Dubai",
             departure_date="2026-10-20", return_date="2026-10-26", adults=2)


@pytest.fixture
def supplier(monkeypatch: pytest.MonkeyPatch) -> _Supplier:
    fake = _Supplier(_load(ECONOMY))
    monkeypatch.setattr(sf, "call_flight_search", fake)
    monkeypatch.setattr(sf, "date", _Today)
    return fake


class TestSearchTool:
    def test_answers_from_the_complete_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A slow provider with the cheapest fare must still be in the answer."""
        economy = _load(ECONOMY)
        cheap_only = json.loads(json.dumps(economy))
        cheap = cheap_only["data"]["pricedItineraries"][0]
        cheap["airItineraryPricingInfo"]["fareSourceCode"] = "CHEAP-FROM-SLOW-PROVIDER"
        cheap["airItineraryPricingInfo"]["itinTotalFare"]["totalFare"]["amount"] = 20000
        cheap_only["data"]["pricedItineraries"] = [cheap]
        fake = _Supplier(economy, {"QR": cheap_only}, delay={"QR": 0.5})
        monkeypatch.setattr(sf, "call_flight_search", fake)
        monkeypatch.setattr(sf, "date", _Today)
        out = sf._impl(**ROUTE)
        assert out["search_complete"] is True
        assert out["cheapest_price_inr"] == 20000

    def test_a_provider_past_the_deadline_lands_in_the_cache(
            self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _Supplier(_load(ECONOMY), delay={"": 0.6})
        monkeypatch.setattr(sf, "call_flight_search", fake)
        monkeypatch.setattr(sf, "date", _Today)
        monkeypatch.setattr(sf, "_SEARCH_DEADLINE_S", 0.2)
        out = sf._impl(**ROUTE)
        assert out["search_complete"] is False
        assert "all-airlines" in out["providers_still_loading"]
        assert "complete list" in out["agent_instructions"]
        time.sleep(0.8)
        entry = flight_cache.get_entry("AMD", "DXB", "2026-10-20", "2026-10-26", 2, 0)
        assert entry is not None and entry.all_options

    def test_business_after_economy_is_not_the_economy_answer(
            self, monkeypatch: pytest.MonkeyPatch, supplier: _Supplier) -> None:
        economy = sf._impl(**ROUTE, cabin="Y")
        assert economy["options"]
        supplier.everything = _load(BUSINESS)
        business = sf._impl(**ROUTE, cabin="C")
        assert business["from_cache"] is False
        assert business["options"]
        assert all(o["cabin"] == "C" for o in business["options"])

    def test_no_business_fares_says_so(self, supplier: _Supplier) -> None:
        out = sf._impl(**ROUTE, cabin="C")  # economy-only supplier answer
        assert out["options"] == []
        assert "No business fares" in out["agent_instructions"]

    def test_force_refresh_asks_the_supplier_again(self, supplier: _Supplier) -> None:
        sf._impl(**ROUTE)
        first = len(supplier.calls)
        cached = sf._impl(**ROUTE)
        assert cached["from_cache"] is True and len(supplier.calls) == first
        fresh = sf._impl(**ROUTE, force_refresh=True)
        assert fresh["from_cache"] is False and len(supplier.calls) == 2 * first

    def test_both_paths_hand_the_model_the_same_slim_rows(self, supplier: _Supplier) -> None:
        for out in (sf._impl(**ROUTE), sf._impl(**ROUTE)):
            for o in out["options"]:
                for dropped in sf._MODEL_DROP_FIELDS:
                    assert dropped not in o
                assert len(o["fare_ref"]) == 12
                assert o["outbound"]["duration_display"]

    def test_child_under_two_flies_as_infant(self, supplier: _Supplier) -> None:
        sf._impl(**ROUTE, children=2, child_ages=[1, 8])
        sent = supplier.calls[0]
        assert (sent["children"], sent["infants"]) == (1, 1)
        assert sent["child_ages"] == [8] and sent["infant_ages"] == [1]

    def test_party_over_nine_is_searched_as_bookings(self, supplier: _Supplier) -> None:
        """10 adults: the supplier is asked for 5 adults (twice the same booking,
        so once), and every option is the sum of both bookings."""
        out = sf._impl(**{**ROUTE, "adults": 10})
        assert not out.get("error")
        assert {c["adults"] for c in supplier.calls} == {5}
        assert all(c["adults"] + c["children"] + c["infants"] <= 9 for c in supplier.calls)
        assert out["priced_for"] == "10 adults, as 2 bookings (5 adults; 5 adults)"
        for o in out["options"]:
            assert len(o["bookings"]) == 2
            assert o["price_inr"] == pytest.approx(sum(b["price_inr"] for b in o["bookings"]), abs=0.05)
            assert o["pax_count"] == 10

    def test_a_group_desk_party_never_reaches_the_supplier(self, supplier: _Supplier) -> None:
        out = sf._impl(**{**ROUTE, "adults": 40})
        assert out["error_type"] == "GroupBookingRequired"
        assert supplier.calls == []

    def test_supplier_rejection_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rejected = {"success": False, "error": [{"code": "INVALID_REQUEST",
                                                 "description": "Maximum 9 Passengers are allowed."}]}
        monkeypatch.setattr(sf, "call_flight_search", lambda **kw: rejected)
        monkeypatch.setattr(sf, "date", _Today)
        out = sf._impl(**ROUTE)
        assert out["options"] == []
        assert out["supplier_errors"] == ["Maximum 9 Passengers are allowed."]
        assert "rejected" in out["agent_instructions"]

    def test_past_dates_are_reported(self, supplier: _Supplier) -> None:
        out = sf._impl(**{**ROUTE, "departure_date": "2026-10-10", "return_date": "2026-10-16"})
        assert out["dates_adjusted"]["searched"] == {"departure_date": "2027-10-10",
                                                     "return_date": "2027-10-16"}
        assert "2027-10-10" in out["agent_instructions"]
        assert supplier.calls[0]["return_date"] == "2027-10-16"

    def test_return_before_departure_is_refused(self, supplier: _Supplier) -> None:
        out = sf._impl(**{**ROUTE, "departure_date": "2026-10-26", "return_date": "2026-10-20"})
        assert out["error_type"] == "InvalidDates"
        assert supplier.calls == []


# =============================================================================
# Flight details — resolvable from fare_ref, tied to its search
# =============================================================================
class TestFlightDetails:
    def test_fare_ref_resolves_and_carries_the_conversation(
            self, monkeypatch: pytest.MonkeyPatch, supplier: _Supplier) -> None:
        out = sf._impl(**ROUTE)
        ref = out["options"][0]["fare_ref"]
        sent: dict[str, Any] = {}

        def _details(**kw: Any) -> dict[str, Any]:
            sent.update(kw)
            raw = _load(ECONOMY)
            raw["data"]["pricedItineraries"] = [
                i for i in raw["data"]["pricedItineraries"]
                if i["airItineraryPricingInfo"]["fareSourceCode"] == kw["fare_source_code"]]
            return raw

        monkeypatch.setattr(details_mod, "call_flight_details", _details)
        result = details_mod._impl(ref)
        assert sent["conversation_id"] == _load(ECONOMY)["conversationId"]
        assert len(sent["fare_source_code"]) > 12
        assert result["option"]["fare_ref"] == ref
        assert "penalties" in result["option"]

    def test_unknown_ref_never_calls_the_supplier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called: list[Any] = []
        monkeypatch.setattr(details_mod, "call_flight_details", lambda **kw: called.append(kw))
        result = details_mod._impl("deadbeef0000")
        assert result["error_type"] == "FareExpired"
        assert called == []

    def test_fare_ref_is_a_digest_not_a_prefix(self) -> None:
        a, b = "MSQkMCQkQW1hZGV1cyQkAAAA", "MSQkMCQkQW1hZGV1cyQkBBBB"
        assert a[:12] == b[:12]
        assert flight_cache.fare_ref_for(a) != flight_cache.fare_ref_for(b)
        assert flight_cache.fare_ref_for("") == ""


# =============================================================================
# The "One moment" stall, and guards on the streaming path
# =============================================================================
class TestTurnGuards:
    def test_promise_without_a_search_is_detected(self) -> None:
        import agent

        stalled = {"messages": [HumanMessage(content="business class please"),
                                AIMessage(content="One moment while I pull those premium options for you.")]}
        assert agent._is_unfulfilled_promise(stalled)

    @pytest.mark.parametrize("reply", [
        "Let me know if you'd like hotels too.",
        "Which date would you like to fly?",
    ])
    def test_ordinary_replies_are_not_promises(self, reply: str) -> None:
        import agent

        assert not agent._is_unfulfilled_promise(
            {"messages": [HumanMessage(content="hi"), AIMessage(content=reply)]})

    def test_a_turn_that_showed_results_is_not_a_promise(self) -> None:
        import agent

        turn = {"messages": [
            HumanMessage(content="flights"),
            AIMessage(content="", tool_calls=[{"name": "search_flights", "args": {}, "id": "c1"}]),
            ToolMessage(content='{"options": [{"price_inr": 1}]}', tool_call_id="c1", name="search_flights"),
            AIMessage(content="Here are the flights. Let me check one more thing for you."),
        ]}
        assert not agent._is_unfulfilled_promise(turn)

    def test_an_empty_search_ending_on_a_promise_is_one(self) -> None:
        import agent

        turn = {"messages": [
            HumanMessage(content="flights"),
            AIMessage(content="", tool_calls=[{"name": "resolve_party_tool", "args": {}, "id": "c1"}]),
            ToolMessage(content='{"adults": 9}', tool_call_id="c1", name="resolve_party_tool"),
            AIMessage(content="I'll search for a group of 9 first. One moment while I pull those options for you."),
        ]}
        assert agent._is_unfulfilled_promise(turn)

    def test_a_promise_mid_reply_is_not_the_ending(self) -> None:
        import agent

        turn = {"messages": [HumanMessage(content="hi"), AIMessage(
            content="Let me check: you want 20-26 October for 2 adults. Is that right?")]}
        assert not agent._is_unfulfilled_promise(turn)

    def test_only_the_latest_turn_counts(self) -> None:
        import agent

        thread = {"messages": [
            HumanMessage(content="flights"),
            AIMessage(content="", tool_calls=[{"name": "search_flights", "args": {}, "id": "c1"}]),
            ToolMessage(content='{"options": []}', tool_call_id="c1", name="search_flights"),
            AIMessage(content="Here are the flights."),
            HumanMessage(content="business?"),
            AIMessage(content="One moment, let me check business fares."),
        ]}
        assert agent._is_unfulfilled_promise(thread)

    def test_recovery_nudges_the_model_to_act(self) -> None:
        import agent

        sent: list[str] = []

        class _Agent:
            def invoke(self, payload: dict[str, Any], _cfg: Any) -> dict[str, Any]:
                sent.append(payload["messages"][0]["content"])
                return {"messages": [
                    HumanMessage(content=sent[-1]),
                    AIMessage(content="", tool_calls=[{"name": "search_flights", "args": {}, "id": "c9"}]),
                    ToolMessage(content='{"options": [{"price_inr": 1}]}', tool_call_id="c9",
                                name="search_flights"),
                    AIMessage(content="Here are the business fares.")]}

        stalled = {"messages": [HumanMessage(content="business"),
                                AIMessage(content="One moment while I pull those premium options.")]}
        out = agent._recover_empty_turn(_Agent(), stalled, {}, surface="chat", thread_id="t")
        assert sent == [agent._ACT_NOW_MARKER]
        assert agent._final_reply(out) == "Here are the business fares."

    def test_streaming_path_runs_the_reply_guards(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invented fares after a refusal were only caught on the Streamlit path."""
        import agent
        from rules import needs_input_error

        invented = "| Airline | Price |\n|---|---|\n| Flydubai | ₹51,000 |"
        refusal = json.dumps(needs_input_error(["origin_city"]))

        class _Agent:
            def stream(self, _payload: Any, _cfg: Any, stream_mode: Any) -> Any:
                yield "updates", {"agent": {"messages": [AIMessage(
                    content="", tool_calls=[{"name": "search_flights", "args": {}, "id": "c1"}])]}}
                yield "updates", {"tools": {"messages": [ToolMessage(
                    content=refusal, tool_call_id="c1", name="search_flights")]}}
                yield "messages", (AIMessageChunk(content=invented), {"langgraph_node": "agent"})
                yield "updates", {"agent": {"messages": [AIMessage(content=invented)]}}

        monkeypatch.setattr(agent, "log_turn", lambda **kw: None)
        holder = agent.StreamResult()
        streamed = "".join(agent.stream_and_log(_Agent(), surface="chat", thread_id="t",
                                                user_message="flights to dubai", result=holder))
        assert "₹51,000" in streamed            # it did stream...
        assert "₹51,000" not in holder.text     # ...but the stored reply is corrected
        assert "which city you are flying from" in holder.text


# =============================================================================
# The follow-up that stalled: "business class for the same trip"
# =============================================================================
class TestFollowUpKeepsTheOrigin:
    class _Thread:
        def __init__(self, messages: list[Any]) -> None:
            self.messages = messages

        def get_state(self, _cfg: Any) -> Any:
            class _State:
                values = {"messages": self.messages}
            return _State()

    def test_earlier_messages_count_as_said(self) -> None:
        import agent

        thread = self._Thread([
            HumanMessage(content="Economy flights from Ahmedabad to Dubai, 20-26 Oct, 2 adults"),
            AIMessage(content="Here are the options."),
            HumanMessage(content="[system] The previous turn produced no reply."),
        ])
        words = agent._customer_words(thread, {}, "Now business class for the same trip")
        assert "Ahmedabad" in words
        assert "[system]" not in words
        assert words.endswith("Now business class for the same trip")

    def test_no_history_is_just_this_message(self) -> None:
        import agent

        assert agent._customer_words(object(), {}, "hello") == "hello"

    def test_follow_up_search_is_not_refused(self, supplier: _Supplier) -> None:
        from rules import set_conversation_text

        set_conversation_text("Economy flights from Ahmedabad to Dubai\nNow business class for the same trip")
        out = sf._impl(**ROUTE, cabin="Y")
        assert out.get("error_type") != "NeedsCustomerInput"
        set_conversation_text("Now business class for the same trip")
        out = sf._impl(**ROUTE, cabin="Y")
        assert out.get("error_type") == "NeedsCustomerInput"  # the guard still guards
        set_conversation_text("")

    def test_latest_named_city_is_recovered(self) -> None:
        from rules import set_conversation_text

        set_conversation_text("we live in mumbai\nactually we fly from ahmedabad")
        assert sf._origin_from_conversation("Dubai") == "Ahmedabad"
        set_conversation_text("")


class TestChildAgesAreMandatory:
    def test_children_without_ages_are_refused(self, supplier: _Supplier) -> None:
        out = sf._impl(**ROUTE, children=1)
        assert out["error_type"] == "NeedsCustomerInput"
        assert out["missing_fields"] == ["child_ages"]
        assert supplier.calls == []

    def test_ages_that_do_not_match_the_count_are_refused(self, supplier: _Supplier) -> None:
        out = sf._impl(**ROUTE, children=2, child_ages=[5])
        assert out["missing_fields"] == ["child_ages"]

    def test_declined_ages_search_as_children(self, supplier: _Supplier) -> None:
        out = sf._impl(**ROUTE, children=1, assume_missing=True)
        assert not out.get("error")
        assert (supplier.calls[0]["children"], supplier.calls[0]["infants"]) == (1, 0)


class TestPricedFor:
    @pytest.mark.parametrize("params,label", [
        ({"adults": 2}, "2 adults"),
        ({"adults": 1, "children": 1}, "1 adult + 1 child"),
        ({"adults": 2, "children": 2, "infants": 1}, "2 adults + 2 children + 1 infant"),
    ])
    def test_label(self, params: dict[str, int], label: str) -> None:
        assert sf._priced_for(params) == label

    def test_result_carries_the_party_it_priced(self, supplier: _Supplier) -> None:
        out = sf._impl(**ROUTE, children=1, child_ages=[1])
        assert out["priced_for"] == "2 adults + 1 infant"
        cached = sf._impl(**ROUTE, children=1, child_ages=[1])
        assert cached["from_cache"] is True and cached["priced_for"] == "2 adults + 1 infant"


class TestPromiseAfterARefusal:
    def test_refused_search_then_promise_is_unfulfilled(self) -> None:
        """10 adults: refused at 9, then "One moment while I pull those options"."""
        import agent

        refusal = json.dumps(sf._validate_party(10, 0, 0))
        turn = {"messages": [
            HumanMessage(content="flights for 10 adults"),
            AIMessage(content="", tool_calls=[{"name": "search_flights", "args": {}, "id": "c1"}]),
            ToolMessage(content=refusal, tool_call_id="c1", name="search_flights"),
            AIMessage(content="I'll search for a group of 9 first. One moment while I pull those options."),
        ]}
        assert agent._is_unfulfilled_promise(turn)

    def test_real_results_then_a_closing_line_is_fine(self) -> None:
        import agent

        turn = {"messages": [
            HumanMessage(content="flights"),
            AIMessage(content="", tool_calls=[{"name": "search_flights", "args": {}, "id": "c1"}]),
            ToolMessage(content='{"options": [{"price_inr": 1}]}', tool_call_id="c1", name="search_flights"),
            AIMessage(content="Here they are. Let me check hotels next if you like."),
        ]}
        assert not agent._is_unfulfilled_promise(turn)



class TestSplitParty:
    @pytest.mark.parametrize("party,sizes", [
        (sf._Party(9, 0, [], 0, []), [9]),
        (sf._Party(10, 0, [], 0, []), [5, 5]),
        (sf._Party(12, 3, [4, 6, 8], 0, []), [8, 7]),
        (sf._Party(8, 1, [5], 2, [0, 1]), [6, 5]),
        (sf._Party(19, 0, [], 0, []), [7, 6, 6]),
    ])
    def test_bookings_are_balanced_and_valid(self, party: Any, sizes: list[int]) -> None:
        parts = sf._split_party(party)
        assert [p.seats for p in parts] == sizes
        assert sum(p.adults for p in parts) == party.adults
        assert sum(p.children for p in parts) == party.children
        assert sum(p.infants for p in parts) == party.infants
        assert all(p.seats <= 9 and p.infants <= p.adults for p in parts)
        assert sorted(a for p in parts for a in p.child_ages) == sorted(party.child_ages)

    def test_equal_bookings_are_searched_once(self) -> None:
        parts = sf._split_party(sf._Party(10, 0, [], 0, []))
        assert len(dict.fromkeys(parts)) == 1


class TestCombineBookings:
    def _row(self, flights: str, price: float, seats: int = 9, refundable: bool = True) -> dict[str, Any]:
        seg = {"marketing_airline_code": "FZ", "flight_number": flights,
               "departure": "2026-10-20 04:25:00", "seats_remaining": seats}
        return {"segments_outbound": [seg], "segments_return": [], "price_inr": price,
                "price_per_adult_inr": price / 5, "refundable": refundable,
                "fare_source_code": f"code-{flights}-{price}", "airline": "Flydubai"}

    def test_totals_are_summed_per_itinerary(self) -> None:
        a, b = sf._Party(5, 0, [], 0, []), sf._Party(5, 0, [], 0, [])
        pool = sf._Pool([self._row("438", 100.0, seats=20)], [], [], [], False, {})
        out = sf._combine_bookings([a, b], [pool, pool])
        assert out[0]["price_inr"] == 200.0
        assert out[0]["price_per_adult_inr"] == 20.0
        assert [x["price_inr"] for x in out[0]["bookings"]] == [100.0, 100.0]

    def test_too_few_seats_for_the_whole_party_is_dropped(self) -> None:
        a = sf._Party(5, 0, [], 0, [])
        pool = sf._Pool([self._row("438", 100.0, seats=7)], [], [], [], False, {})
        assert sf._combine_bookings([a, a], [pool, pool]) == []

    def test_nine_seats_means_nine_or_more(self) -> None:
        """The supplier caps the count at 9; a 10-seat party must not be dropped."""
        a = sf._Party(5, 0, [], 0, [])
        pool = sf._Pool([self._row("438", 100.0, seats=9)], [], [], [], False, {})
        assert len(sf._combine_bookings([a, a], [pool, pool])) == 1

    def test_an_itinerary_missing_from_one_booking_is_dropped(self) -> None:
        a, b = sf._Party(5, 0, [], 0, []), sf._Party(4, 0, [], 0, [])
        out = sf._combine_bookings([a, b], [
            sf._Pool([self._row("438", 100.0)], [], [], [], False, {}),
            sf._Pool([self._row("999", 90.0)], [], [], [], False, {})])
        assert out == []

    def test_refundable_only_if_every_booking_is(self) -> None:
        a = sf._Party(5, 0, [], 0, [])
        out = sf._combine_bookings([a, a], [
            sf._Pool([self._row("438", 100.0, seats=20)], [], [], [], False, {}),
            sf._Pool([self._row("438", 100.0, seats=20, refundable=False)], [], [], [], False, {})])
        assert out[0]["refundable"] is False


class TestNudgedRetryMustBeGrounded:
    def test_invented_table_after_the_nudge_is_not_shown(self) -> None:
        import agent

        class _Agent:
            def invoke(self, payload: dict[str, Any], _cfg: Any) -> dict[str, Any]:
                return {"messages": [HumanMessage(content=payload["messages"][0]["content"]),
                                     AIMessage(content="| Air India | 04:15 -> 06:10 | Non-stop |", id="r1")]}

        stalled = {"messages": [HumanMessage(content="10 adults"),
                                AIMessage(content="One moment while I pull those options for you.")]}
        out = agent._recover_empty_turn(_Agent(), stalled, {}, surface="chat", thread_id="t")
        assert agent._final_reply(out) == agent._COULD_NOT_FINISH

    def test_a_question_back_is_allowed(self) -> None:
        import agent

        class _Agent:
            def invoke(self, payload: dict[str, Any], _cfg: Any) -> dict[str, Any]:
                return {"messages": [HumanMessage(content=payload["messages"][0]["content"]),
                                     AIMessage(content="Which date do you want to fly?")]}

        stalled = {"messages": [HumanMessage(content="flights"),
                                AIMessage(content="Let me check that for you.")]}
        out = agent._recover_empty_turn(_Agent(), stalled, {}, surface="chat", thread_id="t")
        assert agent._final_reply(out) == "Which date do you want to fly?"

    def test_the_corrected_reply_replaces_the_stored_one(self) -> None:
        import agent

        saved: list[Any] = []

        class _Agent:
            def update_state(self, _cfg: Any, update: dict[str, Any]) -> None:
                saved.extend(update["messages"])

        reply = AIMessage(content="corrected", id="ai-1")
        agent._save_final_reply(_Agent(), {}, {"messages": [reply]})
        assert saved == [reply]

