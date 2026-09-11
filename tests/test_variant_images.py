"""Tour variants inherit the tour's image, because they have none of their own.

The variants view rendered every card as an empty placeholder. The cause was
not a broken URL: `get_tour_options` emitted no image field at all, while
`search_tours` on the same tour emitted a working CDN URL.

Verified against the live supplier: tour 30647's five option rows carry no
image, photo or media key of any kind -- variants are ticket types of ONE tour,
so the tour's picture is the right one to show.
"""

from unittest.mock import patch

from mcp_tools.get_tour_options import _impl, _tour_image_url

TOUR = 30647
DATE = "2026-09-15"
CDN = "https://d3bfv5x1dw8ekm.cloudfront.net"

OPTIONS = [
    {"optionId": 1, "optionName": "Dubai Desert Safari",
     "supplierId": 2, "validateTourOption": [{"transferTypeId": 3}]},
    {"optionId": 2, "optionName": "Desert Safari with Shisha",
     "supplierId": 2, "validateTourOption": [{"transferTypeId": 3}]},
]


def _run(image: str) -> list[dict]:
    raw = {"result": {"tourOptionlist": OPTIONS}}
    rate = {"result": [{"rate": 200.0, "currencyCode": "AED"}]}
    with patch("booking_api.endpoints.call_tour_options", lambda **k: raw), \
         patch("booking_api.endpoints.call_tour_option_rate", lambda **k: rate), \
         patch("mcp_tools.get_tour_options._tour_image_url", lambda *a, **k: image):
        out = _impl(tour_id=TOUR, travel_date=DATE, adults=2)
    return out.get("options") or []


def test_every_variant_carries_the_tours_image():
    rows = _run(f"{CDN}/tour-images/RYT/509316/x.webp")
    assert rows, "expected variants"
    for r in rows:
        assert r["image_url"] == f"{CDN}/tour-images/RYT/509316/x.webp"


def test_a_tour_without_an_image_does_not_emit_an_empty_one():
    # imagePath is genuinely "" for some tours (53633). The card must fall back
    # to its placeholder rather than trying to load an empty URL.
    rows = _run("")
    assert rows, "expected variants"
    for r in rows:
        assert not r.get("image_url")


def test_the_image_lookup_never_raises():
    # A picture is cosmetic: a failure here must not break the variant lookup.
    #
    # The cache must be cleared first. This lookup is cached at the STATIC
    # tier, so any earlier test (or an earlier live call in the same run) that
    # populated it would return that URL and the supplier failure would never
    # be exercised -- the test passed alone and failed in the full suite.
    from mcp_tools.result_cache import clear_result_cache

    clear_result_cache()
    with patch("booking_api.endpoints.call_tour_search",
               side_effect=RuntimeError("supplier down")):
        assert _tour_image_url(TOUR, DATE) == ""


class TestTheTourImageIsResolvedToTheCdn:
    """The API host 404s on media; the CDN serves it."""

    def _image(self, image_path: str) -> str:
        raw = {"result": {"tourStaticlists": [
            {"tourID": TOUR, "imagePath": image_path},
        ]}}
        from mcp_tools.result_cache import clear_result_cache

        # Bypass the static-tier cache so each case really calls through.
        clear_result_cache()
        with patch("booking_api.endpoints.call_tour_search", lambda **k: raw):
            return _tour_image_url(TOUR, DATE)

    def test_a_plain_cdn_path_resolves(self):
        assert self._image("tour-images/RYT/1/a.webp") == (
            f"{CDN}/tour-images/RYT/1/a.webp"
        )

    def test_a_tenant_scoped_path_gets_the_uploads_prefix(self):
        # Without /uploads the CDN answers 403.
        assert self._image("/guid/TourMedia/53632/b.jpg") == (
            f"{CDN}/uploads/guid/TourMedia/53632/b.jpg"
        )

    def test_an_empty_path_stays_empty(self):
        assert self._image("") == ""
