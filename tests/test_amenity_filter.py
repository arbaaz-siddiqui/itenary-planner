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


class TestEveryRoomTypeIsShown:
    """The website lists 3 room cards for Admiral Plaza — Superior Double,
    Superior Twin and Family Room — but the agent showed only Superior types.

    The hotel returns 14 RATES across 3 room types (same room at different
    board/cancellation terms). room_policies was capped at the 5 cheapest
    rates, which were all duplicates of the two Superiors, so the Family Room
    never reached the customer.
    """

    def _admiral(self):
        from mcp_tools.search_hotels import _impl

        return _impl(destination_city="Dubai", check_in="2026-10-10",
                     check_out="2026-10-15", hotel_name="Admiral Plaza",
                     adults=2, force_refresh=True)

    def test_family_room_is_listed(self):
        o = (self._admiral().get("options") or [{}])[0]
        rooms = " ".join(p["room"].lower() for p in o.get("room_policies") or [])
        assert "family room" in rooms, rooms

    def test_no_duplicate_room_types(self):
        o = (self._admiral().get("options") or [{}])[0]
        names = [p["room"].strip().lower() for p in o.get("room_policies") or []]
        assert len(names) == len(set(names)), f"duplicates: {names}"

    def test_distinct_type_count_is_reported(self):
        o = (self._admiral().get("options") or [{}])[0]
        # 14 rates collapse to 3 real choices; both numbers are surfaced.
        assert o.get("room_options_count", 0) > o.get("room_types_total", 0)
        assert o.get("room_types_total") == len(o.get("room_policies") or [])

    def test_cheapest_rate_wins_per_type(self):
        # _room_policy_breakdown sorts refundable-first then cheapest, so the
        # kept row for a type must be its cheapest occurrence.
        from mcp_tools.search_hotels import _room_policy_breakdown

        rooms = [
            {"RoomTypeName": "Family Room", "TotalRate": 900, "RoomRates": []},
            {"RoomTypeName": "Family Room", "TotalRate": 500, "RoomRates": []},
        ]
        out = _room_policy_breakdown(rooms, 1)
        prices = [p["price_inr"] for p in out if "family" in p["room"].lower()]
        assert prices == sorted(prices), prices
