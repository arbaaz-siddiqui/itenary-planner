"""Calls whose arguments were written into the tool name are made runnable."""

from agent import repair_inline_tool_call


def test_arguments_in_the_name_become_arguments():
    got = repair_inline_tool_call({"name": "get_tour_details(tour id:53632)<tool call|>", "args": {}, "id": "x"})
    assert got == {"name": "get_tour_details", "args": {"tour_id": 53632}, "id": "x"}


def test_template_quotes_are_stripped_and_types_kept():
    got = repair_inline_tool_call({
        "name": 'get_tour_options(tour_id:28488,travel_date:<|"|>2026-10-22<|"|>,adults:2)<tool_call|>',
        "args": {},
    })
    assert got["name"] == "get_tour_options"
    assert got["args"] == {"tour_id": 28488, "travel_date": "2026-10-22", "adults": 2}


def test_real_arguments_win_over_inline_ones():
    got = repair_inline_tool_call({"name": "search_tours(adults:3)", "args": {"adults": 2}})
    assert got["args"]["adults"] == 2


def test_clean_names_are_left_alone():
    assert repair_inline_tool_call({"name": "search_tours", "args": {}}) is None
