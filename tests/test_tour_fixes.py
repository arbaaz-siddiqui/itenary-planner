"""Tour fixes from the client's Tour Suggestions Report (October 2026).

Prices, children and the travel date are decided in code, not by the model:

* a tour is priced only for a day the customer named;
* children are sent to the supplier, so the total includes them;
* each total is the supplier's own grand total per transfer type.
"""

from unittest.mock import patch

import pytest

import rules


@pytest.fixture
def said():
    def _set(text: str) -> None:
        rules.set_conversation_text(text)

    yield _set
    rules.set_conversation_text("")


class TestCustomerGaveDate:
    @pytest.mark.parametrize("text", [
        "22 oct", "on the 5th", "tomorrow", "next friday please", "in december",
        "22/10", "3 may", "2026-10-22", "this weekend",
    ])
    def test_a_named_day_counts(self, said, text):
        said(text)
        assert rules.customer_gave_date()

    @pytest.mark.parametrize("text", [
        "how much is the desert safari for 2",
        "2 adults and 2 children aged 5 and 8",
        "I may be travelling with family",
        "we'd like a sun and sand tour",
    ])
    def test_no_day_does_not(self, said, text):
        said(text)
        assert not rules.customer_gave_date()

    def test_no_conversation_text_fails_open(self, said):
        said("")
        assert rules.customer_gave_date()


class TestUndatedPriceIsRefused:
    def test_get_tour_options_asks_for_the_date(self, said):
        from mcp_tools.get_tour_options import get_tour_options

        said("price of the desert safari for 2 adults")
        with patch("mcp_tools.get_tour_options.tour_options") as priced:
            out = get_tour_options(53632, "2026-10-22", 2)
        assert out["error_type"] == "NeedsCustomerInput"
        assert out["missing_fields"] == ["travel_date"]
        priced.assert_not_called()

    def test_search_tours_asks_for_the_date(self, said):
        from mcp_tools.search_tours import search_tours_tool

        said("show me desert safaris")
        with patch("mcp_tools.search_tours._cached_impl") as searched:
            out = search_tours_tool.invoke({
                "destination_city": "Dubai", "travel_date": "2026-10-22",
                "adults": 2,
            })
        assert out["error_type"] == "NeedsCustomerInput"
        searched.assert_not_called()

    def test_the_model_cannot_waive_the_date_for_the_customer(self, said):
        from mcp_tools.get_tour_options import get_tour_options

        said("What's the exact price for Dubai Desert Safari with private transfer for 2 people?")
        with patch("mcp_tools.get_tour_options.tour_options") as priced:
            out = get_tour_options(30647, "2026-11-01", 2, assume_missing=True)
        assert out["error_type"] == "NeedsCustomerInput"
        priced.assert_not_called()

    def test_the_customer_can_waive_it(self, said):
        from mcp_tools.get_tour_options import get_tour_options
        from mcp_tools.result_cache import clear_result_cache

        clear_result_cache()
        said("desert safari price for 2? not sure of dates yet, rough estimate is fine")
        with patch("mcp_tools.get_tour_options.tour_options",
                   return_value={"options": [{"name": "x"}]}) as priced:
            get_tour_options(30647, "2026-11-01", 2, assume_missing=True)
        priced.assert_called_once()

    def test_a_dated_request_goes_through(self, said):
        from mcp_tools.get_tour_options import get_tour_options
        from mcp_tools.result_cache import clear_result_cache

        clear_result_cache()
        said("desert safari on 22 oct for 2 adults and kids aged 5 and 8")
        with patch("mcp_tools.get_tour_options.tour_options",
                   return_value={"options": [{"name": "x"}]}) as priced:
            get_tour_options(53632, "2026-10-22", 2, child_ages=[5, 8])
        assert priced.call_args.args[3] == [5, 8]


class TestCustomerWordsKeepTheFirstMessage:
    def test_a_memory_prefix_does_not_hide_the_customer_text(self):
        from agent import _customer_part

        first = "[system] Known about this customer: city: Ahmedabad\n\nBurj Khalifa on 22 October"
        assert _customer_part(first) == "Burj Khalifa on 22 October"

    def test_a_bare_marker_contributes_nothing(self):
        from agent import _customer_part

        assert _customer_part("[system] The previous turn produced no reply.") == ""
        assert _customer_part("hello") == "hello"


class TestStepBudget:
    def test_six_tool_rounds_fit(self):
        import agent

        # pre hook + model + post hook + tools per round.
        assert agent._STEPS_PER_TOOL_ROUND == 4


class TestPartyTotalOnSearchRows:
    """Cards show per-person prices; the reply quotes the party total."""

    def _row(self, prices, per_adult):
        from types import SimpleNamespace

        return SimpleNamespace(transfer_prices=prices, price_per_adult_inr=per_adult)

    def test_uses_the_supplier_party_total_when_there_is_one(self):
        from mcp_tools.search_tours import _party_total

        got = _party_total(self._row([{"total_inr": 13290.34}, {"total_inr": 5350.66}], 2675.33), 2, None)
        assert got == "from ₹5,350.66 total for 2 adults"

    def test_ticket_only_adults_multiply_the_ticket(self):
        from mcp_tools.search_tours import _party_total

        assert _party_total(self._row([], 1830.0), 2, None) == "₹3,660 total for 2 adults"

    def test_ticket_only_with_children_does_not_guess(self):
        from mcp_tools.search_tours import _party_total

        assert "child price on request" in _party_total(self._row([], 1830.0), 2, [6])


def test_party_totals_line_lists_the_top_three_with_their_totals():
    from mcp_tools.search_tours import _party_totals_line

    rows = [
        {"name": "A", "party_total_display": "from ₹9,780.77 total for 2 adults"},
        {"name": "B", "party_total_display": "₹3,660 total for 2 adults"},
        {"name": "C", "party_total_display": "₹1,830 per adult; child price on request"},
        {"name": "D", "party_total_display": "₹4,000 total for 2 adults"},
    ]
    assert _party_totals_line(rows, "2 adults") == "**Total for 2 adults:** A from ₹9,780.77, B ₹3,660."
