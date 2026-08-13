"""Tests for the flight leg-summary helpers.

The supplier returns departure/arrival times, terminals, aircraft, seats and
operating-vs-marketing airline per segment, but replies only ever quoted
airline + price. These helpers flatten the nested segments into the handful of
fields the agent quotes, so the detail is available without the model walking
(or inventing) the structure.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_tools.search_flights import _clock, _fmt_hm, _leg_summary


class TestFormatDuration:
    @pytest.mark.parametrize(
        "minutes,expected",
        [(184, "3h 04m"), (180, "3h 00m"), (59, "0h 59m"), (1440, "24h 00m")],
    )
    def test_formats_minutes(self, minutes: int, expected: str) -> None:
        assert _fmt_hm(minutes) == expected

    @pytest.mark.parametrize("bad", [None, 0, -5, "", "abc", [], {}])
    def test_unknown_yields_empty(self, bad: Any) -> None:
        assert _fmt_hm(bad) == ""

    def test_numeric_string_is_accepted(self) -> None:
        assert _fmt_hm("184") == "3h 04m"


class TestClock:
    def test_extracts_hhmm(self) -> None:
        assert _clock("2026-09-15 22:25:00") == "22:25"

    @pytest.mark.parametrize("bad", [None, "", "2026-09-15", "garbage", 12345])
    def test_unparseable_yields_empty(self, bad: Any) -> None:
        assert _clock(bad) == ""


class TestLegSummary:
    def _seg(self, **over: Any) -> dict[str, Any]:
        base = {
            "from_airport": "BOM",
            "to_airport": "DXB",
            "departure": "2026-09-15 22:25:00",
            "arrival": "2026-09-15 23:59:00",
            "departure_terminal": "2",
            "arrival_terminal": "3",
            "flight_number": "509",
            "aircraft": "Boeing",
            "seats_remaining": 9,
            "marketing_airline": "Emirates",
            "operating_airline": "Emirates",
            "layover_min": 0,
        }
        base.update(over)
        return base

    def test_nonstop_summary(self) -> None:
        out = _leg_summary([self._seg()])
        assert out["departure_time"] == "22:25"
        assert out["arrival_time"] == "23:59"
        assert out["departure_terminal"] == "2"
        assert out["flight_numbers"] == ["509"]
        assert out["seats_remaining"] == 9
        assert out["codeshare"] == ""   # same marketing/operating carrier
        assert out["layovers"] == []

    def test_uses_first_departure_and_last_arrival(self) -> None:
        """On a multi-leg trip the times must span the whole journey."""
        legs = [
            self._seg(departure="2026-09-15 21:35:00", arrival="2026-09-15 23:00:00"),
            self._seg(departure="2026-09-16 01:00:00", arrival="2026-09-16 06:10:00",
                      flight_number="539", layover_min=120, from_airport="DEL"),
        ]
        out = _leg_summary(legs)
        assert out["departure_time"] == "21:35"
        assert out["arrival_time"] == "06:10"
        assert out["flight_numbers"] == ["509", "539"]
        assert out["layovers"] == ["DEL 2h 00m"]

    def test_codeshare_is_flagged(self) -> None:
        """Sold by one airline, flown by another — customers need to know."""
        out = _leg_summary([self._seg(marketing_airline="Emirates",
                                      operating_airline="flydubai")])
        assert out["codeshare"] == "Emirates (operated by flydubai)"

    @pytest.mark.parametrize("bad", [None, [], "", {}, ["not-a-dict"]])
    def test_missing_segments_yield_empty(self, bad: Any) -> None:
        assert _leg_summary(bad) == {}

    def test_absent_fields_do_not_crash(self) -> None:
        out = _leg_summary([{"from_airport": "BOM"}])
        assert out["departure_time"] == ""
        assert out["flight_numbers"] == []
