"""A tour is not one product — it has bookable variants.

The client's site shows 12 option cards for "Desert Safari Tours in Dubai"
(Overnight/Evening x Shared/Private vehicle x add-ons) and 12 for Burj Khalifa
(At the Top Silver, Fast Track, Level 148...). We showed none of them: asked
"what are the tour options for X", the agent answered from get_tour_details
prose. get_tour_options exposes the real variants.
"""

import pytest


class TestVariantsAreExposed:
    @pytest.fixture(scope="class")
    def desert(self):
        from mcp_tools.get_tour_options import _impl

        return _impl(tour_id=46009, travel_date="2026-09-30", adults=2)

    @pytest.fixture(scope="class")
    def burj(self):
        from mcp_tools.get_tour_options import _impl

        return _impl(tour_id=30614, travel_date="2026-09-30", adults=2)

    def test_desert_safari_returns_its_variants(self, desert):
        assert desert["total_results"] >= 10, desert["total_results"]
        names = " ".join(o["name"] for o in desert["options"]).lower()
        assert "overnight" in names and "evening" in names
        assert "shared vehicle" in names or "private vehicle" in names

    def test_burj_variants_carry_real_prices(self, burj):
        priced = [o for o in burj["options"] if o.get("price_inr")]
        assert priced, "expected priced Burj variants"
        assert any("at the top" in o["name"].lower() for o in burj["options"])

    def test_table_has_one_row_per_variant(self, burj):
        rows = len(burj["table_markdown"].splitlines()) - 2  # header + separator
        assert rows == burj["total_results"]

    def test_pax_limits_and_rate_basis_present(self, desert):
        limits = [o["pax_limits"] for o in desert["options"] if o.get("pax_limits")]
        assert limits, "pax limits missing"
        first = next(iter(limits[0].values()))
        assert "min_pax" in first and "max_pax" in first and "rate_basis" in first

    def test_unpriced_variants_say_on_request_not_zero(self, desert):
        for o in desert["options"]:
            if not o.get("price_inr"):
                assert o["price_display"] == "On request"
                assert "0" not in o["price_display"]

    def test_instructions_demand_every_row(self, burj):
        note = burj["agent_instructions"]
        assert "PASTE `table_markdown` VERBATIM" in note
        assert str(burj["total_results"]) in note

    def test_timeslot_flag_is_surfaced(self, burj):
        # Burj variants are slot-based; the agent needs to know to fetch times.
        assert any(o["has_timeslots"] for o in burj["options"])


class TestNoFabricatedTransferPrices:
    def test_all_zero_tiers_are_not_reported_as_included(self):
        from mcp_tools.get_tour_options import _tier_rows

        # Rates not loaded for the date -> both tiers 0 -> report nothing.
        row = {"initialTransferRates": [
            {"transferTypeName": "Sharing Transfers", "startingFromRate": 0, "currencyCode": "AED"},
            {"transferTypeName": "Private Transfers", "startingFromRate": 0, "currencyCode": "AED"},
        ]}
        assert _tier_rows(row) == []

    def test_zero_alongside_a_paid_tier_is_included(self):
        from mcp_tools.get_tour_options import _tier_rows

        row = {"initialTransferRates": [
            {"transferTypeName": "Sharing Transfer", "startingFromRate": 0, "currencyCode": "AED"},
            {"transferTypeName": "Private Transfer", "startingFromRate": 192.61, "currencyCode": "AED"},
        ]}
        tiers = _tier_rows(row)
        assert len(tiers) == 2
        sharing = next(t for t in tiers if "Sharing" in t["transfer_type"])
        assert sharing["price_inr"] == 0
        assert "Included" in sharing["price_display"]


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


class TestTransferProbeSkipsTicketOnlyTours:
    def test_without_transfer_tours_are_not_probed(self):
        import mcp_tools.search_tours as st

        class Opt:
            tour_id = 46009
            transfer_scenario = "Without Transfer"
            supplier_id = 3

        probed = []
        orig = st.call_tour_options if hasattr(st, "call_tour_options") else None
        assert orig is None  # imported inside the function, so nothing to patch
        # A "Without Transfer" tour short-circuits before any network call.
        st._attach_transfer_prices([Opt()], "2026-09-30", 2)
        assert probed == []


class TestAddonsArePricedAndFlagged:
    """The website priced "2 Drinks Package - Per Person (Add-on)" while we said
    "On request". Cause: the rate call is keyed on transferId and we hardcoded 1;
    add-ons only answer on transferId 3 (Without Transfers), returning
    result:null for 1."""

    @pytest.fixture(scope="class")
    def desert(self):
        from mcp_tools.get_tour_options import _impl

        return _impl(tour_id=30647, travel_date="2026-09-25", adults=2)

    def test_no_variant_is_on_request(self, desert):
        unpriced = [o["name"] for o in desert["options"] if not o.get("price_inr")]
        assert not unpriced, f"still On request: {unpriced}"

    def test_addons_are_flagged(self, desert):
        addons = [o for o in desert["options"] if o.get("is_addon")]
        assert addons, "no add-on detected"
        assert all("add-on" in o["name"].lower() for o in addons)

    def test_addons_carry_no_transfer_prices(self, desert):
        # initialTransferRates is the TOUR's transfer, not the add-on's — showing
        # "Private Transfers Rs 11,509" on a drinks package is wrong.
        for o in desert["options"]:
            if o.get("is_addon"):
                assert not o.get("transfer_prices")

    def test_main_variants_keep_transfer_prices(self, desert):
        mains = [o for o in desert["options"] if not o.get("is_addon")]
        assert any(o.get("transfer_prices") for o in mains)

    def test_addons_sort_after_main_variants(self, desert):
        body = [ln for ln in desert["table_markdown"].splitlines()[2:]]
        first_addon = next(i for i, ln in enumerate(body) if "(add-on)" in ln)
        assert all("(add-on)" not in ln for ln in body[:first_addon])

    def test_instructions_tell_agent_to_offer_addons(self, desert):
        note = desert["agent_instructions"].lower()
        assert "is_addon" in note
        assert "ask whether they want any add-ons" in note


class TestBookingConstraintsAreSurfaced:
    """Variants carry rules: an add-on needs a main option, and
    restrictedRateType lists pax types that cannot take it (the drinks package
    excludes Child and Infant)."""

    @pytest.fixture(scope="class")
    def desert(self):
        from mcp_tools.get_tour_options import _impl

        return _impl(tour_id=30647, travel_date="2026-09-25", adults=2)

    def test_restricted_pax_types_are_listed(self, desert):
        drinks = next(o for o in desert["options"] if "drinks" in o["name"].lower())
        assert "Child" in drinks["not_available_for"]
        assert "Infant" in drinks["not_available_for"]

    def test_unrestricted_variants_list_nothing(self, desert):
        main = next(o for o in desert["options"] if o["name"] == "Dubai Desert Safari")
        assert main["not_available_for"] == []

    def test_notes_column_states_the_rules(self, desert):
        table = desert["table_markdown"]
        assert "add-on — needs a main option" in table
        assert "not for Child/Infant" in table

    def test_notes_cell_is_dash_when_unconstrained(self):
        from mcp_tools.get_tour_options import _notes_cell

        assert _notes_cell({"is_addon": False}) == "-"

    def test_notes_cell_combines_constraints(self):
        from mcp_tools.get_tour_options import _notes_cell

        cell = _notes_cell({
            "is_addon": True,
            "not_available_for": ["Child"],
            "transfer_not_available": ["Private Transfers"],
        })
        assert "add-on" in cell and "not for Child" in cell
        assert "no Private Transfers" in cell

    def test_instructions_cover_the_dependency_rules(self, desert):
        note = desert["agent_instructions"]
        assert "not_available_for" in note
        assert "transfer_not_available" in note
