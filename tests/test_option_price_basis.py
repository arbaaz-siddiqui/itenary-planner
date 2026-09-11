"""A variant's price basis comes from its name, and is not always per person.

Tour 30647 has two add-ons that price differently:

    "2 Drinks Package - Per Person (Add-on)"              per person
    "Private Majlis - Per Group up to 6 Guests (Add-on)"  per group

Both were divided by the party size and labelled "per adult", so the group
majlis showed as Rs 19,088 "per adult" -- neither the per-head cost nor what
the supplier bills.

Measured live on 2026-09-15, the supplier multiplies BOTH by pax:

    per-group  add-on: 726 / 1,452 / 4,356 AED at pax 1 / 2 / 6
    per-person add-on: 42.35 / 84.70 / 254.10 AED at pax 1 / 2 / 6

So the group item is billed once per guest. We report what will actually be
charged and say it is group-priced, rather than dividing it down to a
per-adult figure the supplier will not honour.
"""

from unittest.mock import patch

import pytest

from mcp_tools.get_tour_options import _impl

TOUR = 30647
DATE = "2026-09-15"

OPTIONS = [
    {"optionId": 1, "optionName": "Dubai Desert Safari",
     "supplierId": 2, "validateTourOption": [{"transferTypeId": 3}]},
    {"optionId": 2, "optionName": "2 Drinks Package - Per Person (Add-on)",
     "supplierId": 2, "validateTourOption": [{"transferTypeId": 3}]},
    {"optionId": 3, "optionName": "Private Majlis - Per Group up to 6 Guests (Add-on)",
     "supplierId": 2, "validateTourOption": [{"transferTypeId": 3}]},
]


def _run(adults: int) -> dict[str, dict]:
    raw = {"result": {"tourOptionlist": OPTIONS}}
    # 100 AED a head, so the per-adult figure is stable and the group figure
    # visibly is not.
    rate = {"result": [{"rate": 100.0 * adults, "currencyCode": "AED"}]}
    with patch("booking_api.endpoints.call_tour_options", lambda **k: raw), \
         patch("booking_api.endpoints.call_tour_option_rate", lambda **k: rate):
        out = _impl(tour_id=TOUR, travel_date=DATE, adults=adults)
    return {o["name"]: o for o in out.get("options") or []}


class TestPriceBasis:
    @pytest.mark.parametrize("adults", [1, 2, 7])
    def test_a_per_group_option_is_never_labelled_per_adult(self, adults):
        rows = _run(adults)
        majlis = rows["Private Majlis - Per Group up to 6 Guests (Add-on)"]
        assert majlis["price_basis"] == "per_group"
        assert "per adult" not in majlis["price_display"].lower()

    @pytest.mark.parametrize("adults", [1, 2, 7])
    def test_a_per_person_option_stays_per_adult(self, adults):
        rows = _run(adults)
        drinks = rows["2 Drinks Package - Per Person (Add-on)"]
        assert drinks["price_basis"] == "per_adult"
        assert "per adult" in drinks["price_display"].lower()

    def test_a_plain_variant_stays_per_adult(self):
        rows = _run(7)
        assert rows["Dubai Desert Safari"]["price_basis"] == "per_adult"

    def test_the_group_price_shown_is_what_the_supplier_bills(self):
        # Not divided down: the supplier charges the group item per guest, and
        # quoting a lower per-head figure would be a price it will not honour.
        rows = _run(7)
        majlis = rows["Private Majlis - Per Group up to 6 Guests (Add-on)"]
        # price_per_adult_inr is rounded to paise, so allow the rounding drift
        # rather than asserting exact equality of a derived figure.
        assert majlis["price_total_inr"] == pytest.approx(
            majlis["price_per_adult_inr"] * 7, abs=0.1
        )
        assert "7 guest" in majlis["price_display"]

    def test_the_group_row_warns_that_the_charge_is_per_guest(self):
        majlis = _run(7)["Private Majlis - Per Group up to 6 Guests (Add-on)"]
        assert "per guest" in str(majlis.get("price_note", "")).lower()
