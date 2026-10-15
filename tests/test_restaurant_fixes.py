"""Restaurant suggestions — the client's QA report (Oct 2026), fixed at the root.

Every test here runs offline against supplier payloads captured live on
2026-10-14 (tests/fixtures/restaurant_dubai_*_live.json): the Dubai list (9
restaurants) and each one's details call. Place lookups are served from
canned entity-search rows copied from live responses.

What the report found, and the test class that pins each fix:
  Issue 1-3  Indus / Bukhara described from memory    TestLookupEntity, TestSearchFilters
  Issue 4    "open for dinner" read as availability    TestDetails, TestInstructions
  Issue 5    same list whatever area was asked         TestNearPlace, TestGeo
  Extra      only the 5 cheapest ever shown            TestSearchReturnsWholeCity
  Extra      Jain / budget answered from prose         TestSearchFilters
  Extra      Atithi described as "Italian"             TestParser
  Extra      split-shift hours read as lunch only      TestParser
  Extra      every restaurant and dish photo a 404     TestImages
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from tests.factories import load_fixture

LIST = "restaurant_dubai_list_live.json"
DETAILS = "restaurant_dubai_details_live.json"
DATE = "2026-10-21"  # a Wednesday

# Entity-search rows copied from live responses (2026-10-14). The hotel rows
# carry the supplier's placeholder position 25.27063, 55.30037.
_PLACEHOLDER = {"latitude": 25.27063, "longitude": 55.30037}
_ENTITY: dict[tuple[str, str], list[dict[str, Any]]] = {
    ("hotels", "deira dubai"): [
        {"location_id": 0, "name_text": "Deira", "type": "neighborhood", "city": "Deira",
         "latitude": 25.278847, "longitude": 55.33094},
        {"location_id": 11, "name_text": "Concorde Deira Hotel", "type": "hotel", "city": "Dubai", **_PLACEHOLDER},
        {"location_id": 12, "name_text": "ibis Deira Creekside Dubai", "type": "hotel", "city": "Dubai", **_PLACEHOLDER},
    ],
    # Unqualified, the worldwide search answers with Madeira.
    ("hotels", "deira"): [
        {"location_id": 0, "name_text": "Funchal", "type": "city", "city": "Funchal",
         "latitude": 32.6479, "longitude": -16.9097},
    ],
    ("hotels", "dubai marina"): [
        {"location_id": 0, "name_text": "Dubai Marina", "type": "neighborhood", "city": "Dubai Marina",
         "latitude": 25.080542, "longitude": 55.140343},
        {"location_id": 21, "name_text": "HiGuests - Marina Vista Tower 1", "type": "hotel", "city": "Dubai", **_PLACEHOLDER},
    ],
    ("hotels", "dubai frame"): [
        {"location_id": 31, "name_text": "Manzil 3BR with pool near Dubai Frame", "type": "hotel", "city": "Dubai", **_PLACEHOLDER},
        {"location_id": 0, "name_text": "Dubai Frame", "type": "point_of_interest", "city": "Dubai Frame",
         "latitude": 25.235336, "longitude": 55.300706},
    ],
    ("hotels", "dubai mall"): [
        {"location_id": 41, "name_text": "Vogue | Binghatti Corner JVC near Circle Mall", "type": "hotel",
         "city": "Dubai", **_PLACEHOLDER},
    ],
    ("tours", "dubai mall"): [
        {"location_id": 0, "name_text": "Ashas Kitchen at Wafi  Mall", "type": "Tour", "city": "Dubai",
         "latitude": 0, "longitude": 0},
        {"location_id": 51, "name_text": "Dubai Mall Kidzania", "type": "Tour", "city": "Dubai",
         "latitude": 25.199244, "longitude": 55.279691},
        {"location_id": 52, "name_text": "PlayDXB - Dubai Mall VR Park", "type": "Tour", "city": "Dubai",
         "latitude": 25.2, "longitude": 55.28},
        {"location_id": 53, "name_text": "Dubai Mall Trampo Extreme Admission", "type": "Tour", "city": "Dubai",
         "latitude": 25.2, "longitude": 55.28},
    ],
    ("hotels", "atlantis the palm dubai"): [
        {"location_id": 295, "name_text": "Atlantis, The Palm", "type": "hotel", "city": "Dubai", **_PLACEHOLDER},
    ],
    # Restaurant lookups. Asked for a name it lacks, the supplier returns the
    # city's other restaurants.
    ("restaurants", "armani ristorante"): [
        {"location_id": 3, "name_text": "Rangoli Restaurant", "type": "Restaurant", "city": "Dubai",
         "latitude": 25.252258, "longitude": 55.3043508},
        {"location_id": 2, "name_text": "Atithi Veg Restaurant", "type": "Restaurant", "city": "Dubai",
         "latitude": 25.2519875, "longitude": 55.2912656},
        {"location_id": 9, "name_text": "Bombay Bites Restaurant", "type": "Restaurant", "city": "Dubai",
         "latitude": 25.2569465, "longitude": 55.2988848},
    ],
    ("restaurants", "veganesha restaurant"): [
        {"location_id": 11, "name_text": "Veganesha", "type": "Restaurant", "city": "Dubai",
         "latitude": 25.2625, "longitude": 55.2974},
        {"location_id": 3, "name_text": "Rangoli Restaurant", "type": "Restaurant", "city": "Dubai",
         "latitude": 25.252258, "longitude": 55.3043508},
        {"location_id": 2, "name_text": "Atithi Veg Restaurant", "type": "Restaurant", "city": "Dubai",
         "latitude": 25.2519875, "longitude": 55.2912656},
    ],
    ("restaurants", "kathiyawadi thali"): [
        {"location_id": 1, "name_text": "Kathhiyawadi Thali Restaurant", "type": "Restaurant", "city": "Dubai",
         "latitude": 25.2346972, "longitude": 55.2963797},
        {"location_id": 5, "name_text": "Aamras Thali Restaurant, Dubai", "type": "Restaurant", "city": "Dubai",
         "latitude": 25.247589, "longitude": 55.3099803},
        {"location_id": 6, "name_text": "Maharaja Bhog Al Karama - Pure Vegetarian Thali Restaurant",
         "type": "Restaurant", "city": "Dubai", "latitude": 25.2512427, "longitude": 55.309154},
    ],
}
_HOTEL_STATIC = {295: {"hotelID": 295, "hotel_address": "Crescent Road The Palm",
                       "lat": 25.130456, "long": 55.116994}}


@pytest.fixture(autouse=True)
def offline_supplier(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Serve the captured supplier payloads; count calls; no network."""
    import booking_api
    from mcp_tools.result_cache import clear_result_cache

    clear_result_cache()
    details = load_fixture(DETAILS)
    calls: dict[str, Any] = {"details": [], "failing": set()}

    def search(**kw: Any) -> dict[str, Any]:
        return load_fixture(LIST)

    def detail(*, restaurant_id: int, **kw: Any) -> dict[str, Any]:
        calls["details"].append(restaurant_id)
        if restaurant_id in calls["failing"]:
            raise RuntimeError("supplier timeout")
        if str(restaurant_id) not in details:
            return {"result": {"detail": []}}
        return details[str(restaurant_id)]

    def entity(*, service: str, query: str, size: int = 10) -> dict[str, Any]:
        return {"data": _ENTITY.get((service, query.strip().lower()), [])}

    def hotel_static(*, hotel_ids: list[int]) -> dict[str, Any]:
        return {"PropertyAddressInfo": [_HOTEL_STATIC[h] for h in hotel_ids if h in _HOTEL_STATIC]}

    monkeypatch.setattr(booking_api, "call_restaurant_search", search)
    monkeypatch.setattr(booking_api, "call_restaurant_details", detail)
    monkeypatch.setattr(booking_api, "call_entity_search", entity)
    monkeypatch.setattr(booking_api, "call_hotel_property_info", hotel_static)
    import mcp_tools.lookup_entity as le

    monkeypatch.setattr(le, "call_entity_search", entity)
    yield calls
    clear_result_cache()


def _restaurant(rid: int):
    from parsers import parse_restaurant_response

    return parse_restaurant_response(load_fixture(DETAILS)[str(rid)])[0]


def _search(**kw: Any) -> dict[str, Any]:
    from mcp_tools.search_restaurants import _impl

    args: dict[str, Any] = {"destination_city": "Dubai", "search_date": DATE}
    args.update(kw)
    return _impl(**args)


def _names(out: dict[str, Any]) -> list[str]:
    return [o["name"] for o in out["options"]]


# =============================================================================
class TestParser:
    def test_feature_tags_come_from_component_name(self) -> None:
        tags = _restaurant(4).tags
        assert "Jain Friendly" in tags and "Premium Dining" in tags

    def test_every_cuisine_is_kept_primary_first(self) -> None:
        # Filed as "Italian", but the menu is chaat, biryani, Chinese and pasta.
        atithi = _restaurant(2)
        assert atithi.cuisine == "Italian"
        assert atithi.cuisines[0] == "Italian"
        assert {"Chinese Main Course", "Indian Chaat", "Pulav And Biryanis"} <= set(atithi.cuisines)

    @pytest.mark.parametrize(
        ("rid", "area"),
        [(3, "Al Karama"), (8, "Al Fahidi"), (9, "Al Mankhool"), (1, "Al Kifaf"),
         (2, ""),   # "772R+QGR - Dubai": a plus code is not an area
         (11, "")],  # no address at all
    )
    def test_area_is_read_from_the_address(self, rid: int, area: str) -> None:
        assert _restaurant(rid).area == area

    def test_split_shifts_are_all_kept_in_time_order(self) -> None:
        # Kathhiyawadi closes between lunch and dinner. Reading only the first
        # row reported it as open for one shift.
        wed = _restaurant(1).schedule_on(date(2026, 10, 21))
        assert wed["opening_hours"] == "12:00 PM-04:00 PM, 07:00 PM-11:15 PM"

    def test_hours_differ_by_weekday(self) -> None:
        rangoli = _restaurant(3)
        assert rangoli.schedule_on(date(2026, 10, 21))["opening_hours"] == "07:30 AM-11:45 PM"
        assert rangoli.schedule_on(date(2026, 10, 24))["opening_hours"] == "09:30 AM-10:45 PM"

    def test_meals_on_a_date(self) -> None:
        meals = {m["meal_type"]: m["hours"] for m in _restaurant(3).schedule_on(date(2026, 10, 21))["meals"]}
        assert meals["Dinner"] == "06:00 PM-11:00 PM"

    def test_dish_prices_keep_paise_and_child_rates(self) -> None:
        from core import to_inr
        from fx import live_rate_map

        thali = next(d for d in _restaurant(3).dishes if d.name == "Special Gujarati Thali")
        assert thali.adult_price_inr == pytest.approx(to_inr(62.0, "AED", rates=live_rate_map()))
        # Not rounded to whole rupees: the website shows the paise too.
        chaat = next(d for d in _restaurant(2).dishes if d.name == "Aloo Chaat")  # AED 21.5
        assert chaat.adult_price_inr == pytest.approx(to_inr(21.5, "AED", rates=live_rate_map()))
        assert chaat.adult_price_inr != round(chaat.adult_price_inr)
        kathi = _restaurant(1).dishes[0]
        assert kathi.child_price_inr == pytest.approx(to_inr(46.0, "AED", rates=live_rate_map()))
        assert kathi.child_price_display

    def test_starting_price_is_said_as_a_from_price(self) -> None:
        r = _restaurant(3)
        assert r.price_from_display.startswith("from ₹") and r.price_from_display.endswith("per person")
        lo, hi = r.menu_price_range
        assert lo == pytest.approx(r.price_per_adult_inr)
        assert hi > lo

    def test_list_only_rows_still_parse(self) -> None:
        from parsers import parse_restaurant_response

        rows = parse_restaurant_response(load_fixture(LIST))
        assert len(rows) == 9
        assert all(r.dishes == [] and r.cuisines for r in rows)


class TestImages:
    def test_restaurant_and_dish_photos_point_at_the_cdn(self) -> None:
        # The supplier sends ".../restaurantmedia/..." (lower case); a
        # case-sensitive check left every URL on the API host, which 404s.
        for rid in (1, 2, 3, 4, 5, 6, 8, 9, 11):
            r = _restaurant(rid)
            urls = [*r.image_urls, *(u for d in r.dishes for u in d.image_urls)]
            assert urls, rid
            assert all(u.startswith("https://d3bfv5x1dw8ekm.cloudfront.net/uploads/") for u in urls), rid

    def test_search_rows_carry_dish_photos(self) -> None:
        row = next(o for o in _search()["options"] if o["restaurant_id"] == 3)
        assert 0 < len(row["dish_photos"]) <= 4
        assert all(p["image_url"] and p["price_display"] for p in row["dish_photos"])

    def test_details_card_carries_every_dish_photo(self) -> None:
        from mcp_tools.get_restaurant_details import _impl

        out = _impl(restaurant_id=3, destination_city="Dubai", search_date=DATE)
        card = out["options"][0]
        assert len(card["dish_photos"]) == len(out["dishes"]) == 30
        assert "dish_photos" not in out["restaurant"]


class TestSearchReturnsWholeCity:
    def test_all_nine_not_the_cheapest_five(self) -> None:
        out = _search()
        assert out["total_results"] == out["total_matching"] == 9
        # The two best-rated used to be cut for being expensive.
        assert {"Veganesha", "Aamras Thali Restaurant, Dubai"} <= set(_names(out))

    def test_coverage_describes_the_catalogue(self) -> None:
        cov = _search(max_results=2)["coverage"]
        assert cov["restaurants_in_city"] == 9
        # Every restaurant with its id, even when only 2 rows are shown: a
        # follow-up about any of them must not read as "not found".
        assert len(cov["restaurants"]) == 9
        assert {"restaurant_id", "name", "area", "rating_display", "price_from_display"} <= set(
            cov["restaurants"][0]
        )
        assert cov["restaurant_types"] == ["Vegetarian"]
        assert "Al Karama" in cov["areas"] and "Jain Friendly" in cov["features"]

    def test_rating_sort(self) -> None:
        ratings = [o["rating"] for o in _search(sort_by="rating")["options"]]
        assert ratings == sorted(ratings, reverse=True)

    def test_default_order_is_starting_price(self) -> None:
        prices = [o["price_per_adult_inr"] for o in _search()["options"]]
        assert prices == sorted(prices)

    def test_failed_details_keep_the_restaurant(self, offline_supplier: dict[str, Any]) -> None:
        offline_supplier["failing"].add(5)
        out = _search()
        assert "Aamras Thali Restaurant, Dubai" in _names(out)
        assert "Aamras Thali Restaurant, Dubai" in out["agent_instructions"]

    def test_fanout_is_bounded(self, offline_supplier: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RESTAURANT_DETAIL_FANOUT", "3")
        _search()
        assert len(offline_supplier["details"]) == 3


class TestSearchFilters:
    def test_jain_uses_the_supplier_tag_not_the_description(self) -> None:
        # Rangoli's prose mentions a Jain diet; the supplier does not tag it.
        names = set(_names(_search(feature="Jain")))
        assert names == {
            "Atithi Veg Restaurant", "Puranmal Vegetarian Restaurant",
            "Khandani Rajdhani - Indian Restaurant in Karama",
            "Maharaja Bhog Al Karama - Pure Vegetarian Thali Restaurant",
            "Aamras Thali Restaurant, Dubai",
        }

    def test_budget_is_the_tag_not_the_cheapest_item(self) -> None:
        assert set(_names(_search(feature="budget"))) == {
            "Puranmal Vegetarian Restaurant", "Aamras Thali Restaurant, Dubai",
        }

    def test_fine_dining_matches_nothing_and_says_so(self) -> None:
        out = _search(feature="fine dining")
        assert out["options"] == [] and out["total_matching"] == 0
        assert "No restaurant we sell matches" in out["agent_instructions"]
        assert out["coverage"]["restaurants_in_city"] == 9

    @pytest.mark.parametrize("name", ["Indus", "Bukhara", "Armani Ristorante"])
    def test_restaurants_we_do_not_sell(self, name: str) -> None:
        out = _search(restaurant_name=name)
        assert out["options"] == []
        assert "do not describe any restaurant" in out["agent_instructions"]

    def test_name_tolerates_spelling(self) -> None:
        assert _names(_search(restaurant_name="Kathiyawadi")) == ["Kathhiyawadi Thali Restaurant"]

    def test_cuisine_reads_the_full_list(self) -> None:
        assert _names(_search(cuisine="Chinese")) == ["Atithi Veg Restaurant"]

    def test_dish_search_lists_the_matching_dishes(self) -> None:
        out = _search(dish="dosa")
        assert set(_names(out)) == {"Puranmal Vegetarian Restaurant", "Veganesha"}
        puranmal = next(o for o in out["options"] if o["restaurant_id"] == 8)
        assert {d["name"] for d in puranmal["matching_dishes"]} == {"Masala Dosa", "Rava Dosa"}
        assert all(d["price_display"] and d["image_url"] for d in puranmal["matching_dishes"])
        # The card leads with the dish they asked for.
        assert "Dosa" in puranmal["dish_photos"][0]["name"]


class TestBudget:
    def test_budget_lists_the_items_within_it(self) -> None:
        from core import to_inr
        from fx import live_rate_map

        budget = to_inr(25.0, "AED", rates=live_rate_map())
        out = _search(max_price_inr=budget)
        assert out["options"]
        for row in out["options"]:
            prices = [d["price_display"] for d in row["matching_dishes"]]
            assert prices, row["name"]
        assert "never present them or a 'from' price as the cost of a full meal" in out[
            "agent_instructions"
        ]
        # Kathhiyawadi's only item (AED 56) is over the budget.
        assert "Kathhiyawadi Thali Restaurant" not in _names(out)

    def test_budget_and_dish_combine(self) -> None:
        out = _search(dish="dosa", max_price_inr=1)
        assert out["options"] == [] and out["total_matching"] == 0


class TestNearPlace:
    def test_area_ranks_by_distance(self) -> None:
        out = _search(near="Deira")
        km = [o["distance_km"] for o in out["options"]]
        assert out["sorted_by"] == "distance" and km == sorted(km)
        assert out["near"]["resolved_to"] == "Deira"
        assert all(o["distance_from"] == "Deira" for o in out["options"])
        assert "never turn it into a travel time" in out["agent_instructions"]

    def test_dubai_frame_closest_is_kathhiyawadi(self) -> None:
        # The bot called Khandani (1.2 km) the closest; Kathhiyawadi is 0.4.
        first = _search(near="Dubai Frame")["options"][0]
        assert first["name"] == "Kathhiyawadi Thali Restaurant"
        assert first["distance_km"] == pytest.approx(0.4, abs=0.1)

    def test_an_area_passed_as_the_city_searches_that_city(self) -> None:
        # "We do not sell restaurants in 'Dubai Marina' — Dubai only" was false.
        out = _search(destination_city="Dubai Marina")
        assert not out.get("error")
        assert out["near"]["resolved_to"] == "Dubai Marina"
        assert min(o["distance_km"] for o in out["options"]) > 20

    def test_unplaceable_landmark_forbids_distance_claims(self) -> None:
        out = _search(near="Burj Khalifa")
        assert out["near"]["resolved_to"] is None
        assert all(o["distance_km"] is None for o in out["options"])
        assert "We could not place 'Burj Khalifa'" in out["agent_instructions"]

    def test_unknown_city_is_still_refused(self) -> None:
        out = _search(destination_city="Atlantis City")
        assert out["error"] and out["error_type"] == "UnsupportedRoute"


class TestGeo:
    def test_worldwide_namesake_outside_the_city_is_rejected(self) -> None:
        from geo import locate_place

        # "Deira" alone is Madeira; only the city-qualified query is trusted.
        assert locate_place("Deira", "Dubai").name == "Deira"
        assert locate_place("Deira") is None

    def test_product_named_after_a_place_is_not_the_place(self) -> None:
        from geo import locate_place

        place = locate_place("Dubai Mall", "Dubai")
        assert place.name == "Dubai Mall Kidzania"  # not "... JVC near Circle Mall"

    def test_hotel_position_comes_from_static_data(self) -> None:
        from geo import locate_place

        place = locate_place("Atlantis The Palm", "Dubai")
        assert (place.latitude, place.longitude) == (25.130456, 55.116994)

    def test_haversine(self) -> None:
        from geo import haversine_km

        assert haversine_km(25.235336, 55.300706, 25.2346972, 55.2963797) == pytest.approx(0.44, abs=0.02)


class TestDetails:
    def test_hours_on_the_requested_date(self) -> None:
        from mcp_tools.get_restaurant_details import _impl

        r = _impl(restaurant_id=3, destination_city="Dubai", search_date=DATE)["restaurant"]
        assert r["hours_on_date"]["weekday"] == "Wednesday"
        assert {"meal_type": "Dinner", "hours": "06:00 PM-11:00 PM"} in r["hours_on_date"]["meals"]

    def test_dish_rows_keep_their_original_keys(self) -> None:
        from mcp_tools.get_restaurant_details import _impl

        dish = _impl(restaurant_id=3, destination_city="Dubai", search_date=DATE)["dishes"][0]
        for key in ("dish_id", "name", "description", "cuisine", "price_inr", "rate_key",
                    "pax_rate_key", "images"):
            assert key in dish
        assert "<" not in dish["description"]  # supplier HTML stripped

    def test_unknown_id_is_not_found(self) -> None:
        from mcp_tools.get_restaurant_details import _impl

        out = _impl(restaurant_id=999, destination_city="Dubai", search_date=DATE)
        assert out["error"] and out["error_type"] == "NotFound"


class TestInstructions:
    def test_hours_are_never_availability(self) -> None:
        from mcp_tools.get_restaurant_details import _impl

        for note in (
            _search()["agent_instructions"],
            _impl(restaurant_id=3, destination_city="Dubai", search_date=DATE)["agent_instructions"],
        ):
            assert "not a table availability check" in note

    def test_prompt_carries_the_restaurant_rules(self) -> None:
        from pathlib import Path

        text = (Path(__file__).resolve().parents[1] / "prompts" / "system_prompt_v3.md").read_text(
            encoding="utf-8"
        )
        assert "We don't offer <name>" in text
        assert "search_restaurants(near=...)" in text
        assert "never travel time" in text
        assert "≠ table availability" in text
        assert "No links or URLs" in text
        assert "To reserve, offer our booking team" in text
        assert "max_price_inr" in text


class TestLookupEntity:
    def _lookup(self, query: str) -> dict[str, Any]:
        from mcp_tools.lookup_entity import _impl

        return _impl(service="restaurants", query=query, city="Dubai")

    def test_unrelated_rows_are_not_the_best_match(self) -> None:
        out = self._lookup("Armani Ristorante")
        assert out["best_match_id"] is None
        assert "We do not sell a restaurant matching 'Armani Ristorante'" in out["scoped_note"]
        assert "is the one they meant" not in out["usage_hint"]

    def test_not_found_never_talks_about_hotels(self) -> None:
        out = self._lookup("Indus")
        assert out["best_match_id"] is None
        assert "hotel" not in out["scoped_note"].lower()
        assert "Do NOT describe it" in out["scoped_note"]

    def test_generic_words_do_not_decide_the_match(self) -> None:
        assert self._lookup("Veganesha restaurant")["best_match_name"] == "Veganesha"
        assert self._lookup("Kathiyawadi thali")["best_match_name"] == "Kathhiyawadi Thali Restaurant"


class TestTextMatch:
    def test_spelling_variants_match_and_lookalikes_do_not(self) -> None:
        from text_match import matches

        assert matches("Kathiyawadi", "Kathhiyawadi Thali Restaurant")
        assert not matches("Indus", "Indian Cuisine")
        assert not matches("Italian", "North Indian")

    def test_values_are_matched_one_at_a_time(self) -> None:
        from text_match import matches_any

        assert matches_any("jain food", ["Jain Friendly", "Gujarati Food"]) == []
        assert matches_any("jain", ["Jain Friendly", "Gujarati Food"]) == ["Jain Friendly"]

    def test_starts_with(self) -> None:
        from text_match import starts_with

        assert starts_with("Dubai Mall Kidzania", "Dubai Mall")
        assert not starts_with("Apartment near Dubai Mall", "Dubai Mall")


def test_placeholder_restaurant_lists_are_gone() -> None:
    import rules

    for name in ("JAIN_FRIENDLY_RESTAURANTS", "HALAL_ONLY_RESTAURANTS", "suggest_restaurants"):
        assert not hasattr(rules, name), name


def test_supplier_refusal_reaches_the_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected date must not read as "no restaurants in Dubai"."""
    import booking_api

    reason = "SearchDate is required, must be in MM-DD-YYYY format, and must be today's date or a future date."
    monkeypatch.setattr(
        booking_api,
        "call_restaurant_search",
        lambda **kw: {"statusCode": 400, "error": {"code": "INVALID_REQUEST", "description": reason},
                      "result": None},
    )
    out = _search(search_date="2026-09-25")
    assert out["error"] is True
    assert "today's date or a future date" in out["message"]


class TestReplyGuard:
    def test_model_links_are_reduced_to_their_label(self) -> None:
        from rules import normalize_reply

        reply = (
            "| [Rangoli Restaurant](https://example.com) | 4.4 |\n"
            "| [Atithi](https://www.google.com/search?q=Atithi) | 4.5 |\n"
            "See [Veganesha](https://d3bfv5x1dw8ekm.cloudfront.net/uploads/x.webp)."
        )
        out = normalize_reply(reply)
        assert "](" not in out
        assert "| Rangoli Restaurant | 4.4 |" in out and "See Veganesha." in out

    def test_image_embeds_and_plain_brackets_survive(self) -> None:
        from rules import normalize_reply

        text = "![thali](https://cdn.example/t.webp) and [optional] notes"
        assert normalize_reply(text) == text


class TestEarlierSearchesAreSuperseded:
    """The model re-filtered old rows instead of searching again ("dinner in
    Deira" answered from a Marina search; a ₹500 budget from starting prices)."""

    def _state(self):
        import json as _json

        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        def call(i: int, args: dict) -> AIMessage:
            return AIMessage(content="", tool_calls=[
                {"name": "search_restaurants", "args": args, "id": f"c{i}", "type": "tool_call"}])

        old = ToolMessage(content=_json.dumps({"options": [{"name": "Rangoli"}],
                                               "search_params": {"near": "Dubai Marina"}}),
                          tool_call_id="c1", name="search_restaurants")
        new = ToolMessage(content=_json.dumps({"options": [{"name": "Maharaja Bhog"}],
                                               "search_params": {"near": "Deira"}}),
                          tool_call_id="c2", name="search_restaurants")
        return [
            HumanMessage(content="restaurants in Dubai Marina"), call(1, {"near": "Dubai Marina"}), old,
            AIMessage(content="Here are ..."),
            HumanMessage(content="dinner in Deira"), call(2, {"near": "Deira"}), new,
        ]

    def test_earlier_turn_rows_are_hidden_from_the_model(self) -> None:
        from agent import _supersede_earlier_searches

        msgs = self._state()
        seen = _supersede_earlier_searches({"messages": msgs})["llm_input_messages"]
        assert seen[2].content.startswith("[superseded]")
        assert "Rangoli" not in seen[2].content and "Dubai Marina" in seen[2].content
        assert seen[2].tool_call_id == "c1"  # history stays well-formed
        assert "Maharaja Bhog" in seen[6].content  # this turn's result is whole

    def test_the_stored_thread_is_untouched(self) -> None:
        from agent import _supersede_earlier_searches

        msgs = self._state()
        _supersede_earlier_searches({"messages": msgs})
        assert "Rangoli" in msgs[2].content


def test_city_centre_placeholder_is_not_a_location(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tour rows with no real position sit on Dubai's centre at 2 decimals."""
    import booking_api
    from geo import locate_place

    rows = {
        ("tours", "burj khalifa dubai"): [
            {"location_id": 28488, "name_text": "Burj Khalifa Tickets", "type": "Tour",
             "city": "Dubai", "latitude": 25.2, "longitude": 55.27},
        ],
    }
    monkeypatch.setattr(booking_api, "call_entity_search",
                        lambda *, service, query, size=10: {"data": rows.get((service, query.lower()), [])})
    assert locate_place("Burj Khalifa", "Dubai") is None


class TestDateIsOptional:
    """Asked "my friend is vegan, where can we eat?", the model asked for a
    date first because `search_date` was required. Restaurants need none."""

    def test_search_without_a_date_uses_the_supplier_today(self, offline_supplier) -> None:
        from core import supplier_today
        from mcp_tools.search_restaurants import _impl

        out = _impl(destination_city="Dubai")
        assert out["options"]
        assert out["search_params"]["search_date"] == supplier_today().isoformat()

    def test_details_without_a_date(self) -> None:
        from mcp_tools.get_restaurant_details import _impl

        assert _impl(restaurant_id=3, destination_city="Dubai")["restaurant"]["name"] == "Rangoli Restaurant"

    def test_supplier_today_is_dubai_time(self) -> None:
        from datetime import datetime, timedelta, timezone

        from core import SUPPLIER_TZ, supplier_today

        assert SUPPLIER_TZ.utcoffset(None) == timedelta(hours=4)
        assert supplier_today() == datetime.now(timezone(timedelta(hours=4))).date()
