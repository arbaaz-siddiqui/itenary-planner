"""Transfer prices on a search row are TOTALS, and must say so.

Asked "we are 7 people, what is the total for both transfers?", the agent took
the 1-pax figures off the row and multiplied by seven: it answered sharing
Rs 12,551 and private Rs 80,983 against a real Rs 1,793 and Rs 23,138. The row
carried no party size, so "Sharing Transfer: Rs 1,793" read as a per-head unit.

Each figure is now the supplier's grand total for the whole party on that
transfer type (ticket included), and carries `priced_for_pax` plus a display
that names the party.
"""

from unittest.mock import patch

import pytest

from mcp_tools import search_tours
from tests.factories import future_date


class _Opt:
    """Minimal stand-in for a parsed tour option."""

    def __init__(self):
        self.tour_id = 30647
        self.supplier_id = 2
        self.transfer_prices = None
        self.transfer_price_display = ""
        self.priced_option_name = ""


def _fake_options(**_kw):
    return {"result": {"tourOptionlist": [{
        "optionId": 1, "supplierId": 2, "optionName": "Dubai Desert Safari",
        "validateTourOption": [{"transferTypeId": 2}],
    }]}}


def _rate_for(private_aed):
    """The supplier's grand total for the party on the requested transfer."""
    def _call(**kw):
        rate = 100.0 + (private_aed if kw["transfer_id"] == 2 else 0.0)
        return {"result": [{"rate": rate, "currencyCode": "AED"}]}
    return _call


def _attach(pax):
    opts = [_Opt()]
    with patch("booking_api.endpoints.call_tour_options", _fake_options), \
         patch("mcp_tools.tour_pricing.call_tour_option_rate", _rate_for(645.0)):
        search_tours._attach_transfer_prices(opts, future_date(14), pax)
    return opts[0]


@pytest.mark.parametrize("pax", [1, 6, 7, 12])
def test_every_transfer_price_is_labelled_with_its_party_size(pax):
    prices = _attach(pax).transfer_prices or []
    assert prices, "expected transfer prices to be attached"
    for p in prices:
        assert p["priced_for_pax"] == pax, (
            "an unlabelled price reads as per-head and gets multiplied again"
        )


def test_the_display_string_says_it_is_a_party_total():
    shown = _attach(7).transfer_price_display
    assert "total for 7 adults, ticket included" in shown


def test_a_solo_search_names_its_party_of_one():
    # Unlabelled, a 1-adult "Sharing Transfer: Rs 1,793" was read as a
    # per-person rate (fixed in 787dc70), so a solo search says so too.
    assert _attach(1).transfer_price_display.endswith(
        "(total for 1 adult, ticket included)"
    )
