"""Changing a Plan-mode trip keeps the customer's picks (live retest, 16 Oct 2026).

Adding a visa re-ran the plan with the hotel's standard 14:00 check-in, the
2-hour rest moved to 16:00 and the 15:00 desert safari vanished. Swapping AYA
Universe for Dubai Frame put Dubai Frame on a full day, where it was dropped,
and the day fill brought AYA Universe straight back.
"""

from scheduling import build_itinerary


def _tours(out: dict) -> dict[str, str]:
    return {i["title"]: d["date"] for d in out["days"] for i in d["items"] if i["kind"] == "tour"}


def test_a_later_standard_check_in_does_not_push_the_rest() -> None:
    out = build_itinerary(
        tours=[
            {
                "name": "Dubai Desert Safari",
                "date": "2026-11-06",
                "duration": "5 Hours",
                "timeslots": [{"time": "15:00"}],
            }
        ],
        start_date="2026-11-06",
        nights=4,
        arrival_time="10:20",
        hotel_name="Social Hotel",
        hotel_checkin_time="14:00",
        departure_time="19:25",
    )
    assert _tours(out).get("Dubai Desert Safari") == "2026-11-06"
    rest = next(i for i in out["days"][0]["items"] if i["title"] == "Check in and rest")
    assert rest["start"] == "11:20"
    assert "standard check-in from 14:00" in rest["detail"]


def test_a_tour_whose_day_is_full_moves_to_another_day() -> None:
    out = build_itinerary(
        tours=[
            {
                "name": "BAPS Hindu Temple Tour",
                "date": "2026-11-08",
                "duration": "6 Hours",
                "timeslots": [{"time": "09:45"}],
            },
            {
                "name": "Dubai Frame",
                "date": "2026-11-08",
                "duration": "3 Hours",
                "timeslots": [{"time": "10:00"}],
            },
        ],
        start_date="2026-11-06",
        nights=4,
        arrival_time="10:20",
        hotel_name="Social Hotel",
        departure_time="19:25",
    )
    placed = _tours(out)
    assert "Dubai Frame" in placed and "BAPS Hindu Temple Tour" in placed
    assert not [e for e in out.get("excluded") or [] if e["title"] == "Dubai Frame"]


def test_a_tour_named_in_this_message_counts_as_named_now() -> None:
    import rules

    rules.set_conversation_text(
        "plan my trip\nreplace aya universe with dubai frame",
        latest="replace AYA Universe with Dubai Frame",
    )
    assert rules.customer_named_now("AYA Universe Dubai By Gujjutours")
    assert not rules.customer_named_now("Dubai Miracle Garden Tickets by Gujjutours")
    rules.set_conversation_text("")


def test_slots_the_model_sends_give_way_to_the_suppliers() -> None:
    """Dubai Frame is sold without set times; a 16:00 'slot' copied from the
    last plan pinned it after a 6-hour temple tour and it was dropped."""
    from unittest.mock import patch

    from agent_tools import plan_itinerary_tool

    frame = "Dubai Frame Tickets with Transfer by Gujjutours"
    with (
        patch("agent_tools._tour_facts_lookup", return_value={}),
        patch("agent_tools._tour_slots", return_value=[]),
        patch("agent_tools._catalogue", return_value={frame.lower(): (frame, 1)}),
        patch("agent_tools._tour_price_lookup", return_value=0.0),
    ):
        out = plan_itinerary_tool.invoke(
            {
                "start_date": "2026-11-06",
                "nights": 4,
                "adults": 2,
                "fill_days": False,
                "tours": [
                    {
                        "name": frame,
                        "date": "2026-11-09",
                        "duration": "3 Hours",
                        "price_per_adult_inr": 900,
                        "timeslots": [{"slot_id": "7", "time": "23:30"}],
                    }
                ],
            }
        )
    starts = [i["start"] for d in out["days"] for i in d["items"] if i["title"] == frame]
    assert starts and starts[0] != "23:30"


def test_the_visa_line_names_the_visa_that_was_chosen() -> None:
    from agent_tools import _visa_label
    from mcp_tools.quote_registry import remember_label

    remember_label("visa", 13254.29, "30 Days Multiple Entry Tourist Visa")
    assert _visa_label(13254.29) == "30 Days Multiple Entry Tourist Visa"
    assert _visa_label(1.0) == "UAE tourist visa"
