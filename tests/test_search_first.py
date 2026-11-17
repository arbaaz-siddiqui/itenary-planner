"""A dated search request starts with a tool call, not invented prices.

Asked "Show me tours in Bangkok on 12 Dec 2026", the model wrote a table of
made-up prices; the unsourced-price guard re-asked it (a second model call,
about 7 s) and the invented table showed until it was replaced.
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent import _must_search_first


def test_a_dated_search_must_start_with_a_tool() -> None:
    assert _must_search_first([HumanMessage("Show me tours in Bangkok on 12 Dec 2026 for 2 adults")])
    assert _must_search_first([HumanMessage("Flights from Mumbai to Tokyo on 15 Dec 2026")])
    assert _must_search_first([HumanMessage("[system] Plan mode: ...\n\nhotels in Paris 10 to 13 Dec")])


def test_other_messages_are_free() -> None:
    assert not _must_search_first([HumanMessage("which of these tours is best for kids?")])
    assert not _must_search_first([HumanMessage("thanks!")])
    assert not _must_search_first([])


def test_only_the_first_step_of_the_turn_is_forced() -> None:
    turn = [
        HumanMessage("Show me tours in Bangkok on 12 Dec 2026"),
        AIMessage("", tool_calls=[{"id": "1", "name": "search_tours", "args": {}}]),
        ToolMessage("{}", tool_call_id="1", name="search_tours"),
    ]
    assert not _must_search_first(turn)


def test_each_ask_forces_its_own_search_tool() -> None:
    from agent import _search_tools_asked

    assert _search_tools_asked([HumanMessage("Show me tours in Paris on 14 Dec 2026")]) == ["search_tours"]
    assert _search_tools_asked([HumanMessage("Flights Mumbai to Tokyo on 15 Dec 2026")]) == ["search_flights"]
    assert _search_tools_asked([HumanMessage("hotels and tours in Bali 10 to 13 Dec")]) == ["search_tours", "search_hotels"]
