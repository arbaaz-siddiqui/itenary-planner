"""Client site showed 13 desert safaris; our chat showed 5.

Two separate causes:
  1. A named search ("desert safari") was capped at the generic page size of
     10, so 3 of 13 never left the tool.
  2. The model then rendered 5 of the 10 it did receive. Telling it the exact
     count in prose did not work — it still sent 5 — so the table is now built
     in code and handed over ready to paste.
"""


class TestNamedSearchReturnsEverything:
    def test_desert_safari_returns_all_matches(self):
        from mcp_tools.search_tours import _impl

        r = _impl(destination_city="Dubai", travel_date="2026-11-01",
                  adults=2, query="desert safari")
        # The supplier lists 13 for this date; the site shows 13.
        assert len(r["options"]) >= 13, (
            f"named search returned {len(r['options'])}, expected all matches"
        )

    def test_generic_browse_still_pages(self):
        from mcp_tools.search_tours import _impl

        r = _impl(destination_city="Dubai", travel_date="2026-11-01", adults=2)
        # Unnamed browse must NOT dump 270 tours into context.
        assert len(r["options"]) == 10
        assert r.get("next_offset") == 10


class TestTableIsBuiltInCode:
    def _result(self):
        from mcp_tools.search_tours import _impl

        return _impl(destination_city="Dubai", travel_date="2026-11-01",
                     adults=2, query="desert safari")

    def test_table_has_one_row_per_option(self):
        r = self._result()
        rows = len(r["table_markdown"].splitlines()) - 2  # header + separator
        assert rows == len(r["options"]) == r["rows_to_render"]

    def test_rows_to_render_is_stated_as_a_number(self):
        r = self._result()
        assert isinstance(r["rows_to_render"], int)
        assert r["rows_to_render"] == len(r["options"])

    def test_instructions_say_to_paste_the_table(self):
        r = self._result()
        note = r["agent_instructions"].lower()
        assert "paste it verbatim" in note
        assert str(r["rows_to_render"]) in r["agent_instructions"]

    def test_pipes_in_names_do_not_break_the_table(self):
        from mcp_tools.search_tours import _tours_table

        class O:
            name = "Safari | With Pipe"
            price_display = "\u20b91,000"
            category = "Tours"
            duration = "2 Hours"
            sharing_display = "Shared"
            cancellation_display = "Free"
            slots_display = "Flexible"
            is_recommended = False

        table = _tours_table([O()])
        body = table.splitlines()[2]
        # 7 columns => 8 pipes; a literal pipe in the name would add a 9th.
        assert body.count("|") == 8, body
