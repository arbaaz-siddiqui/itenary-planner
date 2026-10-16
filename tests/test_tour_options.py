"""get_tour_options — variants of one tour, priced for a stated party size."""

import pytest

from mcp_tools.tour_pricing import tour_options
from tests.factories import future_date


class TestVariantsAreExposed:
    @pytest.fixture(scope="class")
    def burj(self):
        return tour_options(30614, future_date(14), 2)

    def test_burj_variants_carry_real_prices(self, burj):
        assert [o for o in burj["options"] if o.get("price_total_inr")]

    def test_unpriced_variants_say_on_request_not_zero(self, burj):
        for o in burj["options"]:
            if not o.get("price_total_inr"):
                assert o["price_display"] == "On request"

    def test_prices_are_totals_for_the_party(self, burj):
        assert burj["adults"] == 2
        for o in burj["options"]:
            for p in o.get("prices") or []:
                assert p["priced_for_pax"] in (1, 2)
                assert p["total_display"].endswith(tuple("0123456789"))


class TestPartyTotals:
    """Totals per transfer type are the supplier's own grand total.

    Rebuilding them from a per-head ticket plus a per-vehicle transfer is how
    the agent quoted Rs 16,689 for a booking gujjutours.com prices at
    Rs 13,290.34, and how children were left out entirely.
    """

    OPTION = {
        "optionId": 222565,
        "supplierId": 1,
        "tourRestrictedTransferType": [{"transferTypeId": 3}],
        "validateTourOption": [
            {"transferTypeId": 1, "transferTypeName": "Sharing Transfer"},
            {"transferTypeId": 3, "transferTypeName": "Without Transfer"},
            {"transferTypeId": 2, "transferTypeName": "Private Transfer"},
        ],
    }

    def _fake_rate(self, calls, rates):
        def fake(**kw):
            calls.append(kw)
            return {"result": [{
                "rate": rates[(kw["transfer_id"], kw.get("vehicle_id", 0))],
                "currencyCode": "AED",
                "vehicleRates": [{"vehicleId": 4, "minPax": 1, "maxPax": 6}],
            }]}
        return fake

    def test_only_the_transfer_types_the_option_is_sold_with(self):
        from mcp_tools.tour_pricing import offered_transfers

        assert offered_transfers(self.OPTION) == [
            (1, "Sharing Transfer"), (2, "Private Transfer")
        ]

    def test_the_supplier_rate_is_quoted_unchanged_with_children(self, monkeypatch):
        import mcp_tools.tour_pricing as tp

        calls: list = []
        monkeypatch.setattr(tp, "call_tour_option_rate", self._fake_rate(
            calls, {(1, 0): 409.2, (2, 0): 136.4, (2, 4): 576.4}))
        monkeypatch.setattr(tp, "convert_supplier_price",
                            lambda aed, **_: (round(aed * 26.1518, 2), "INR"))

        got = tp.party_prices(53632, self.OPTION, "2026-10-22", 2, (5, 8))

        assert {p["transfer_type"]: p["total_inr"] for p in got} == {
            "Sharing Transfer": round(409.2 * 26.1518, 2),
            "Private Transfer": round(576.4 * 26.1518, 2),
        }
        assert all(c["child_ages"] == (5, 8) for c in calls)
        # Private re-priced with the vehicle the supplier named.
        assert any(c["transfer_id"] == 2 and c["vehicle_id"] == 4 for c in calls)

    def test_a_per_unit_option_is_priced_for_one_unit(self, monkeypatch):
        import mcp_tools.tour_pricing as tp

        quad = {
            "optionId": 222612,
            "supplierId": 1,
            "restrictedRateType": [{"name": "Adult"}, {"name": "Child"}],
            "tourRestrictedTransferType": [
                {"transferTypeId": 1}, {"transferTypeId": 2}],
            "validateTourOption": [
                {"transferTypeId": 3, "rateTypeName": "Unit",
                 "transferTypeName": "Without Transfer"}],
        }
        calls: list = []
        monkeypatch.setattr(tp, "call_tour_option_rate",
                            self._fake_rate(calls, {(3, 0): 60.5}))
        assert tp.sold_per_unit(quad)
        got = tp.party_prices(53632, quad, "2026-10-22", 4, (6,))
        assert got[0]["priced_per"] == "unit"
        assert calls[0]["units"] == 1 and calls[0]["child_ages"] == ()

    def test_children_reach_the_supplier_payload(self):
        from booking_api.endpoints import _tour_pax_details

        assert _tour_pax_details(2, (5, 8)) == [
            {"label": "adult", "value": "2", "age": ""},
            {"label": "child", "value": "2", "age": "5,8"},
        ]
        assert _tour_pax_details(2, ()) == [
            {"label": "adult", "value": "2", "age": ""}]

    def test_the_party_is_named(self):
        from mcp_tools.tour_pricing import party_label

        assert party_label(2, (5, 8)) == "2 adults + 2 children (ages 5, 8)"
        assert party_label(1) == "1 adult"


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
        return tour_options(30647, future_date(14), 4)

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
