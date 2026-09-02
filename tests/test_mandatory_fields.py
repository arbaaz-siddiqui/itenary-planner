"""A trip with no stated departure city was searched as Mumbai->Dubai and a
full fare table presented as fact.

The model fills a missing mandatory field with a plausible value, so the value
it passes looks valid to the tool. Two guards: the tool refuses a blank
mandatory field, and `customer_said` treats a city absent from the customer's
own words as blank.
"""

import pytest

from rules import (
    customer_said,
    missing_search_fields,
    needs_input_error,
    set_conversation_text,
)


class TestMissingFieldDetection:
    def test_blank_and_zero_count_as_missing(self):
        got = missing_search_fields(origin_city="", departure_date="2026-09-09", adults=0)
        assert sorted(got) == ["adults", "origin_city"]

    def test_all_present_is_empty(self):
        assert missing_search_fields(check_in="2026-09-09", check_out="2026-09-13") == []

    def test_error_names_the_fields_and_offers_a_retry(self):
        err = needs_input_error(["origin_city"])
        assert err["error"] is True
        assert err["error_type"] == "NeedsCustomerInput"
        assert err["missing_fields"] == ["origin_city"]
        assert "which city they are flying from" in err["message"]
        assert "origin_city" in err["retry_with"]

    def test_error_lists_several_fields_readably(self):
        msg = needs_input_error(["check_in", "check_out"])["message"]
        assert "check-in date and the check-out date" in msg


class TestCustomerSaidGate:
    def test_city_the_customer_never_named_is_not_said(self):
        set_conversation_text("plan a dubai trip for 3 adults 9 sep to 13 sep")
        assert not customer_said("Mumbai")

    def test_city_the_customer_named_is_said(self):
        set_conversation_text("dubai 4 nights 2 adults from hyderabad 20 october")
        assert customer_said("Hyderabad")

    def test_matches_on_first_word_so_delhi_matches_new_delhi(self):
        set_conversation_text("we are flying from new delhi")
        assert customer_said("Delhi")

    def test_fails_open_with_no_conversation_text(self):
        # Never block a search just because context was not set.
        set_conversation_text("")
        assert customer_said("Mumbai")


class TestToolsRefuseRatherThanGuess:
    def test_flights_refuse_an_unstated_origin_without_calling_the_api(self):
        from booking_api.http_client import http_requests_since, latest_http_seq
        from mcp_tools.search_flights import _impl

        set_conversation_text("plan a dubai trip for 3 adults 9 sep to 13 sep")
        cursor = latest_http_seq()
        out = _impl(origin_city="Mumbai", destination_city="Dubai",
                    departure_date="2026-09-09", adults=3)
        assert out["error_type"] == "NeedsCustomerInput"
        assert out["missing_fields"] == ["origin_city"]
        # The whole point: refusing costs nothing.
        assert http_requests_since(cursor) == []

    def test_hotels_refuse_missing_dates(self):
        from mcp_tools.search_hotels import _impl

        set_conversation_text("dubai trip please")
        out = _impl(destination_city="Dubai", check_in="", check_out="", adults=0)
        assert out["error_type"] == "NeedsCustomerInput"
        assert "check_in" in out["missing_fields"]

    def test_hotels_accept_rooms_instead_of_adults(self):
        from mcp_tools.search_hotels import _impl

        set_conversation_text("dubai 9 to 13 sep for 3 adults")
        out = _impl(destination_city="Dubai", check_in="2026-09-09",
                    check_out="2026-09-13",
                    rooms=[{"adults": 3, "children": 0, "child_ages": []}],
                    max_results=1)
        assert out.get("error_type") != "NeedsCustomerInput"

    def test_tours_refuse_a_missing_date(self):
        from mcp_tools.search_tours import _impl

        set_conversation_text("show me dubai tours")
        out = _impl(destination_city="Dubai", travel_date="")
        assert out["error_type"] == "NeedsCustomerInput"
        assert out["missing_fields"] == ["travel_date"]

    @pytest.mark.parametrize("tool_path,kwargs", [
        ("mcp_tools.search_tours", {"destination_city": "Dubai", "travel_date": ""}),
        ("mcp_tools.search_hotels", {"destination_city": "Dubai", "check_in": "",
                                     "check_out": "", "adults": 0}),
    ])
    def test_assume_missing_lets_an_insistent_customer_through(self, tool_path, kwargs):
        # If the customer declines or says search anyway, we must not deadlock.
        import importlib

        impl = importlib.import_module(tool_path)._impl
        out = impl(**kwargs, assume_missing=True)
        assert out.get("error_type") != "NeedsCustomerInput"


class TestNonMandatoryFieldsNeverGate:
    def test_budget_is_not_a_mandatory_field(self):
        from rules import _FIELD_PROMPTS

        for never in ("budget", "cabin", "airline", "meal", "room_type"):
            assert never not in _FIELD_PROMPTS
