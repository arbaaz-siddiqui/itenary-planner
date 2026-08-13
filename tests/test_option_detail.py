"""Detail fields the APIs return but the reply used to drop.

The client's complaint: "in flights we have baggage weight, timing, all the
details that are required to know, and same with hotel". All of it was already
being fetched — the prompt suppressed it and the flat summaries omitted it.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_tools.search_flights import _baggage_display
from mcp_tools.search_hotels import _cancellation_display


class TestBaggageDisplay:
    def test_checked_and_cabin(self) -> None:
        assert _baggage_display(["25kg"], ["7kg"]) == "25kg check-in + 7kg cabin"

    def test_checked_only(self) -> None:
        assert _baggage_display(["30kg"], []) == "30kg check-in"

    def test_multiple_allowances_are_listed(self) -> None:
        """Different legs can carry different allowances."""
        assert _baggage_display(["25kg", "23kg"], ["7kg"]) == (
            "25kg / 23kg check-in + 7kg cabin"
        )

    def test_duplicates_collapse(self) -> None:
        assert _baggage_display(["25kg", "25kg"], []) == "25kg check-in"

    @pytest.mark.parametrize(
        "checked", [["0pc"], ["0kg"], ["0"], ["none"], ["0pc", "0pc"], [], None, "25kg"]
    )
    def test_placeholder_zero_is_not_reported_as_no_baggage(self, checked: Any) -> None:
        """'0pc' means "not specified", not "you get nothing"."""
        assert _baggage_display(checked, []) == ""


class TestCancellationDisplay:
    def _option(self, **term: Any) -> dict[str, Any]:
        base = {
            "from_date": "08-13-2026",
            "to_date": "11-25-2026",
            "cancellation_price": 0.0,
            "is_free_cancellation": True,
            "is_nrf": False,
        }
        base.update(term)
        return {"has_free_cancellation": base["is_free_cancellation"],
                "rooms": [{"cancellation_policy": [base]}]}

    def test_free_with_deadline(self) -> None:
        assert _cancellation_display(self._option()) == (
            "Free cancellation until 11-25-2026"
        )

    def test_fee_with_date(self) -> None:
        out = _cancellation_display(
            self._option(is_free_cancellation=False, cancellation_price=32016.94,
                         to_date="12-01-2026")
        )
        assert "32,016" in out and "12-01-2026" in out

    def test_non_refundable_flag(self) -> None:
        out = _cancellation_display(self._option(is_free_cancellation=False, is_nrf=True))
        assert out == "Non-refundable"

    @pytest.mark.parametrize("rooms", [None, [], "x", [{}], [{"cancellation_policy": []}]])
    def test_missing_policy_does_not_invent_terms(self, rooms: Any) -> None:
        """Never guess at a customer's refund rights."""
        out = _cancellation_display({"has_free_cancellation": False, "rooms": rooms})
        assert out == ""

    def test_free_flag_without_policy_detail(self) -> None:
        out = _cancellation_display({"has_free_cancellation": True, "rooms": []})
        assert out == "Free cancellation"
