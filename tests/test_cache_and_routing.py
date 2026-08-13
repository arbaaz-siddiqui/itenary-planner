"""Regressions for the two defects behind the Howard Johnson failure.

A customer asked about a hotel that IS in inventory and was told:
"I couldn't find a Howard Johnson in Dubai. Most of the ones I see are in the
US or China."

Two independent bugs produced that one sentence:

1. The model called `lookup_entity` (worldwide, no city filter) instead of
   `search_hotels`. Prompt/docstring warnings did not hold.
2. The answer came from a cache entry created BEFORE the city-scoping fix
   shipped — keys had no code version, so the pre-fix result replayed for the
   full 1-hour TTL. This also made the bug unreproducible in a fresh process.
"""

from __future__ import annotations

import mcp_tools.result_cache as rc
from mcp_tools.lookup_entity import _impl as lookup_entity


class TestCacheVersioning:
    def test_epoch_is_part_of_the_key(self) -> None:
        """Bumping the epoch must orphan every existing entry."""
        params = {"service": "hotels", "query": "Howard Johnson"}
        before = rc._key("lookup_entity", params)
        original = rc.CACHE_EPOCH
        try:
            rc.CACHE_EPOCH = original + 1
            after = rc._key("lookup_entity", params)
        finally:
            rc.CACHE_EPOCH = original
        assert before != after, "epoch change must produce a different key"

    def test_same_epoch_and_args_is_stable(self) -> None:
        p = {"service": "tours", "query": "safari"}
        assert rc._key("lookup_entity", p) == rc._key("lookup_entity", p)

    def test_different_args_differ(self) -> None:
        a = rc._key("search_hotels", {"destination_city": "Dubai"})
        b = rc._key("search_hotels", {"destination_city": "Abu Dhabi"})
        assert a != b

    def test_stale_entry_from_old_epoch_is_not_served(self) -> None:
        """The exact replay that reached the customer."""
        rc.clear_result_cache()
        params = {"service": "hotels", "query": "Howard Johnson"}
        stale = {"results": [{"name": "Howard Johnson by Wyndham Bakersfield"}],
                 "total_results": 1}
        # Seed under the CURRENT epoch, then simulate a code change.
        rc.cached_or_call("lookup_entity", params, lambda: stale)
        original = rc.CACHE_EPOCH
        try:
            rc.CACHE_EPOCH = original + 1
            fresh = {"results": [{"name": "Howard Johnson by Wyndham Bur Dubai"}],
                     "total_results": 1}
            out = rc.cached_or_call("lookup_entity", params, lambda: fresh)
        finally:
            rc.CACHE_EPOCH = original
            rc.clear_result_cache()
        assert "Bur Dubai" in str(out), "post-change call must not replay the old result"
        assert out.get("from_cache") is False

    def test_cache_is_bounded(self) -> None:
        """Entries were never evicted; full payloads accumulated forever."""
        rc.clear_result_cache()
        try:
            for i in range(rc._MAX_ENTRIES + 40):
                rc.cached_or_call(
                    "search_hotels", {"n": i}, lambda i=i: {"total_results": 1, "x": i}
                )
            assert len(rc._CACHE) <= rc._MAX_ENTRIES
        finally:
            rc.clear_result_cache()


class TestHotelLookupIsStructurallyBlocked:
    """Prose could not stop this; the tool now refuses the call outright."""

    def test_hotels_service_is_rejected(self) -> None:
        out = lookup_entity(service="hotels", query="Howard Johnson Hotel")
        assert out["error"] is True
        assert out["error_type"] == "WrongTool"
        assert out["use_instead"] == "search_hotels"

    def test_rejection_tells_the_model_what_to_call(self) -> None:
        out = lookup_entity(service="hotels", query="Howard Johnson", city="Dubai")
        retry = out["retry_with"]
        assert retry["tool"] == "search_hotels"
        assert retry["hotel_name"] == "Howard Johnson"
        assert retry["destination_city"] == "Dubai"

    def test_rejection_never_implies_the_hotel_is_missing(self) -> None:
        """The failure mode was a 'doesn't exist' reply — the message must not
        give the model any basis for one."""
        msg = lookup_entity(service="hotels", query="Howard Johnson")["message"].lower()
        for phrase in ("not found", "no results", "does not exist", "doesn't exist"):
            assert phrase not in msg

    def test_hotels_no_longer_a_valid_service(self) -> None:
        import mcp_tools.lookup_entity as le

        assert "hotels" not in le._VALID_SERVICES
        assert {"tours", "restaurants", "airlines"} <= le._VALID_SERVICES

    def test_docstring_does_not_demo_a_hotel_lookup(self) -> None:
        """The old docstring's first example was lookup_entity(service="hotels",
        query="Atlantis Dubai") with no city — the model copied it verbatim."""
        doc = lookup_entity.__doc__ or ""
        assert 'service="hotels"' not in doc


class TestPerSurfaceTools:
    """A tool a surface cannot use should not be on its menu.

    Voice was handed display_options_tool despite system_prompt_voice.md saying
    "Never call display_options_tool (no screen)" — a ban enforced in prose
    against a structurally present tool.
    """

    def test_voice_drops_screen_and_file_tools(self) -> None:
        from agent_tools import tools_for_surface

        names = {t.name for t in tools_for_surface("voice")}
        assert "display_options_tool" not in names
        assert "build_trip_schedule_tool" not in names
        assert "generate_itinerary_pdf_tool" not in names, "a call cannot deliver a file"

    def test_whatsapp_keeps_pdf_but_drops_cards(self) -> None:
        from agent_tools import tools_for_surface

        names = {t.name for t in tools_for_surface("whatsapp")}
        assert "generate_itinerary_pdf_tool" in names, "PDFs ship as attachments"
        assert "display_options_tool" not in names

    def test_streamlit_gets_everything(self) -> None:
        from agent_tools import ALL_TOOLS, tools_for_surface

        assert len(tools_for_surface("streamlit")) == len(ALL_TOOLS)

    def test_every_surface_keeps_the_search_tools(self) -> None:
        from agent_tools import tools_for_surface

        core = {"search_flights", "search_hotels", "search_tours",
                "search_airport_transfer_dubai", "get_visa_info"}
        for surface in ("streamlit", "whatsapp", "voice", "unknown"):
            names = {t.name for t in tools_for_surface(surface)}
            assert core <= names, f"{surface} lost a core search tool"

    def test_unknown_surface_is_not_crippled(self) -> None:
        from agent_tools import ALL_TOOLS, tools_for_surface

        assert len(tools_for_surface("")) == len(ALL_TOOLS)
