"""Restaurants were showing a bare rating and no service hours.

The supplier returns both, and the client named the exact fields:
  "review": {"rating": "4.4", "reviewCount": "<p>Very Good</p>"}
  "restaurantMealTiming": [{"mealType": "Breakfast", "openingTiming": [...]}]

`reviewCount` is not a count — it is an HTML verdict. `restaurantMealTiming`
appears only in the details response, not the list.
"""

from parsers import parse_restaurant_response


def _raw(**over):
    row = {
        "restaurantId": 3,
        "restaurantName": "Rangoli Restaurant",
        "priceStarts": {"perPersonPrice": 20, "currency": "AED"},
        "review": {"rating": "4.4", "reviewCount": "<p>Very Good</p>"},
        "restaurantMealTiming": [
            {
                "mealType": "Breakfast",
                "openingTiming": [
                    {"openingTime": "08:00 AM", "closingTime": "11:30 AM",
                     "isClosed": False, "weekDayName": d}
                    for d in ("Monday", "Tuesday", "Wednesday")
                ],
            },
            {
                "mealType": "Lunch",
                "openingTiming": [
                    {"openingTime": "11:30 AM", "closingTime": "03:30 PM",
                     "isClosed": False, "weekDayName": "Friday"},
                ],
            },
            {
                "mealType": "Dinner",
                "openingTiming": [
                    {"openingTime": "06:00 PM", "closingTime": "11:00 PM",
                     "isClosed": True, "weekDayName": "Sunday"},
                ],
            },
        ],
    }
    row.update(over)
    return {"result": {"list": [row]}}


def _one(**over):
    return parse_restaurant_response(_raw(**over))[0]


class TestReviewLabel:
    def test_html_verdict_is_stripped_and_kept(self):
        assert _one().review_label == "Very Good"

    def test_rating_display_pairs_number_and_verdict(self):
        assert _one().rating_display == "4.4 (Very Good)"

    def test_rating_alone_when_no_verdict(self):
        r = _one(review={"rating": "4.4"})
        assert r.rating_display == "4.4"

    def test_no_rating_gives_empty_display(self):
        assert _one(review={}, rating=None).rating_display == ""


class TestMealTimings:
    def test_each_meal_type_is_parsed(self):
        meals = {m["meal_type"] for m in _one().meal_timings}
        assert "Breakfast" in meals and "Lunch" in meals

    def test_identical_hours_across_days_collapse_to_one_range(self):
        bf = next(m for m in _one().meal_timings if m["meal_type"] == "Breakfast")
        assert bf["hours"] == "08:00 AM-11:30 AM"
        assert bf["days"] == ["Monday", "Tuesday", "Wednesday"]

    def test_closed_days_are_dropped(self):
        # Dinner is isClosed on its only day, so the meal has no open window.
        meals = {m["meal_type"] for m in _one().meal_timings}
        assert "Dinner" not in meals

    def test_display_lists_every_served_meal(self):
        shown = _one().meal_timings_display
        assert "Breakfast 08:00 AM-11:30 AM" in shown
        assert "Lunch 11:30 AM-03:30 PM" in shown

    def test_no_timings_gives_empty_display(self):
        assert _one(restaurantMealTiming=[]).meal_timings_display == ""


class TestToolsExposeTheDisplayStrings:
    def test_search_attaches_display_properties(self):
        # model_dump() drops @property values, so they must be added explicitly.
        from mcp_tools.search_restaurants import _impl

        out = _impl(destination_city="Dubai", search_date="2026-09-25", adults=2)
        assert out["options"], "expected restaurants"
        first = out["options"][0]
        for key in ("price_display", "rating_display", "meal_timings_display"):
            assert key in first, key

    def test_details_attaches_display_properties(self):
        from mcp_tools.get_restaurant_details import _impl

        out = _impl(restaurant_id=3, destination_city="Dubai",
                    search_date="2026-09-25", adults=2)
        r = out.get("restaurant") or {}
        for key in ("price_display", "rating_display", "meal_timings_display"):
            assert key in r, key

    def test_details_carries_real_meal_windows(self):
        # Live check: restaurant 3 publishes Breakfast, Lunch and Dinner.
        from mcp_tools.get_restaurant_details import _impl

        out = _impl(restaurant_id=3, destination_city="Dubai",
                    search_date="2026-09-25", adults=2)
        shown = (out.get("restaurant") or {}).get("meal_timings_display") or ""
        assert "Breakfast" in shown and "Lunch" in shown and "Dinner" in shown

    def test_instructions_tell_the_agent_to_use_both(self):
        from mcp_tools.search_restaurants import _impl

        note = _impl(destination_city="Dubai", search_date="2026-09-25",
                     adults=2)["agent_instructions"]
        assert "rating_display" in note and "meal_timings_display" in note
        assert "Never invent" in note


class TestReviewRatingWinsOverTopLevel:
    """Postman showed review.rating 4.4 for Rangoli; the chat said 4.

    The supplier sends BOTH a coarse top-level `rating` ("4.0") and the real
    `review.rating` ("4.4"). The parser preferred the top-level one.
    """

    def test_review_rating_takes_precedence(self):
        raw = {"result": {"list": [{
            "restaurantId": 3,
            "restaurantName": "Rangoli Restaurant",
            "priceStarts": {"perPersonPrice": 20, "currency": "AED"},
            "rating": "4.0",
            "review": {"rating": "4.4", "reviewCount": "<p>Very Good</p>"},
        }]}}
        r = parse_restaurant_response(raw)[0]
        assert r.rating == 4.4
        assert r.rating_display == "4.4 (Very Good)"

    def test_top_level_used_when_review_has_no_rating(self):
        raw = {"result": {"list": [{
            "restaurantId": 9, "restaurantName": "X",
            "priceStarts": {"perPersonPrice": 10, "currency": "AED"},
            "rating": "4.1", "review": {"reviewCount": "<p>Good</p>"},
        }]}}
        assert parse_restaurant_response(raw)[0].rating == 4.1

    def test_live_rangoli_reports_the_review_rating(self):
        # The exact case the client checked in Postman.
        from mcp_tools.get_restaurant_details import _impl

        out = _impl(restaurant_id=3, destination_city="Dubai",
                    search_date="2026-10-01", adults=1)
        assert (out.get("restaurant") or {}).get("rating") == 4.4


class TestFactsAreNotAnsweredFromMemory:
    def test_search_tells_the_agent_to_refetch_for_ratings(self):
        from mcp_tools.search_restaurants import _impl

        note = _impl(destination_city="Dubai", search_date="2026-10-01",
                     adults=1)["agent_instructions"]
        assert "restaurant_id" in note
        assert "get_restaurant_details" in note

    def test_prompt_forbids_answering_inventory_facts_from_recall(self):
        from pathlib import Path

        p = Path(__file__).resolve().parents[1] / "prompts" / "system_prompt_v3.md"
        text = p.read_text(encoding="utf-8")
        assert "never answer a factual question about inventory from memory" in text.lower()
