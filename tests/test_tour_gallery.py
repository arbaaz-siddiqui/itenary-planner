"""Tour photo gallery from /api/tours/medias, plus option-text cleanup.

Search returns one photo per tour; the website shows 6 to 15 (Global Village
46045 has 8, Desert Safari 53632 has 6, checked 2026-10-16).
"""

from parsers import MEDIA_CDN_BASE, parse_tour_medias

MEDIAS = {"statusCode": 200, "result": [
    {"imageId": 2, "imagePath": "a29cd3ee-d050-a34a-3a53-3a20e4faf5f3/TourMedia/53632/b_R.webp", "displayOrder": 1},
    {"imageId": 1, "imagePath": "a29cd3ee-d050-a34a-3a53-3a20e4faf5f3/TourMedia/53632/a_R.webp", "displayOrder": 0},
    {"imageId": 3, "imagePath": "tour-images/EMR/2/3.webp", "displayOrder": 2},
    {"imageId": 4, "imagePath": "tour-images/EMR/2/3.webp", "displayOrder": 3},
]}


def test_photos_come_back_in_display_order_as_cdn_urls():
    assert parse_tour_medias(MEDIAS) == [
        MEDIA_CDN_BASE + "/uploads/a29cd3ee-d050-a34a-3a53-3a20e4faf5f3/TourMedia/53632/a_R.webp",
        MEDIA_CDN_BASE + "/uploads/a29cd3ee-d050-a34a-3a53-3a20e4faf5f3/TourMedia/53632/b_R.webp",
        MEDIA_CDN_BASE + "/tour-images/EMR/2/3.webp",
    ]


def test_empty_or_odd_response_gives_nothing():
    assert parse_tour_medias({"result": []}) == []
    assert parse_tour_medias(None) == []
    assert parse_tour_medias({"result": None}) == []


def test_fetch_failure_is_not_an_error(monkeypatch):
    import booking_api.endpoints as endpoints
    from mcp_tools import tour_gallery

    def boom(**_):
        raise RuntimeError("down")

    monkeypatch.setattr(endpoints, "call_tour_medias", boom)
    tour_gallery._CACHE.clear()
    assert tour_gallery.tour_image_urls(999999) == []


def test_markdown_option_text_becomes_clean_bullets_and_titles_merge():
    from parsers import parse_tour_option_description

    raw = {"result": [
        {"type": "About this package", "descriptionText": "## What's included\n\n- Admission to: Global Village"},
        {"type": "Things to note", "descriptionText": "## Disclaimer\n- By booking you accept the terms"},
        {"type": "Things to note", "descriptionText": "## Eligibility\n\n- Free for children aged 0-2\n- Free for seniors"},
        {"type": "Inclusions", "descriptionText": "<ul><li>Hotel pickup</li></ul>"},
    ]}
    out = parse_tour_option_description(raw)
    assert [s["title"] for s in out] == ["About this package", "Things to note", "Inclusions"]
    assert out[0]["items"] == ["Admission to: Global Village"]
    assert out[1]["items"] == ["By booking you accept the terms", "Free for children aged 0-2", "Free for seniors"]
    assert out[2]["items"] == ["Hotel pickup"]


def test_paisa_rounding_matches_the_website():
    from mcp_tools.tour_pricing import half_up

    assert half_up(1180.25 / 2) == 590.13
    assert half_up(3472.0 / 2) == 1736.0


def test_unpriced_tour_is_replaced_by_the_priced_listing(monkeypatch):
    import booking_api.endpoints as endpoints
    import mcp_tools.get_tour_options as gto
    import mcp_tools.search_tours as st

    monkeypatch.setattr(gto, "customer_gave_date", lambda: True, raising=False)
    import rules
    monkeypatch.setattr(rules, "customer_gave_date", lambda: True)
    monkeypatch.setattr(gto, "_cached", lambda **_: {"options": [
        {"name": "Global Village Any Day Ticket - Person", "price_display": "On request"}]})
    monkeypatch.setattr(endpoints, "call_tour_details",
                        lambda **_: {"result": {"tourName": "Global Village Ticket in Dubai", "cityName": "Dubai"}})
    monkeypatch.setattr(st, "_impl", lambda **kw: {"options": [
        {"tour_id": 46045, "name": "Global Village Ticket in Dubai", "price_per_adult_inr": 0},
        {"tour_id": 53827, "name": "Global Village Dubai Tickets by Gujjutours", "price_per_adult_inr": 590.13},
    ]})
    out = gto.get_tour_options(tour_id=46045, travel_date="2026-11-10", adults=2,
                               tour_name="Global Village Ticket in Dubai")
    assert [o["tour_id"] for o in out["options"]] == [53827]
    assert out["replaced_tour_id"] == 46045


def test_priced_tour_is_left_alone(monkeypatch):
    import mcp_tools.get_tour_options as gto
    import rules

    monkeypatch.setattr(rules, "customer_gave_date", lambda: True)
    priced = {"options": [{"name": "Ticket", "prices": [{"total_inr": 1180.25}]}]}
    monkeypatch.setattr(gto, "_cached", lambda **_: priced)
    assert gto.get_tour_options(tour_id=53827, travel_date="2026-11-10", adults=2) is priced
