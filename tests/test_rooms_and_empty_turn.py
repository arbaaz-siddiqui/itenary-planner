"""Regressions from the 20-conversation live audit.

Three defects it caught, each of which shipped wrong data to a customer:
  1. 8 adults were quoted "2 rooms" -- the party payload never said how many
     rooms the party needed, so the model guessed.
  2. My first fix for (1) silently DROPPED children: 2 adults + 2 kids came
     back as one 2-adult room, pricing the kids at zero.
  3. An empty assistant turn left no data in history, and the next turn
     invented a whole hotel table ("Rove Downtown Rs 61,240") for hotels that
     were never searched.
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent import _guard_empty_turn, is_internal_marker
from rules import resolve_party


class TestRoomAllocation:
    def test_two_adults_is_one_room(self):
        assert resolve_party(adults=2)["rooms_needed"] == 1

    def test_eight_adults_is_four_rooms(self):
        # The live defect: this was quoted as 2 rooms.
        assert resolve_party(adults=8)["rooms_needed"] == 4

    def test_odd_party_rounds_up(self):
        assert resolve_party(adults=9)["rooms_needed"] == 5
        assert resolve_party(adults=1)["rooms_needed"] == 1

    def test_children_are_not_dropped(self):
        # The regression my own first fix introduced.
        r = resolve_party(adults=2, children=2, child_ages=[6, 9])
        assert sum(x["adults"] for x in r["rooms"]) == 2
        assert sum(x["children"] for x in r["rooms"]) == 2
        assert sorted(a for x in r["rooms"] for a in x["child_ages"]) == [6, 9]

    def test_every_traveller_is_covered(self):
        for adults, children, ages in [
            (2, 0, []), (4, 0, []), (8, 0, []),
            (2, 2, [6, 9]), (4, 3, [3, 7, 11]), (1, 1, [5]),
        ]:
            r = resolve_party(adults=adults, children=children, child_ages=ages)
            assert sum(x["adults"] for x in r["rooms"]) == adults
            assert sum(x["children"] for x in r["rooms"]) == children
            assert len(r["rooms"]) == r["rooms_needed"]

    def test_no_room_holds_children_without_an_adult(self):
        # An unaccompanied-children room is not bookable.
        r = resolve_party(adults=1, children=3, child_ages=[4, 6, 8])
        for room in r["rooms"]:
            if room["children"]:
                assert room["adults"] >= 1

    def test_children_per_room_within_supplier_cap(self):
        # Supplier allows at most 2 children per room.
        r = resolve_party(adults=4, children=4, child_ages=[3, 5, 7, 9])
        assert all(x["children"] <= 2 for x in r["rooms"])


class TestEmptyTurnGuard:
    def _empty_no_tools(self):
        return {"messages": [HumanMessage(content="dubai family trip"),
                             AIMessage(content="")]}

    def test_no_data_marker_added_when_nothing_ran(self):
        r = self._empty_no_tools()
        _guard_empty_turn(r, surface="streamlit", thread_id="t")
        last = str(r["messages"][-1].content)
        assert "NOTHING has been searched" in last
        assert is_internal_marker(last)

    def test_normal_reply_is_untouched(self):
        r = {"messages": [AIMessage(content="Here are your hotels")]}
        before = len(r["messages"])
        _guard_empty_turn(r, surface="streamlit", thread_id="t")
        assert len(r["messages"]) == before

    def test_tools_ran_but_no_prose_is_salvaged(self):
        call = AIMessage(content="", tool_calls=[
            {"name": "search_flights", "args": {}, "id": "1", "type": "tool_call"},
            {"name": "search_tours", "args": {}, "id": "2", "type": "tool_call"}])
        r = {"messages": [
            HumanMessage(content="family trip"), call,
            ToolMessage(content='{"options": []}', tool_call_id="1", name="search_flights"),
            ToolMessage(content='{"options": []}', tool_call_id="2", name="search_tours"),
            AIMessage(content="")]}
        _guard_empty_turn(r, surface="streamlit", thread_id="t")
        last = str(r["messages"][-1].content)
        assert "search_flights" in last and "search_tours" in last
        assert "do not re-search" in last
        # Hotels were not among the calls, so it must warn before any hotel talk.
        assert "search_hotels" in last

    def test_markers_are_never_customer_text(self):
        assert is_internal_marker("[system] guidance for the next turn")
        assert not is_internal_marker("Here are your hotel options")
        assert not is_internal_marker(None)
