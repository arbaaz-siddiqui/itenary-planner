"""Asked for a hotel with a BAR, the chat returned 4 hotels and none showed one.

Three bugs: the Bar keywords missed the supplier's usual plural phrasing
("2 bars/lounges"); matching was plain substring, so "bar" hit "minibars" (an
in-room fridge); and the amenity filter only SORTED matches to the top instead
of filtering, so non-matching hotels were still listed.
"""

from mcp_tools.search_hotels import _extract_amenities, _match_amenities


class TestBarIsNotAMinibar:
    def test_minibar_is_not_a_bar(self):
        assert _match_amenities(["bar"], "guestrooms featuring minibars") == []

    def test_barber_and_barbecue_are_not_bars(self):
        assert _match_amenities(["bar"], "a barber shop on site") == []
        assert _match_amenities(["bar"], "barbecue grills available") == []

    def test_plural_bars_lounges_matches(self):
        # The supplier's usual phrasing, which the old keys missed entirely.
        assert _match_amenities(["bar"], "at one of the 2 bars/lounges") == ["bar"]

    def test_singular_and_rooftop_bars_match(self):
        assert _match_amenities(["bar"], "a bar/lounge on site") == ["bar"]
        assert _match_amenities(["bar"], "relax at the rooftop bar") == ["bar"]

    def test_no_bar_mentioned_does_not_match(self):
        assert _match_amenities(["bar"], "no drinks here at all") == []


class TestPrefixKeywordsStillWork:
    def test_fitness_center_matches_gym(self):
        # "fitness cent" is a prefix key — a strict word boundary would break it.
        assert _match_amenities(["gym"], "a 24-hour fitness center") == ["gym"]

    def test_multiple_amenities_all_matched(self):
        got = _match_amenities(["pool", "gym"], "outdoor pool and a fitness center")
        assert sorted(got) == ["gym", "pool"]


class TestAmenityDisplayIncludesBar:
    def test_display_list_shows_bar_for_plural_phrasing(self):
        # The customer must be able to SEE why a hotel matched their filter.
        assert "Bar" in _extract_amenities("drink at one of the 2 bars/lounges")

    def test_display_list_has_no_bar_for_minibar(self):
        assert "Bar" not in _extract_amenities("guestrooms with minibars")
