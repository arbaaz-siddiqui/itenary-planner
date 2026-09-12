"""get_tour_options — variants of one tour, priced for a stated party size."""

import pytest

from mcp_tools.tour_pricing import tour_options, transfer_prices


class TestVariantsAreExposed:
    @pytest.fixture(scope="class")
    def burj(self):
        return tour_options(30614, "2026-09-30", 2)

    def test_burj_variants_carry_real_prices(self, burj):
        assert [o for o in burj["options"] if o.get("price_total_inr")]

    def test_unpriced_variants_say_on_request_not_zero(self, burj):
        for o in burj["options"]:
            if not o.get("price_total_inr"):
                assert o["price_display"] == "On request"

    def test_prices_are_totals_for_the_party(self, burj):
        assert burj["adults"] == 2
        for o in burj["options"]:
            if o.get("price_total_inr"):
                assert o["price_per_adult_inr"] == pytest.approx(
                    o["price_total_inr"] / 2, abs=1
                )


class TestTransferTiers:
    """Tier prices come from `initialTransferRates`.

    `totalTransferRate` is the per-person ticket plus that tier's transfer, so
    subtracting the Without-Transfer entry leaves the transfer itself. A tier
    that costs nothing is not listed: "Included (Rs 0)" reads as a free
    transfer and gives the customer nothing to act on.
    """

    def _row(self, *, sharing_total, private_total, base=100.0):
        return {"initialTransferRates": [
            {"transferTypeId": 3, "transferTypeName": "Without Transfer",
             "startingFromRate": 0, "totalTransferRate": base,
             "currencyCode": "AED"},
            {"transferTypeId": 1, "transferTypeName": "Sharing Transfer",
             "startingFromRate": 0, "totalTransferRate": sharing_total,
             "currencyCode": "AED"},
            {"transferTypeId": 2, "transferTypeName": "Private Transfer",
             "startingFromRate": private_total - base,
             "totalTransferRate": private_total, "currencyCode": "AED"},
        ]}

    def test_a_tier_that_costs_nothing_is_not_listed(self):
        row = self._row(sharing_total=100.0, private_total=100.0)
        assert transfer_prices(row, 1) == []

    def test_a_paid_tier_survives_when_another_is_free(self):
        row = self._row(sharing_total=100.0, private_total=292.61)
        tiers = transfer_prices(row, 1)
        assert [t["transfer_type"] for t in tiers] == ["Private Transfer"]
        assert tiers[0]["price_inr"] > 0

    def test_sharing_scales_with_the_party_but_private_does_not(self):
        # Sharing is per person; private is per vehicle, and startingFromRate
        # already holds the whole-vehicle figure.
        row = self._row(sharing_total=125.0, private_total=300.0)
        one = {t["transfer_type"]: t["price_inr"] for t in transfer_prices(row, 1)}
        four = {t["transfer_type"]: t["price_inr"] for t in transfer_prices(row, 4)}
        assert four["Sharing Transfer"] == pytest.approx(
            one["Sharing Transfer"] * 4, abs=2
        )
        assert four["Private Transfer"] == pytest.approx(one["Private Transfer"])


class TestRoeCaching:
    def test_ttl_is_five_minutes(self):
        from fx import ROE_CACHE_TTL_SECS

        assert ROE_CACHE_TTL_SECS == 300.0

    def test_concurrent_callers_make_one_fetch(self, monkeypatch):
        import fx

        calls = []

        def fake(code):
            calls.append(code)
            return (0.0387, 26.33)

        monkeypatch.setattr(fx, "_fetch_roe_pair", fake)
        fx._ROE_PAIR_CACHE.clear()

        import concurrent.futures as cf

        with cf.ThreadPoolExecutor(max_workers=12) as ex:
            list(ex.map(lambda _: fx._roe_pair("INR"), range(12)))
        assert len(calls) == 1, f"12 callers made {len(calls)} ROE fetches"


class TestAddonsAndGroupPricing:
    @pytest.fixture(scope="class")
    def desert(self):
        return tour_options(30647, "2026-09-15", 4)

    def test_addons_are_flagged(self, desert):
        addons = [o for o in desert["options"] if o.get("is_addon")]
        assert addons, "tour 30647 sells add-ons"
        for a in addons:
            assert "add-on" in a["name"].lower()

    def test_main_variants_keep_transfer_prices(self, desert):
        main = [o for o in desert["options"] if not o.get("is_addon")]
        assert any(o.get("transfer_prices") for o in main)

    def test_a_per_group_item_is_not_labelled_per_adult(self, desert):
        majlis = next(
            (o for o in desert["options"] if "per group" in o["name"].lower()), None
        )
        assert majlis, "expected the Private Majlis add-on"
        assert majlis["price_basis"] == "per_group"
        assert "per adult" not in majlis["price_display"].lower()
