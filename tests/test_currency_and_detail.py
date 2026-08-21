"""Supplier currency rule + the "no cancellation policy" false denial.

Two client-reported problems are pinned here.

1. CURRENCY. The supplier's own rule (client-confirmed 2026-08-21):
       base/system currency is ALWAYS AED, so fareInfo.price is in AED
       if creditlimitCurrencyCode == fareInfo.currency:  price / buyingROE
       else:                                            price * sellingROE
   Their worked examples:
       300.868335 / 0.0387979638 = 7750.00 INR
       300.868335 * 0.2750170184 =   82.75 USD

2. FALSE DENIAL. `_cancellation_display` returned "" when it could not parse
   terms, and the agent turned that silence into "there is no cancellation
   policy" — a factual error, since the supplier always has terms.
"""

from __future__ import annotations

from typing import Any

import pytest

import fx
from mcp_tools.search_hotels import _cancellation_display, _human_date


class TestSupplierCurrencyRule:
    """The branch is on fareInfo.currency vs the ACCOUNT currency."""

    @pytest.fixture(autouse=True)
    def _pin_rates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rates = {
            "INR": (0.0387979638, 26.33),
            "USD": (0.0117, 0.2750170184),
        }
        monkeypatch.setattr(fx, "_roe_pair", lambda c: rates.get(c.upper(), (None, None)))
        monkeypatch.setattr(fx, "account_currency_code", lambda: "INR")

    def test_client_inr_example(self) -> None:
        """Same currency as the account -> DIVIDE by buyingROE.

        300.868335 / 0.0387979638 = 7754.75, which we round to 7755. The client
        quoted 7750.00 in their example — their own rounding of the same figure,
        so a few rupees of tolerance is expected, not a formula difference.
        """
        amount, cur = fx.convert_supplier_price(300.868335, fare_currency="INR")
        assert cur == "INR"
        assert amount == pytest.approx(7755.0, abs=1.0)

    def test_client_usd_example(self) -> None:
        """Different currency -> MULTIPLY by sellingROE."""
        amount, cur = fx.convert_supplier_price(
            300.868335, fare_currency="INR", display_currency="USD"
        )
        assert cur == "USD"
        assert amount == pytest.approx(82.75, abs=0.05)

    def test_selling_roe_would_overquote_inr(self) -> None:
        """Guard the branch: multiplying instead of dividing overquotes ~2%."""
        divided, _ = fx.convert_supplier_price(300.868335, fare_currency="INR")
        multiplied = 300.868335 * 26.33
        assert multiplied > divided

    def test_inr_is_whole_rupees(self) -> None:
        """Paisa on a visa fee reads like a bug."""
        amount, _ = fx.convert_supplier_price(300.868335, fare_currency="INR")
        assert amount == int(amount)

    def test_usd_keeps_minor_units(self) -> None:
        amount, _ = fx.convert_supplier_price(
            300.868335, fare_currency="INR", display_currency="USD"
        )
        assert amount != int(amount), "USD must keep cents"

    def test_no_rate_returns_none_not_a_guess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(fx, "_roe_pair", lambda c: (None, None))
        assert fx.convert_supplier_price(100.0, fare_currency="INR") == (None, "INR")

    @pytest.mark.parametrize("bad", [0, -5, None, "abc"])
    def test_bad_input_rejected(self, bad: Any) -> None:
        assert fx.convert_supplier_price(bad)[0] is None


class TestRoePairCached:
    def test_repeated_conversions_make_one_network_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without caching, parsing 270 tours made 270 HTTP calls (43 SECONDS)."""
        fx.clear_fx_cache()
        calls = {"n": 0}

        def _fake(code: str) -> tuple[float, float]:
            calls["n"] += 1
            return (0.0387979638, 26.33)

        monkeypatch.setattr(fx, "_fetch_roe_pair", _fake)
        monkeypatch.setattr(fx, "account_currency_code", lambda: "INR")
        for _ in range(50):
            fx.convert_supplier_price(100.0, fare_currency="INR")
        assert calls["n"] == 1, f"expected 1 fetch, made {calls['n']}"


class TestNeverDenyCancellation:
    def _opt(self, **term: Any) -> dict[str, Any]:
        base = {
            "to_date": "11-25-2026",
            "cancellation_price": 0.0,
            "is_free_cancellation": True,
            "is_nrf": False,
        }
        base.update(term)
        return {
            "has_free_cancellation": base["is_free_cancellation"],
            "rooms": [{"cancellation_policy": [base]}],
        }

    @pytest.mark.parametrize(
        "rooms", [None, [], "junk", [{}], [{"cancellation_policy": []}], [{"cancellation_policy": "x"}]]
    )
    def test_never_returns_empty(self, rooms: Any) -> None:
        out = _cancellation_display({"has_free_cancellation": False, "rooms": rooms})
        assert out.strip(), "empty string is what made the agent deny the policy"

    @pytest.mark.parametrize(
        "rooms", [None, [], "junk", [{}], [{"cancellation_policy": []}]]
    )
    def test_unknown_never_claims_free_cancellation(self, rooms: Any) -> None:
        """Honest about not knowing — must not invent a refund right."""
        out = _cancellation_display({"has_free_cancellation": False, "rooms": rooms}).lower()
        assert "free cancellation" not in out
        assert "confirm" in out or "on request" in out

    def test_free_reads_naturally(self) -> None:
        assert _cancellation_display(self._opt()) == "Free cancellation until 25 Nov 2026"

    def test_non_refundable_still_stated_plainly(self) -> None:
        out = _cancellation_display(self._opt(is_free_cancellation=False, is_nrf=True))
        assert out == "Non-refundable"


class TestHumanDate:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("09-16-2026", "16 Sep 2026"),   # supplier MM-DD-YYYY
            ("11-25-2026", "25 Nov 2026"),
            ("2026-09-16", "16 Sep 2026"),   # ISO
        ],
    )
    def test_formats(self, raw: str, expected: str) -> None:
        assert _human_date(raw) == expected

    def test_ambiguous_date_is_disambiguated(self) -> None:
        """"10-09-2026" could be 9 Oct or 10 Sep — spelling the month settles it."""
        assert _human_date("10-09-2026") == "09 Oct 2026"

    @pytest.mark.parametrize("raw", ["", "garbage", "not-a-date"])
    def test_unparseable_passed_through(self, raw: str) -> None:
        assert _human_date(raw) == raw


class TestHotelContentExtraction:
    """A whole conversation went by with no amenities, food, or description —
    only prices. `search_hotels` returned none of it, and the agent almost never
    made the extra `get_hotel_description` call.

    Real Howard Johnson description text is used as the fixture.
    """

    HJ = (
        "Pamper yourself with onsite massages, body treatments, and facials. If "
        "you're looking for recreational opportunities, you'll find an outdoor "
        "pool, a sauna, and a 24-hour fitness center. Additional features at "
        "this hotel include complimentary wireless internet access, concierge "
        "services, and babysitting (surcharge). Grab a bite to eat at Creek View "
        "All Day Dining, one of the hotel's many dining establishments, which "
        "include 3 restaurants and a coffee shop/cafe. Buffet breakfasts are "
        "available daily from 6:30 AM to 11:00 AM for a fee. Featured amenities "
        "include a business center, limo/town car service, and dry "
        "cleaning/laundry services. Free valet parking is available onsite."
    )

    def test_amenities_extracted(self) -> None:
        from mcp_tools.search_hotels import _extract_amenities

        got = _extract_amenities(self.HJ)
        for expected in ("Pool", "Gym", "Spa", "Sauna", "Free WiFi", "Restaurant"):
            assert expected in got, f"missed {expected}"

    def test_amenities_not_invented(self) -> None:
        from mcp_tools.search_hotels import _extract_amenities

        got = _extract_amenities(self.HJ)
        assert "Beach access" not in got, "must not claim a beach the text never mentions"

    def test_empty_description_yields_nothing(self) -> None:
        from mcp_tools.search_hotels import _extract_amenities, _dining_summary

        assert _extract_amenities("") == []
        assert _dining_summary("") == ""

    def test_dining_counts_restaurants(self) -> None:
        from mcp_tools.search_hotels import _dining_summary

        out = _dining_summary(self.HJ)
        assert "3 restaurants" in out
        assert "coffee shop" in out
        assert "buffet breakfast" in out

    def test_dining_names_a_venue(self) -> None:
        """Customers ask "where can we eat?" — a name beats a count."""
        from mcp_tools.search_hotels import _dining_summary

        assert "Creek View All Day Dining" in _dining_summary(self.HJ)


class TestRefundableRoomSummary:
    """"Non-refundable" for every hotel was wrong — and it lost us business.

    `price_inr` is the CHEAPEST offer and the cheapest is usually
    non-refundable, so `cancellation_display` described that one rate. The agent
    presented it as the hotel's policy and told a customer who explicitly asked
    for refundable rooms that there were none. Live at the time: Social Hotel
    4/10 refundable, Howard Johnson 5/10, Novotel 2/10.
    """

    def _rooms(self, *specs: tuple[float, bool]) -> list[dict[str, Any]]:
        return [
            {
                "price_inr": price,
                "room_type_name": f"Room {i}",
                "cancellation_policy": [{"is_free_cancellation": free}],
            }
            for i, (price, free) in enumerate(specs)
        ]

    def _summarise(self, shown: float, rooms: list[dict[str, Any]]) -> dict[str, Any]:
        from mcp_tools.search_hotels import _summarise_refundable

        o: dict[str, Any] = {"price_inr": shown}
        _summarise_refundable(o, rooms)
        return o

    def test_finds_refundable_above_the_shown_price(self) -> None:
        o = self._summarise(19975.0, self._rooms((19975.0, False), (22194.8, True), (30000.0, True)))
        assert o["refundable_room_count"] == 2
        assert o["cheapest_refundable_inr"] == 22194.8
        assert "22,195" in o["refundable_display"]  # whole rupees, not paisa

    def test_states_the_ratio_so_it_is_not_a_blanket_claim(self) -> None:
        o = self._summarise(100.0, self._rooms((100.0, False), (200.0, True), (300.0, False)))
        assert "1 of 3" in o["refundable_display"]

    def test_says_so_when_genuinely_none(self) -> None:
        o = self._summarise(100.0, self._rooms((100.0, False), (200.0, False)))
        assert o["refundable_room_count"] == 0
        assert "no refundable" in o["refundable_display"].lower()
        assert "cheapest_refundable_inr" not in o

    def test_shown_rate_already_refundable(self) -> None:
        """Don't tell them to pay more for what they already have."""
        o = self._summarise(22194.8, self._rooms((22194.8, True), (30000.0, True)))
        assert o["refundable_display"] == "The rate shown is refundable"

    def test_no_rooms_is_handled(self) -> None:
        o = self._summarise(100.0, [])
        assert o["refundable_room_count"] == 0
        assert o["refundable_display"]

    def test_junk_rooms_ignored(self) -> None:
        from mcp_tools.search_hotels import _summarise_refundable

        o: dict[str, Any] = {"price_inr": 100.0}
        _summarise_refundable(o, ["junk", None, 42])  # type: ignore[list-item]
        assert o["refundable_room_count"] == 0

    def test_prompt_forbids_the_blanket_claim(self) -> None:
        from pathlib import Path

        text = (
            Path(__file__).resolve().parent.parent / "prompts" / "system_prompt_v2.md"
        ).read_text(encoding="utf-8")
        assert "refundable_display" in text
        assert "not the whole hotel" in text.lower()
