"""search_log rows derived from a reply's tool calls (api/search_log.py)."""

import json

from api.search_log import error_of, party_of, result_count_of, search_rows


def test_flight_search_row_has_route_date_party_and_count():
    calls = [{
        "tool_name": "search_flights",
        "input": {"origin_city": "Mumbai", "destination_city": "Dubai",
                  "departure_date": "2026-12-10", "adults": 2, "children": 1, "infants": 0},
        "output": json.dumps({"options": [{"airline": "Emirates"}, {"airline": "IndiGo"}]}),
    }]
    [row] = search_rows(calls)
    assert row["call_index"] == 0
    assert row["tool"] == "search_flights"
    assert row["origin"] == "Mumbai"
    assert row["destination"] == "Dubai"
    assert row["start_date"] == "2026-12-10"
    assert row["party"] == "2 adults, 1 child"
    assert row["result_count"] == 2
    assert row["error"] is None
    assert row["args"]["adults"] == 2


def test_hotel_rooms_list_gives_adults_and_rooms():
    args = {"destination_city": "Dubai", "check_in": "2026-12-01", "check_out": "2026-12-04",
            "rooms": [{"adults": 2}, {"adults": 1, "child_ages": [6]}]}
    [row] = search_rows([{"tool_name": "search_hotels", "input": args,
                          "output": {"options": [], "total_results": 0}}])
    assert row["party"] == "3 adults, 2 rooms"
    assert row["start_date"] == "2026-12-01"
    assert row["result_count"] == 0


def test_visa_uses_destination_country_and_total_results():
    out = {"total_results": 3, "options": None}
    [row] = search_rows([{"tool_name": "get_visa_info",
                          "input": {"destination_country": "UAE", "travel_date": "2026-12-01",
                                    "adults": 1},
                          "output": json.dumps(out)}])
    assert row["destination"] == "UAE"
    assert row["result_count"] == 3
    assert row["party"] == "1 adult"


def test_errors_from_flag_message_and_plain_text():
    assert error_of({"error": True, "message": "No flights"}, None) == "No flights"
    assert error_of({"error": "Bad city"}, None) == "Bad city"
    assert error_of({"error": True}, None) == "failed"
    assert error_of({"error": False, "options": []}, None) is None
    assert error_of(None, "Error: tool crashed") == "Error: tool crashed"
    assert error_of(None, "Here are tours") is None
    [row] = search_rows([{"tool_name": "search_tours", "input": "{\"destination_city\": \"Dubai\"}",
                          "output": "Error: timeout"}])
    assert row["error"] == "Error: timeout"
    assert row["destination"] == "Dubai"
    assert row["result_count"] is None


def test_transfer_hotel_is_not_a_destination():
    [row] = search_rows([{"tool_name": "search_airport_transfer_dubai",
                          "input": {"hotel_name": "Address Sky View", "arrival_date": "2026-12-10",
                                    "adults": 4},
                          "output": {"options": [{}, {}]}}])
    assert row["destination"] is None
    assert row["start_date"] == "2026-12-10"
    assert row["party"] == "4 adults"


def test_counts_fall_back_to_results_and_items():
    assert result_count_of({"results": [1, 2, 3]}) == 3
    assert result_count_of({"items": [1]}) == 1
    assert result_count_of({"message": "ok"}) is None
    assert result_count_of(None) is None


def test_party_handles_strings_child_ages_and_nothing():
    assert party_of({"adults": "4", "child_ages": [3, 9], "rooms": 2}) == "4 adults, 2 children, 2 rooms"
    assert party_of({}) is None
    assert party_of({"adults": True}) is None


def test_bad_entries_skipped_and_big_args_truncated():
    rows = search_rows([None, "x", {"tool_name": "plan_itinerary_tool",
                                    "input": {"tours": ["t" * 100] * 200}}])
    assert len(rows) == 1
    assert rows[0]["call_index"] == 2
    assert rows[0]["args"] == {"_truncated": True}
    assert search_rows(None) == []
