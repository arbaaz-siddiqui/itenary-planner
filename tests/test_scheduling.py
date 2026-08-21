"""Time feasibility must be provable, not asserted in a prompt.

Every time in a rendered schedule used to be invented by the model, because
`build_trip_schedule_tool` accepts `start`/`end` as free text and validates
nothing. These tests pin the rules that replace that guesswork.

The headline case (`TestBurjKhalifaOnArrivalDay`) is the real one from the
product: land 16:00, hotel 17:00, Burj Khalifa's last slot 18:00. With a 3-hour
rest the customer is free at 20:00, so the tour CANNOT be that day — and the
reason must be quotable back to them.
"""

from __future__ import annotations

from datetime import time

import pytest

from scheduling import (
    SchedulePolicy,
    build_itinerary,
    earliest_free_time,
    parse_duration_minutes,
    parse_slot_time,
    usable_slots,
)


def _slots(*times_: str, available: int = 100) -> list[dict]:
    return [
        {"timeSlot": t, "available": available, "timeSlotId": f"id-{t}"} for t in times_
    ]


# =============================================================================
# Slot parsing — the 00:00 placeholder is the trap
# =============================================================================
class TestSlotParsing:
    def test_real_time_parsed(self) -> None:
        assert parse_slot_time("07:30") == time(7, 30)
        assert parse_slot_time("23:00") == time(23, 0)

    @pytest.mark.parametrize("raw", ["00:00", "0:00", "00:00:00", "", None])
    def test_placeholder_rejected(self, raw: object) -> None:
        """25% of live tours return 00:00 meaning "no fixed time".

        Treating it as midnight would schedule a tour at 00:00.
        """
        assert parse_slot_time(raw) is None

    @pytest.mark.parametrize("raw", ["garbage", "99:99", "25:00", "7pm"])
    def test_junk_rejected(self, raw: str) -> None:
        assert parse_slot_time(raw) is None

    def test_sold_out_slots_dropped(self) -> None:
        rows = _slots("09:00") + [{"timeSlot": "10:00", "available": 0}]
        assert [t for t, _ in usable_slots(rows)] == [time(9, 0)]

    def test_slots_sorted_and_id_preserved(self) -> None:
        got = usable_slots(_slots("15:00", "07:00", "11:00"))
        assert [t for t, _ in got] == [time(7, 0), time(11, 0), time(15, 0)]
        assert got[0][1]["timeSlotId"] == "id-07:00"

    def test_all_placeholders_means_no_slots(self) -> None:
        """A tour whose only slot is 00:00 must be treated as NOT slot-based."""
        assert usable_slots(_slots("00:00")) == []


# =============================================================================
# Duration parsing — three live formats, 141/332 empty
# =============================================================================
class TestDurationParsing:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("0-Days 2-Hours 0-Minutes", 120),
            ("0-Days 2-Hours 30-Minutes", 150),
            ("0-Days 0-Hours 30-Minutes", 30),
            ("4 Hours (Approx)", 240),
            ("1 Hour (Approx)", 60),
            ("15 Minutes (Approx)", 15),
            ("2 hours (Approx)", 120),
            ("Dubai Beach - 30 / 60 mins", 30),
        ],
    )
    def test_live_formats(self, raw: str, expected: int) -> None:
        assert parse_duration_minutes(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "   "])
    def test_missing_duration_is_none(self, raw: object) -> None:
        """141 of 332 Dubai tours have no duration; callers apply a default."""
        assert parse_duration_minutes(raw) is None


# =============================================================================
# Rest policy
# =============================================================================
class TestEarliestFreeTime:
    def test_three_hour_rest_after_hotel(self) -> None:
        assert earliest_free_time(arrival_time="16:00", hotel_checkin_time="17:00") == time(20, 0)

    def test_transit_estimated_when_hotel_time_unknown(self) -> None:
        """No hotel time given -> landing + 60min transit + 180min rest."""
        assert earliest_free_time(arrival_time="16:00") == time(20, 0)

    def test_rest_is_configurable(self) -> None:
        p = SchedulePolicy(rest_after_landing_min=60)
        assert earliest_free_time(arrival_time="16:00", hotel_checkin_time="17:00", policy=p) == time(18, 0)

    def test_rolling_past_midnight_returns_none(self) -> None:
        assert earliest_free_time(arrival_time="23:00", hotel_checkin_time="23:30") is None

    def test_unparseable_arrival(self) -> None:
        assert earliest_free_time(arrival_time="nonsense") is None


# =============================================================================
# THE headline case
# =============================================================================
class TestBurjKhalifaOnArrivalDay:
    @pytest.fixture
    def burj(self) -> dict:
        # Real shape: half-hourly, but here truncated so the last slot is 18:00.
        return {
            "name": "At The Top, Burj Khalifa",
            "duration": "0-Days 1-Hours 30-Minutes",
            "timeslots": _slots("07:00", "12:00", "17:00", "18:00"),
        }

    def test_not_scheduled_on_arrival_day(self, burj: dict) -> None:
        out = build_itinerary(
            start_date="2026-09-15", nights=3, arrival_time="16:00",
            hotel_checkin_time="17:00", tours=[burj],
        )
        day1 = out["days"][0]
        assert not [i for i in day1["items"] if i["kind"] == "tour"], (
            "customer is free at 20:00 but the last slot is 18:00 — nothing "
            "should be booked on arrival day"
        )

    def test_moved_to_a_later_day(self, burj: dict) -> None:
        out = build_itinerary(
            start_date="2026-09-15", nights=3, arrival_time="16:00",
            hotel_checkin_time="17:00", tours=[burj],
        )
        tours = [
            (d["day_number"], i)
            for d in out["days"] for i in d["items"] if i["kind"] == "tour"
        ]
        assert len(tours) == 1
        day_no, item = tours[0]
        assert day_no >= 2
        assert item["start"] in {"07:00", "12:00", "17:00", "18:00"}
        assert item["slot_id"], "a real slot id must be carried for booking"

    def test_rest_block_is_visible_to_the_customer(self, burj: dict) -> None:
        out = build_itinerary(
            start_date="2026-09-15", nights=3, arrival_time="16:00",
            hotel_checkin_time="17:00", hotel_name="Novotel", tours=[burj],
        )
        kinds = [i["kind"] for i in out["days"][0]["items"]]
        assert "flight" in kinds and "transfer" in kinds and "free" in kinds

    def test_early_arrival_allows_same_day(self) -> None:
        """The rule is time-based, not a blanket "never on arrival day"."""
        burj = {
            "name": "Burj Khalifa", "duration": "0-Days 1-Hours 0-Minutes",
            "timeslots": _slots("09:00", "14:00", "18:00"),
        }
        out = build_itinerary(
            start_date="2026-09-15", nights=2, arrival_time="06:00",
            hotel_checkin_time="07:00", tours=[burj],
        )
        assert [i for i in out["days"][0]["items"] if i["kind"] == "tour"], (
            "free from 10:00, and a 14:00 slot exists — it should fit today"
        )

    def test_exclusion_reason_names_the_slot(self) -> None:
        """If it fits nowhere, the reason must be specific enough to say aloud."""
        burj = {"name": "Burj Khalifa", "duration": "0-Days 1-Hours 0-Minutes",
                "timeslots": _slots("07:00", "08:00")}
        out = build_itinerary(
            start_date="2026-09-15", nights=0, arrival_time="16:00",
            hotel_checkin_time="17:00", tours=[burj],
        )
        assert out["excluded"], "a one-day trip landing at 16:00 cannot fit a 08:00-last-slot tour"
        assert "last slot" in out["excluded"][0]["reason"]


# =============================================================================
# Placement behaviour
# =============================================================================
class TestPlacement:
    def test_tour_without_slots_uses_duration(self) -> None:
        out = build_itinerary(
            start_date="2026-09-15", nights=2, arrival_time="06:00",
            hotel_checkin_time="07:00",
            tours=[{"name": "Desert Safari", "duration": "6 Hours (Approx)"}],
        )
        tours = [i for d in out["days"] for i in d["items"] if i["kind"] == "tour"]
        assert len(tours) == 1
        assert tours[0]["end"], "an end time must be derived from duration"

    def test_placeholder_only_tour_still_schedulable(self) -> None:
        """00:00 slots must not make a tour unschedulable — fall back to duration."""
        out = build_itinerary(
            start_date="2026-09-15", nights=2, arrival_time="06:00",
            hotel_checkin_time="07:00",
            tours=[{"name": "Mall Visit", "duration": "2 Hours (Approx)",
                    "timeslots": _slots("00:00")}],
        )
        assert [i for d in out["days"] for i in d["items"] if i["kind"] == "tour"]

    def test_activities_do_not_overlap(self) -> None:
        tours = [
            {"name": f"Tour {i}", "duration": "0-Days 2-Hours 0-Minutes"} for i in range(3)
        ]
        out = build_itinerary(
            start_date="2026-09-15", nights=4, arrival_time="06:00",
            hotel_checkin_time="07:00", tours=tours,
        )
        for d in out["days"]:
            items = [i for i in d["items"] if i["kind"] == "tour" and i["end"]]
            for a, b in zip(items, items[1:], strict=False):
                assert a["end"] <= b["start"], f"overlap on day {d['day_number']}"

    def test_long_tour_not_stranded_by_a_short_one(self) -> None:
        """Regression: a 30-min evening slot used to push the day's cursor past
        the point where a 6-hour tour could fit, stranding it entirely."""
        short = {"name": "Fountain Show", "duration": "30 Minutes (Approx)",
                 "timeslots": _slots("17:45")}
        long = {"name": "Desert Safari", "duration": "6 Hours (Approx)"}
        out = build_itinerary(
            start_date="2026-09-15", nights=2, arrival_time="06:00",
            hotel_checkin_time="07:00", tours=[short, long],
        )
        titles = [i["title"] for d in out["days"] for i in d["items"]]
        assert "Desert Safari" in titles
        assert not out["excluded"]

    def test_nothing_scheduled_past_the_finish_limit(self) -> None:
        """The FINISH time is the constraint, not the start.

        A blanket latest-start cap wrongly blocked a 30-minute 21:45 fountain
        show ending 22:15, so the rule is expressed as "must be done by X".
        """
        p = SchedulePolicy(latest_activity_end=time(18, 0))
        out = build_itinerary(
            start_date="2026-09-15", nights=1, arrival_time="06:00",
            hotel_checkin_time="07:00", policy=p,
            tours=[{"name": "Late Tour", "duration": "1 Hour (Approx)",
                    "timeslots": _slots("20:00")}],
        )
        assert not [i for d in out["days"] for i in d["items"] if i["kind"] == "tour"]
        assert out["excluded"]

    def test_departure_day_kept_clear(self) -> None:
        out = build_itinerary(
            start_date="2026-09-15", nights=2, arrival_time="06:00",
            hotel_checkin_time="07:00", departure_time="10:00",
            tours=[{"name": f"T{i}", "duration": "2 Hours (Approx)"} for i in range(4)],
        )
        last = out["days"][-1]
        assert not [i for i in last["items"] if i["kind"] == "tour"]
        assert any(i["kind"] == "flight" for i in last["items"])


class TestLateArrival:
    def test_no_activity_when_landing_near_midnight(self) -> None:
        """Regression: a 23:30 landing put the customer at the hotel at 00:30 the
        NEXT day, so "free at 03:30" looked valid and a 07:00 tour was booked on
        the arrival day."""
        out = build_itinerary(
            start_date="2026-09-15", nights=3, arrival_time="23:30",
            tours=[{"name": "Burj Khalifa", "duration": "1 Hour (Approx)",
                    "timeslots": _slots("07:00", "12:00")}],
        )
        assert not [i for i in out["days"][0]["items"] if i["kind"] == "tour"]
        placed = [d["day_number"] for d in out["days"]
                  for i in d["items"] if i["kind"] == "tour"]
        assert placed and min(placed) >= 2

    def test_arrival_narrative_stays_in_order(self) -> None:
        """A post-midnight check-in must not sort above the flight that caused it."""
        out = build_itinerary(
            start_date="2026-09-15", nights=2, arrival_time="23:30", hotel_name="Novotel",
        )
        titles = [i["title"] for i in out["days"][0]["items"]]
        assert titles.index("Arrive in Dubai") < titles.index("Check in and rest")


class TestStructure:
    def test_day_count_is_nights_plus_one(self) -> None:
        out = build_itinerary(start_date="2026-09-15", nights=10)
        assert len(out["days"]) == 11

    def test_dates_are_consecutive(self) -> None:
        out = build_itinerary(start_date="2026-09-15", nights=3)
        assert [d["date"] for d in out["days"]] == [
            "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18",
        ]

    def test_bad_start_date_errors(self) -> None:
        assert build_itinerary(start_date="15-09-2026", nights=2).get("error") is True

    def test_policy_echoed_for_transparency(self) -> None:
        out = build_itinerary(start_date="2026-09-15", nights=1)
        assert out["policy"]["rest_after_landing_min"] == 180

    def test_no_tours_is_not_an_error(self) -> None:
        out = build_itinerary(start_date="2026-09-15", nights=2, arrival_time="10:00")
        assert out["excluded"] == []
        assert len(out["days"]) == 3

    def test_non_dict_tours_ignored(self) -> None:
        out = build_itinerary(
            start_date="2026-09-15", nights=2, tours=["junk", None, 42],  # type: ignore[list-item]
        )
        assert out["excluded"] == []


class TestFinishTimeGuard:
    """Guarding only the START time let a long tour run to a silly hour.

    Live regression: BAPS Hindu Temple (5h, no slots) was placed 18:30-23:30
    because 18:30 is before the 21:00 latest-start. A tour must also FINISH at a
    civil hour.
    """

    def test_long_tour_not_started_late_in_the_evening(self) -> None:
        out = build_itinerary(
            start_date="2026-09-15", nights=4, arrival_time="06:00",
            hotel_checkin_time="07:00",
            tours=[
                {"name": "All Day Tour", "duration": "8 Hours (Approx)"},
                {"name": "Long Temple Tour", "duration": "5 Hours (Approx)"},
            ],
        )
        for d in out["days"]:
            for i in d["items"]:
                if i["kind"] == "tour" and i["end"]:
                    assert i["end"] <= "23:00", f"{i['title']} ends {i['end']}"

    def test_short_evening_tour_still_allowed(self) -> None:
        """The guard must not block genuinely short evening activities."""
        out = build_itinerary(
            start_date="2026-09-15", nights=1, arrival_time="06:00",
            hotel_checkin_time="07:00",
            tours=[{"name": "Fountain Show", "duration": "30 Minutes (Approx)",
                    "timeslots": _slots("21:45")}],
        )
        placed = [i for d in out["days"] for i in d["items"] if i["kind"] == "tour"]
        assert placed and placed[0]["start"] == "21:45"

    def test_late_slot_skipped_for_a_long_tour(self) -> None:
        """A slot-based tour should take an EARLIER slot rather than be dropped."""
        out = build_itinerary(
            start_date="2026-09-15", nights=2, arrival_time="06:00",
            hotel_checkin_time="07:00",
            tours=[{"name": "Long Cruise", "duration": "4 Hours (Approx)",
                    "timeslots": _slots("12:00", "20:00")}],
        )
        placed = [i for d in out["days"] for i in d["items"] if i["kind"] == "tour"]
        assert placed and placed[0]["start"] == "12:00", (
            "20:00 + 4h = 00:00, so the 12:00 slot must be chosen"
        )

    def test_finish_guard_is_configurable(self) -> None:
        p = SchedulePolicy(latest_activity_end=time(18, 0))
        out = build_itinerary(
            start_date="2026-09-15", nights=1, arrival_time="06:00",
            hotel_checkin_time="07:00", policy=p,
            tours=[{"name": "Evening Tour", "duration": "2 Hours (Approx)",
                    "timeslots": _slots("17:00")}],
        )
        assert out["excluded"], "17:00 + 2h = 19:00, past an 18:00 finish limit"


class TestPdfCompatibility:
    """The PDF renderer reads `d.get("title")` per day and
    `{title, start, detail, kind}` per item (itinerary_pdf.py). Emitting only
    `label`/`date` left every day heading in the generated PDF blank.
    """

    def test_day_exposes_a_title_for_the_pdf(self) -> None:
        out = build_itinerary(start_date="2026-09-15", nights=2, arrival_time="16:00")
        for d in out["days"]:
            assert d["title"].strip(), "PDF day heading would render blank"
            assert d["title"].startswith("Day ")

    def test_arrival_label_included_in_title(self) -> None:
        out = build_itinerary(start_date="2026-09-15", nights=2, arrival_time="16:00")
        assert "Arrival" in out["days"][0]["title"]

    def test_items_use_the_keys_the_pdf_reads(self) -> None:
        out = build_itinerary(
            start_date="2026-09-15", nights=2, arrival_time="06:00",
            hotel_checkin_time="07:00",
            tours=[{"name": "Burj Khalifa", "duration": "2 Hours (Approx)"}],
        )
        items = [i for d in out["days"] for i in d["items"]]
        assert items
        for i in items:
            assert {"title", "start", "detail", "kind"} <= set(i)


class TestComputedTripTotal:
    """The model wrote its OWN "ESTIMATED TOTAL" table — twice, differing by
    ₹3,000, and both far under the truth.

    Live: it guessed tours at ~₹25,000 then ~₹28,000 for five tours that
    actually cost ₹43,264 for four adults, and quoted one room's hotel rate for
    a four-adult party. The prompt already forbids estimates; a 26B model does
    not hold that rule under a full context, so the arithmetic moved into code.
    """

    TOURS = [
        {"name": "Old Town Tour", "price_per_adult_inr": 1826.0, "duration": "5 Hours (Approx)"},
        {"name": "Canal Yacht", "price_per_adult_inr": 1759.0, "duration": "1 Hour (Approx)"},
        {"name": "At The Top", "price_per_adult_inr": 4271.0, "duration": "2 Hours (Approx)"},
    ]

    def _plan(self, **kw: object) -> dict:
        from agent_tools import plan_itinerary_tool

        args = {
            "start_date": "2026-09-01",
            "nights": 5,
            "adults": 4,
            "tours": self.TOURS,
            "arrival_time": "09:40",
            # Costing tests must own the tour list exactly; auto-fill would add
            # catalogue tours and change every expected sum.
            "fill_days": False,
        }
        args.update(kw)
        return plan_itinerary_tool.invoke(args)

    def test_tours_are_multiplied_by_the_party(self) -> None:
        out = self._plan()
        expected = (1826.0 + 1759.0 + 4271.0) * 4
        assert out["cost_breakdown"]["tours"] == pytest.approx(expected, abs=1.0)

    def test_per_tour_costs_are_itemised(self) -> None:
        """So the agent can show the line items instead of one guessed number."""
        out = self._plan()
        assert len(out["tour_costs"]) == 3
        for t in out["tour_costs"]:
            assert t["group_inr"] == pytest.approx(t["per_adult_inr"] * 4, abs=1.0)

    def test_visa_is_per_adult_times_party(self) -> None:
        out = self._plan(visa_per_adult_inr=7626)
        assert out["cost_breakdown"]["visa"] == pytest.approx(7626 * 4, abs=1.0)

    def test_total_is_the_sum_of_the_breakdown(self) -> None:
        out = self._plan(flight_total_inr=236269, hotel_total_inr=46392, visa_per_adult_inr=7626)
        assert out["total_inr"] == pytest.approx(sum(out["cost_breakdown"].values()), abs=1.0)

    def test_total_matches_the_verified_real_figure(self) -> None:
        """Guard the actual under-quote: agent said ₹2,88,868, truth was higher."""
        out = self._plan(flight_total_inr=236269, hotel_total_inr=46392, visa_per_adult_inr=7626)
        assert out["total_inr"] > 288868, "must not reproduce the under-quote"

    def test_unpriced_tour_is_named_not_guessed(self) -> None:
        out = self._plan(tours=[*self.TOURS, {"name": "Mystery Tour", "duration": "2 Hours"}])
        assert "Mystery Tour" in out["costing_note"]
        assert "guessing" in out["costing_note"].lower()

    def test_note_forbids_writing_your_own_total(self) -> None:
        out = self._plan()
        note = out["costing_note"].lower()
        assert "verbatim" in note
        assert "never write your own total" in note

    def test_missing_flight_and_hotel_is_disclosed(self) -> None:
        """A partial total must say it is partial, not look complete."""
        out = self._plan()
        assert "only what is listed" in out["costing_note"]

    def test_adults_defaults_safely(self) -> None:
        out = self._plan(adults=0)
        assert out["adults"] == 1


class TestSpreadAndFill:
    """Three tours on arrival day and four empty days is not an itinerary.

    Live: the agent planned BAPS + Dubai Citytour + Abu Dhabi City Tour ALL on
    day 1, then presented "Free day" three times as a finished 5-night plan.
    Greedy first-fit made every placement individually legal but the whole thing
    unusable.
    """

    THREE = [
        {"name": "Tour A", "price_per_adult_inr": 2420.0, "duration": "5 Hours (Approx)"},
        {"name": "Tour B", "price_per_adult_inr": 2022.0, "duration": "30 Minutes (Approx)"},
        {"name": "Tour C", "price_per_adult_inr": 2419.0, "duration": "8 Hours (Approx)"},
    ]

    def _plan(self, **kw: object) -> dict:
        args = {
            "start_date": "2026-09-01",
            "nights": 5,
            "tours": self.THREE,
            "arrival_time": "10:25",
            "departure_time": "23:10",
        }
        args.update(kw)
        return build_itinerary(**args)  # type: ignore[arg-type]

    def test_days_are_filled_not_rationed(self) -> None:
        """A short tour must NOT consume a whole day.

        The first attempt at spreading sorted by tour COUNT, so every day got
        one tour before any day got two and a 1-hour kayak became a full day.
        The budget is activity MINUTES: keep filling a day toward
        `target_activity_min_per_day` before moving to the next.
        """
        out = self._plan()
        per_day = [
            sum(1 for i in d["items"] if i["kind"] == "tour") for d in out["days"]
        ]
        assert max(per_day) >= 2, (
            f"short tours should share a day, got {per_day} — a 30-minute city "
            f"tour must not use up a day on a 5-night trip"
        )

    def test_a_full_day_tour_still_owns_its_day(self) -> None:
        """An 8-hour trip fills the budget on its own; nothing is crammed after."""
        out = self._plan(
            tours=[
                {"name": "Full Day", "price_per_adult_inr": 2419.0, "duration": "8 Hours (Approx)"},
                {"name": "Short One", "price_per_adult_inr": 541.0, "duration": "30 Minutes (Approx)"},
            ]
        )
        by_day = {
            d["day_number"]: [i["title"] for i in d["items"] if i["kind"] == "tour"]
            for d in out["days"]
        }
        full_day = next(n for n, t in by_day.items() if "Full Day" in t)
        assert by_day[full_day] == ["Full Day"], "nothing should follow an 8-hour tour"

    def test_all_three_still_placed(self) -> None:
        out = self._plan()
        placed = [i for d in out["days"] for i in d["items"] if i["kind"] == "tour"]
        assert len(placed) == 3
        assert not out["excluded"]

    def test_empty_days_reported(self) -> None:
        """The caller must be told to go and find more tours."""
        out = self._plan()
        assert out["empty_days"], "3 tours cannot fill a 5-night trip"
        assert "Search for more tours" in out["planning_note"]

    def test_departure_day_not_counted_as_empty(self) -> None:
        """It is meant to be clear — flagging it would send the agent hunting."""
        out = self._plan()
        assert out["days"][-1]["day_number"] not in out["empty_days"]

    def test_no_gap_when_there_is_enough_to_do(self) -> None:
        """Enough activity minutes to cover every day -> no gap reported.

        Each day takes ~7 hours of activity, so filling 5 days needs roughly
        that much per day, not merely five tours.
        """
        tours = [
            {"name": f"T{i}", "price_per_adult_inr": 1000.0, "duration": "7 Hours (Approx)"}
            for i in range(6)
        ]
        out = self._plan(tours=tours)
        assert out["empty_days"] == [], f"unexpected gaps: {out['empty_days']}"
        assert "Every day has at least one activity" in out["planning_note"]

    def test_chronological_order_preserved_within_a_day(self) -> None:
        out = self._plan()
        for d in out["days"]:
            starts = [i["start"] for i in d["items"] if i["start"]]
            assert starts == sorted(starts)


class TestAutoFillDays:
    """Telling the model to "search for more tours and call again" did not work.

    It printed "Free Day — relax at your own pace" three times and called a
    5-night trip complete. So the planner tops the list up itself.
    """

    THIN = [{"name": "Abu Dhabi City Tour from Dubai"}]

    def _plan(self, **kw: object) -> dict:
        from agent_tools import plan_itinerary_tool

        args = {
            "start_date": "2026-09-01",
            "nights": 5,
            "adults": 4,
            "tours": self.THIN,
            "arrival_time": "05:25",
            "departure_time": "15:00",
        }
        args.update(kw)
        return plan_itinerary_tool.invoke(args)

    def test_thin_list_is_topped_up(self) -> None:
        out = self._plan()
        assert out["auto_filled_tours"], "one tour cannot fill a 5-night trip"

    def test_far_fewer_free_days_than_before(self) -> None:
        """One tour used to leave four days blank."""
        out = self._plan()
        assert len(out["empty_days"]) <= 1

    def test_additions_are_named_so_they_can_be_swapped(self) -> None:
        out = self._plan()
        for name in out["auto_filled_tours"]:
            assert isinstance(name, str) and name.strip()

    def test_no_duplicate_attractions(self) -> None:
        """The catalogue lists "Dubai Frame" AND "Dubai Frame Ticket" — booking a
        customer onto both is embarrassing."""
        out = self._plan()
        titles = [
            i["title"].lower()
            for d in out["days"]
            for i in d["items"]
            if i["kind"] == "tour"
        ]
        drop = {"the", "a", "an", "in", "of", "at", "to", "from", "with", "and",
                "dubai", "abu", "dhabi", "ticket", "tickets", "tour", "tours",
                "experience", "entry", "pass", "combo"}
        keys = [
            " ".join(sorted({w for w in t.replace("-", " ").split() if w not in drop}))
            for t in titles
        ]
        assert len(keys) == len(set(keys)), f"duplicate attractions booked: {titles}"

    def test_no_day_is_overloaded(self) -> None:
        """An earlier over-shoot crammed six tours onto one day."""
        out = self._plan()
        per_day = [
            sum(1 for i in d["items"] if i["kind"] == "tour") for d in out["days"]
        ]
        assert max(per_day) <= 4, f"day overloaded: {per_day}"

    def test_auto_fill_can_be_switched_off(self) -> None:
        out = self._plan(fill_days=False)
        assert out["auto_filled_tours"] == []

    def test_auto_filled_tours_are_costed(self) -> None:
        """Additions must appear in the total, not arrive free."""
        out = self._plan()
        assert out["cost_breakdown"]["tours"] > 0
        assert len(out["tour_costs"]) > 1


class TestBareTourNameGetsPriced:
    """The model sends [{"name": "Dubai Citytour"}] with no price, having shown
    the customer ₹2,022 two messages earlier. The total then read "Tours:
    pricing to be confirmed" and silently EXCLUDED them — ₹3,56,031 instead of
    ₹3,83,476.
    """

    def test_lookup_matches_exact_name(self) -> None:
        from agent_tools import _tour_price_lookup

        assert _tour_price_lookup("Dubai Citytour", "2026-09-01") > 0

    def test_lookup_matches_a_shortened_name(self) -> None:
        """"Abu Dhabi City Tour" for "Abu Dhabi City Tour from Dubai"."""
        from agent_tools import _tour_price_lookup

        assert _tour_price_lookup("Abu Dhabi City Tour", "2026-09-01") > 0

    def test_unknown_tour_returns_zero_not_a_guess(self) -> None:
        from agent_tools import _tour_price_lookup

        assert _tour_price_lookup("Completely Made Up Experience XYZ", "2026-09-01") == 0.0

    def test_empty_name_is_safe(self) -> None:
        from agent_tools import _tour_price_lookup

        assert _tour_price_lookup("", "2026-09-01") == 0.0


class TestNoFreeDaysEver:
    """"Free Day — relax at your own pace" is gone for good.

    Client: "remove the free day logic at all simple and permanent fix". Two
    limits now hold the plan together: the fill loop RE-SCHEDULES until
    `empty_days` is empty, and `max_activities_per_day` stops the other extreme
    (removing the gaps first produced days with 5-7 tours).
    """

    ONE_TOUR = [{"name": "Abu Dhabi City Tour from Dubai"}]

    def _plan(self, nights: int, **kw: object) -> dict:
        from agent_tools import plan_itinerary_tool

        args = {
            "start_date": "2026-09-01",
            "nights": nights,
            "adults": 4,
            "tours": self.ONE_TOUR,
            "arrival_time": "05:25",
            "departure_time": "20:50",
        }
        args.update(kw)
        return plan_itinerary_tool.invoke(args)

    @pytest.mark.parametrize("nights", [3, 5, 7])
    def test_no_empty_days_at_any_length(self, nights: int) -> None:
        out = self._plan(nights)
        assert out["empty_days"] == [], (
            f"{nights}-night trip left days {out['empty_days']} blank"
        )

    @pytest.mark.parametrize("nights", [3, 5, 7])
    def test_no_day_is_a_conveyor_belt(self, nights: int) -> None:
        """Removing the gaps must not swing to 6 tours a day."""
        from scheduling import SchedulePolicy

        cap = SchedulePolicy().max_activities_per_day
        out = self._plan(nights)
        per_day = [
            sum(1 for i in d["items"] if i["kind"] == "tour") for d in out["days"]
        ]
        assert max(per_day) <= cap, f"day overloaded: {per_day}"

    def test_every_non_departure_day_has_something(self) -> None:
        out = self._plan(5)
        for d in out["days"][:-1]:  # last day is departure
            has_tour = any(i["kind"] == "tour" for i in d["items"])
            is_arrival = any(i["kind"] == "flight" for i in d["items"])
            assert has_tour or is_arrival, f"day {d['day_number']} is bare"

    def test_planning_note_confirms_full_coverage(self) -> None:
        out = self._plan(5)
        assert "Every day has at least one activity" in out["planning_note"]

    def test_additions_are_all_costed(self) -> None:
        """A filled day must not arrive free of charge."""
        out = self._plan(5)
        assert out["cost_breakdown"]["tours"] > 0
        assert len(out["tour_costs"]) >= len(out["auto_filled_tours"])

    def test_still_no_duplicate_attractions(self) -> None:
        out = self._plan(7)
        titles = [
            i["title"].lower()
            for d in out["days"]
            for i in d["items"]
            if i["kind"] == "tour"
        ]
        drop = {"the", "a", "an", "in", "of", "at", "to", "from", "with", "and",
                "dubai", "abu", "dhabi", "ticket", "tickets", "tour", "tours",
                "experience", "entry", "pass", "combo"}
        keys = [
            " ".join(sorted({w for w in t.replace("-", " ").split() if w not in drop}))
            for t in titles
        ]
        assert len(keys) == len(set(keys)), f"duplicates booked: {titles}"

    def test_opt_out_still_works(self) -> None:
        """fill_days=False must leave the caller's list untouched."""
        out = self._plan(5, fill_days=False)
        assert out["auto_filled_tours"] == []


class TestPdfReusesThePlan:
    """Every PDF request used to bounce once on MissingDayPlans.

    The guard is right to refuse a PDF with no itinerary, but the model then had
    to re-send a schedule `plan_itinerary_tool` had built seconds earlier — one
    wasted round-trip on every single request. The plan is now cached and reused.
    """

    def test_pdf_succeeds_without_day_plans(self) -> None:
        from agent_tools import generate_itinerary_pdf_tool, plan_itinerary_tool

        plan_itinerary_tool.invoke(
            {
                "start_date": "2026-09-01",
                "nights": 5,
                "adults": 4,
                "tours": [{"name": "Abu Dhabi City Tour from Dubai"}],
                "arrival_time": "05:25",
            }
        )
        out = generate_itinerary_pdf_tool.invoke(
            {
                "destination": "Dubai",
                "start_date": "2026-09-01",
                "end_date": "2026-09-06",
                "nights": 5,
                "party_summary": "4 adults",
                "total_inr": 372586,
            }
        )
        assert not out.get("error"), out
        assert out.get("download_url")

    def test_still_refuses_when_no_plan_was_ever_built(self) -> None:
        """The guard must not become a rubber stamp — a hollow PDF is worse."""
        import agent_tools
        from agent_tools import generate_itinerary_pdf_tool

        saved = agent_tools._LAST_PLAN
        agent_tools._LAST_PLAN = {}
        try:
            out = generate_itinerary_pdf_tool.invoke(
                {
                    "destination": "Dubai",
                    "start_date": "2026-12-01",
                    "end_date": "2026-12-06",
                    "nights": 5,
                    "party_summary": "4 adults",
                }
            )
            assert out.get("error_type") == "MissingDayPlans"
        finally:
            agent_tools._LAST_PLAN = saved

    def test_stale_plan_for_other_dates_is_not_reused(self) -> None:
        """A plan for a different trip must not leak into this PDF."""
        import agent_tools
        from agent_tools import generate_itinerary_pdf_tool, plan_itinerary_tool

        plan_itinerary_tool.invoke(
            {"start_date": "2026-09-01", "nights": 5, "adults": 4,
             "tours": [{"name": "Abu Dhabi City Tour from Dubai"}]}
        )
        out = generate_itinerary_pdf_tool.invoke(
            {
                "destination": "Dubai",
                "start_date": "2027-03-15",  # different trip entirely
                "end_date": "2027-03-20",
                "nights": 5,
                "party_summary": "4 adults",
            }
        )
        assert out.get("error_type") == "MissingDayPlans"
