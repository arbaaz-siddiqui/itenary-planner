"""Client: "your chat says same tour price, our site shows Sharing Rs 1,790 and
Private Rs 11,548."

Cause: toursearchlistrate returns ONE flat rate per tour with no transfer
split, so sharing_display could only name the modes. The real split lives in
the B2C tour-option APIs (#23/#25 in the client's Postman doc), which are
documented on stagingb2c.gujjutours.com -- a host that 404s on every /api path.
They answer on www.gujjutours.com.

Verified against the client's own screenshot (tour 30647, 24 Sep, 4 adults):
Sharing 68.2 AED -> Rs 1,789, Private 440 AED -> Rs 11,542 (site: 1,790.03 /
11,548.61; the difference is live ROE drift).
"""

import pytest


class TestTransferPricesReachTheCustomer:
    @pytest.fixture(scope="class")
    def result(self):
        from mcp_tools.search_tours import _impl

        return _impl(destination_city="Dubai", travel_date="2026-09-24",
                     adults=4, query="desert safari", force_refresh=True)

    def test_a_tour_with_transfers_shows_two_real_prices(self, result):
        withprices = [o for o in result["options"] if o.get("transfer_prices")]
        assert withprices, "no tour carried per-transfer-type prices"
        tiers = withprices[0]["transfer_prices"]
        assert len(tiers) >= 2, tiers
        names = {t["transfer_type"] for t in tiers}
        assert any("Sharing" in n for n in names), names
        assert any("Private" in n for n in names), names

    def test_prices_differ_between_sharing_and_private(self, result):
        # The whole complaint: they are NOT the same price.
        for o in result["options"]:
            tiers = o.get("transfer_prices") or []
            if len(tiers) >= 2:
                vals = [t["price_inr"] for t in tiers]
                assert len(set(vals)) > 1, f"{o['name']} priced all tiers equal: {vals}"
                return
        pytest.skip("no multi-tier tour in this page")

    def test_no_same_tour_price_claim_when_we_have_real_prices(self, result):
        for o in result["options"]:
            if o.get("transfer_prices"):
                assert "same tour price" not in (o.get("sharing_display") or "")

    def test_ticket_only_tours_are_left_alone(self, result):
        # A tour with no pickup legitimately has no transfer prices.
        for o in result["options"]:
            if "Ticket only" in (o.get("sharing_display") or ""):
                assert not o.get("transfer_prices")


class TestPriceFreshness:
    def test_volatile_ttl_is_180_seconds(self):
        from mcp_tools.result_cache import _VOLATILE_TTL

        assert _VOLATILE_TTL == 180.0

    def test_price_searches_use_the_volatile_tier(self):
        from mcp_tools.result_cache import _TOOL_TTL, _VOLATILE_TTL

        for tool in ("search_tours", "search_hotels", "search_flights"):
            assert _TOOL_TTL[tool] == _VOLATILE_TTL, tool

    def test_a_cached_price_result_tells_the_agent_to_offer_a_refresh(self):
        from mcp_tools.search_tours import search_tours_tool

        args = {"destination_city": "Dubai", "travel_date": "2026-09-24",
                "adults": 2, "query": "desert safari"}
        search_tours_tool.invoke(args)
        second = search_tours_tool.invoke(args)
        assert second.get("from_cache") is True
        note = second.get("freshness_note") or ""
        assert "force_refresh=True" in note
        assert "can move" in note


class TestZeroRateTiersAreIncludedNotDropped:
    """Burj Khalifa (tour 30614) publishes Without Rs 0 / Sharing Rs 0 /
    Private 192.61 AED. Dropping the Rs 0 tiers left the model with only
    'Private' and it invented 'sharing depends on your hotel distance'."""

    def test_burj_shows_all_three_tiers(self):
        from mcp_tools.search_tours import _impl

        r = _impl(destination_city="Dubai", travel_date="2026-09-30",
                  adults=1, query="burj khalifa tickets", force_refresh=True)
        burj = [o for o in r["options"] if o["tour_id"] == 30614]
        assert burj, "tour 30614 not in results"
        tiers = burj[0].get("transfer_prices") or []
        names = {t["transfer_type"] for t in tiers}
        assert any("Sharing" in n for n in names), f"Sharing tier dropped: {names}"
        assert any("Private" in n for n in names), names
        sharing = next(t for t in tiers if "Sharing" in t["transfer_type"])
        assert sharing["price_inr"] == 0
        assert "Included" in sharing["price_display"] or "\u20b90" in sharing["price_display"]
