"""Tour photo gallery read from the website's tour page.

Search returns one photo per tour; the website shows 6 to 15 (Desert Safari
53632 has 6, tour 28488 has 15, checked 2026-10-16).
"""

from parsers import MEDIA_CDN_BASE, parse_tour_gallery

# Shape as embedded in the page: escaped JSON inside a script string.
PAGE = (
    'self.__next_f.push([1,"{\\"tourID\\":53632,\\"tourImages\\":['
    '{\\"imageId\\":2,\\"imagePath\\":\\"a29cd3ee-d050-a34a-3a53-3a20e4faf5f3/TourMedia/53632/b_R.webp\\",'
    '\\"displayOrder\\":1,\\"isSupplierPath\\":false},'
    '{\\"imageId\\":1,\\"imagePath\\":\\"a29cd3ee-d050-a34a-3a53-3a20e4faf5f3/TourMedia/53632/a_R.webp\\",'
    '\\"displayOrder\\":0,\\"isSupplierPath\\":false},'
    '{\\"imageId\\":3,\\"imagePath\\":\\"tour-images/EMR/2/3.webp\\",\\"displayOrder\\":2}'
    '],\\"vehicleList\\":[]}"])'
)


def test_photos_come_back_in_display_order_as_cdn_urls():
    assert parse_tour_gallery(PAGE) == [
        MEDIA_CDN_BASE + "/uploads/a29cd3ee-d050-a34a-3a53-3a20e4faf5f3/TourMedia/53632/a_R.webp",
        MEDIA_CDN_BASE + "/uploads/a29cd3ee-d050-a34a-3a53-3a20e4faf5f3/TourMedia/53632/b_R.webp",
        MEDIA_CDN_BASE + "/tour-images/EMR/2/3.webp",
    ]


def test_page_without_a_gallery_gives_nothing():
    assert parse_tour_gallery("<html>no tour here</html>") == []
    assert parse_tour_gallery("") == []


def test_fetch_failure_is_not_an_error(monkeypatch):
    import booking_api.endpoints as endpoints
    from mcp_tools import tour_gallery

    def boom(**_):
        raise RuntimeError("down")

    monkeypatch.setattr(endpoints, "call_tour_page", boom)
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
