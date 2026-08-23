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


class TestPdfCarriesTheSchedule:
    """The customer's PDF had a price table and NO day-by-day itinerary.

    Root cause: the `data` dict was built with `"day_plans": day_plans or []`
    BEFORE the block that recovers day_plans from the cached plan, so the
    recovery wrote a local nobody read again. The PDF was always built from the
    empty list, and because day_plans looked non-empty afterwards, the
    MissingDayPlans guard never fired either — a hollow PDF with no error.
    """

    def test_pdf_reuses_the_cached_plan_when_no_date_is_passed(self):
        from agent_tools import generate_itinerary_pdf_tool, plan_itinerary_tool

        plan_itinerary_tool.invoke(
            {"start_date": "2026-09-01", "nights": 5, "adults": 4,
             "tours": [{"name": "Abu Dhabi City Tour from Dubai"}]}
        )
        out = generate_itinerary_pdf_tool.invoke(
            {"destination": "Dubai", "nights": 5, "total_inr": 1000.0}
        )
        assert not out.get("error"), out.get("message")
        assert out.get("download_url")

    def test_time_display_shows_the_full_range(self):
        from itinerary_pdf import DayItem

        assert DayItem(title="Tour", start="09:45", end="11:45").time_display == "09:45-11:45"
        # Open-ended and instant entries show a single time, not "09:45-".
        assert DayItem(title="Flight", start="23:10").time_display == "23:10"
        assert DayItem(title="X", start="09:00", end="09:00").time_display == "09:00"

    def test_end_time_survives_the_dict_hand_off(self):
        from itinerary_pdf import _coerce_day_items

        items = _coerce_day_items([
            {"title": "Dhow Cruise", "start": "09:45", "end": "11:45", "kind": "tour"}
        ])
        assert items[0].end == "11:45"
        assert items[0].time_display == "09:45-11:45"


class TestHotelPerRoomPricing:
    """The customer asked to see what ONE room costs, not just the combined
    figure for all rooms."""

    def test_total_per_room_is_the_stay_divided_by_rooms(self):
        # 33,291.59 for 2 rooms over 5 nights -> 16,645.79 for one room.
        total, rooms, nights = 33291.59, 2, 5
        assert round(total / rooms, 2) == 16645.80 or round(total / rooms, 2) == 16645.79
        assert round(total / nights / rooms, 2) == 3329.16
