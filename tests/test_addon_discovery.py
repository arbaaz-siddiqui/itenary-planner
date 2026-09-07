"""Asked for a tour's add-ons, the agent kept replying "which tour do you mean?"

Two root causes, both ambiguity the customer had already resolved:

1. `search_tours` returned 15 rows with nothing marking which one a follow-up
   ("does it have any add ons") was about, so the model asked instead of
   calling get_tour_options. The variant list it needed was already being
   fetched and discarded by the transfer-price pass.
2. `lookup_entity` ranked on the supplier's own order, so "burj khalifa" put
   "Sky Views Edge Walk" at results[0] while every usage hint says to use
   results[0]. Faced with three near-identical candidates the model stalled and
   returned an EMPTY reply.
"""

from mcp_tools.lookup_entity import _impl as _lookup
from mcp_tools.search_tours import _impl as _tours

DATE = "2026-09-20"


class TestSearchRowsAdvertiseTheirAddons:
    def _desert(self):
        return _tours(destination_city="Dubai", travel_date=DATE,
                      query="desert safari", adults=2, max_results=15)

    def test_the_addon_bearing_tour_names_its_addons(self):
        rows = [o for o in self._desert()["options"] if o.get("addon_names")]
        assert rows, "no row advertised add-ons"
        names = " ".join(n.lower() for r in rows for n in r["addon_names"])
        assert "drinks package" in names

    def test_addons_display_is_ready_to_relay(self):
        row = next(o for o in self._desert()["options"] if o.get("addon_names"))
        assert row["addons_display"].startswith(f"{len(row['addon_names'])} add-on")

    def test_ticket_only_tours_advertise_none(self):
        # A "Without Transfer" tour is never probed, so it has no variant data;
        # it must report no add-ons rather than a stale or invented list.
        for o in self._desert()["options"]:
            if "without" in str(o.get("transfer_scenario", "")).lower():
                assert not o.get("addon_names")

    def test_extras_column_appears_only_when_some_row_has_addons(self):
        table = self._desert()["table_markdown"]
        assert "| Extras |" in table
        header, _sep, *body = table.splitlines()
        assert all(len(r.split("|")) == len(header.split("|")) for r in body)

    def test_instructions_forbid_asking_which_tour(self):
        note = self._desert()["agent_instructions"]
        assert "do NOT ask them which tour" in note


class TestLookupRanksTheRealMatchFirst:
    def test_burj_khalifa_leads_with_a_burj_tour(self):
        top = _lookup(service="tours", query="burj khalifa", city="Dubai")["results"][0]
        assert "burj khalifa" in top["name"].lower(), top["name"]

    def test_city_still_outranks_name_relevance(self):
        # Name sorting must not undo the city scoping applied just before it.
        res = _lookup(service="tours", query="dhow cruise", city="Dubai")["results"]
        in_city = [r for r in res if "dubai" in str(r.get("city", "")).lower()]
        if in_city and len(in_city) < len(res):
            assert "dubai" in str(res[0].get("city", "")).lower()

    def test_best_match_is_stated_so_no_choice_is_needed(self):
        # Five rows plus "pass results[0]" is not a decision: with three
        # near-identical Burj rows the model deliberated and emitted an EMPTY
        # message to the customer (5 empty turns in 5 runs; 0 when the same
        # call returned one row).
        res = _lookup(service="tours", query="burj khalifa", city="Dubai")
        assert res["best_match_id"] == res["results"][0]["id"]
        assert res["best_match_name"] == res["results"][0]["name"]
        assert "best_match_id" in res["usage_hint"]

    def test_best_match_points_at_a_BOOKABLE_tour(self):
        # Ranking on name alone promoted "Burj Khalifa Tickets" (28482), which
        # has ZERO bookable variants, over "At The Top, Burj Khalifa" (28488,
        # 14 priced). This response cannot distinguish them
        # (totalAvailableServices reads 2 for both), so supplier order wins.
        from mcp_tools.get_tour_options import _impl as _options

        best = _lookup(service="tours", query="burj khalifa", city="Dubai")["best_match_id"]
        assert _options(tour_id=best, travel_date=DATE, adults=2)["total_results"] > 0

    def test_the_dead_duplicate_field_is_gone(self):
        # all_results was a byte-identical copy of results that nothing read.
        assert "all_results" not in _lookup(service="tours", query="burj khalifa",
                                            city="Dubai")
