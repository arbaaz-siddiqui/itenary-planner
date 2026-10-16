"""Hotel search covers the whole city, by property type and refundability.

Client report (hotels):
1. "Show me cheaper hotels": Novotel at Rs 44,066 called the lowest, while
   the website listed 713 hotels from Rs 18,181. We priced 40 IDs.
2. "4-5 star apartments": the website shows one (Swissotel Living Al Ghurair);
   we listed regular hotels, and the agent said we have no apartments.
3. "5 star refundable": Pullman and Movenpick Dubai Creek were called
   refundable; the website's Refundable filter shows neither, because their
   shown (cheapest) rate is non-refundable.
"""

from unittest.mock import patch

from mcp_tools.search_hotels import (
    _cheapest_rate_is_free,
    _cheapest_raw,
    _hotel_ids_to_search,
    _property_key,
)

CATALOGUE = (
    (399, 5.0, "Apartment"),  # Swissotel Living Al Ghurair
    (205, 5.0, "Hotel"),  # Pullman Dubai Creek City Centre
    (2133, 0.0, "Hotel"),  # Melody Queen, unrated
    (7, 4.0, "Aparthotel"),
    (8, 0.0, "Apartment"),
    (9, 2.0, "Hostel/Backpacker accommodation"),
)


def _ids(**kw):
    with (
        patch("mcp_tools.search_hotels.city_hotel_catalogue", return_value=CATALOGUE),
        patch("mcp_tools.search_hotels.get_hotel_ids_for_city", return_value=[]),
    ):
        return _hotel_ids_to_search(1, "dubai", **kw)


def test_the_whole_city_is_searched_not_a_batch_of_40() -> None:
    ids, _ = _ids(min_stars=0, max_stars=5)
    assert ids == [399, 205, 2133, 7, 8, 9]


def test_four_to_five_star_apartments_are_apartments_only() -> None:
    ids, cats = _ids(min_stars=4, max_stars=5, property_type="apartment")
    assert ids == [399]
    assert cats == {"399": "Apartment"}


def test_a_star_request_leaves_unrated_properties_out() -> None:
    ids, _ = _ids(min_stars=5, max_stars=5)
    assert ids == [399, 205]


def test_property_words_map_to_the_website_categories() -> None:
    assert _property_key("apartments") == "apartment"
    assert _property_key("Serviced hotel apartment") == "aparthotel"
    assert _property_key("hostel") == "hostel"
    assert _property_key("") == ""
    ids, _ = _ids(min_stars=0, max_stars=5, property_type="hostel")
    assert ids == [9]


def _hotel(hid: int, rooms: list[tuple[float, float]]) -> dict:
    """rooms: (price, cancellation charge of the first period)."""
    return {
        "HotelId": hid,
        "StartPrice": min(p for p, _ in rooms),
        "HotelOption": [
            {
                "HotelRooms": [
                    [
                        {
                            "Price": p,
                            "CancellationPolicy": [{"CancellationPrice": c}],
                        }
                    ]
                    for p, c in rooms
                ]
            }
        ],
    }


def test_refundable_means_the_shown_rate_is_refundable() -> None:
    pullman = _hotel(205, [(58136.0, 58136.0), (68396.0, 0.0)])  # cheapest is non-refundable
    avani = _hotel(11, [(26285.0, 0.0)])
    assert not _cheapest_rate_is_free(pullman)
    assert _cheapest_rate_is_free(avani)
    raw = {"AvailabilityRS": {"Currency": "INR", "HotelResult": [pullman, avani]}}
    kept = _cheapest_raw(raw, 10, refundable_only=True)["AvailabilityRS"]["HotelResult"]
    assert [h["HotelId"] for h in kept] == [11]


def test_the_cheapest_hotels_come_first() -> None:
    raw = {
        "AvailabilityRS": {
            "HotelResult": [
                _hotel(1, [(44066.0, 0.0)]),
                _hotel(2, [(18181.0, 18181.0)]),
                _hotel(3, [(30000.0, 0.0)]),
            ]
        }
    }
    kept = _cheapest_raw(raw, 2, refundable_only=False)["AvailabilityRS"]["HotelResult"]
    assert [h["HotelId"] for h in kept] == [2, 3]


def test_transfer_next_step_only_when_the_customer_asked() -> None:
    import rules
    from mcp_tools.search_hotels import _asked_for_transfer

    rules.set_conversation_text("show me cheaper hotels for 2 adults")
    assert not _asked_for_transfer()
    rules.set_conversation_text("hotel near the marina and an airport pickup")
    assert _asked_for_transfer()
    rules.set_conversation_text("")
