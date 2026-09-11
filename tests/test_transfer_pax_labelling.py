"""Transfer prices on a search row are TOTALS, and must say so.

Asked "we are 7 people, what is the total for both transfers?", the agent took
the 1-pax figures off the row and multiplied by seven: it answered sharing
Rs 12,551 and private Rs 80,983 against a real Rs 1,793 and Rs 23,138. The row
carried no party size, so "Sharing Transfer: Rs 1,793" read as a per-head unit.

Measured live on tour 30647 (Dubai Desert Safari), 2026-09-15:

    pax | sharing | private
      1 |   1,793 |  11,569
      6 |   1,793 |  11,569
      7 |   1,793 |  23,138   <- second vehicle, added by the supplier
     12 |   1,793 |  23,138

Private steps at the 6-seat vehicle boundary and sharing is flat, so NEITHER
tier is per person on this tour. The fix is that each price carries
`priced_for_pax`.
"""

from unittest.mock import patch

import pytest

from mcp_tools import search_tours


class _Opt:
    """Minimal stand-in for a parsed tour option."""

    def __init__(self):
        self.tour_id = 30647
        self.supplier_id = 2
        self.transfer_prices = None
        self.transfer_price_display = ""


def _fake_options(**_kw):
    return {"result": {"tourOptionlist": [{
        "optionId": 1, "supplierId": 2,
        "validateTourOption": [{"transferTypeId": 2}],
    }]}}


def _rate_for(private_aed):
    def _call(**kw):
        tid = kw.get("transfer_id")
        rate = private_aed if tid == 2 else 100.0
        return {"result": [{
            "rate": rate,
            "initialTransferRates": [
                {"transferTypeId": 1, "transferTypeName": "Sharing Transfer",
                 "startingFromRate": 100.0, "currencyCode": "AED"},
                {"transferTypeId": 2, "transferTypeName": "Private Transfer",
                 "startingFromRate": private_aed, "currencyCode": "AED"},
            ],
        }]}
    return _call


@pytest.mark.parametrize("pax", [1, 6, 7, 12])
def test_every_transfer_price_is_labelled_with_its_party_size(pax):
    opts = [_Opt()]
    with patch.object(search_tours, "_attach_transfer_prices",
                      search_tours._attach_transfer_prices):
        with patch("booking_api.endpoints.call_tour_options", _fake_options), \
             patch("booking_api.endpoints.call_tour_option_rate", _rate_for(645.0)):
            search_tours._attach_transfer_prices(opts, "2026-09-15", pax)

    prices = opts[0].transfer_prices or []
    assert prices, "expected transfer prices to be attached"
    for p in prices:
        assert p["priced_for_pax"] == pax, (
            "an unlabelled price reads as per-head and gets multiplied again"
        )


def test_the_display_string_says_it_is_a_party_total():
    opts = [_Opt()]
    with patch("booking_api.endpoints.call_tour_options", _fake_options), \
         patch("booking_api.endpoints.call_tour_option_rate", _rate_for(645.0)):
        search_tours._attach_transfer_prices(opts, "2026-09-15", 7)
    assert "total for 7 pax" in opts[0].transfer_price_display


def test_a_solo_search_does_not_claim_a_party_total():
    opts = [_Opt()]
    with patch("booking_api.endpoints.call_tour_options", _fake_options), \
         patch("booking_api.endpoints.call_tour_option_rate", _rate_for(645.0)):
        search_tours._attach_transfer_prices(opts, "2026-09-15", 1)
    assert "total for" not in opts[0].transfer_price_display
