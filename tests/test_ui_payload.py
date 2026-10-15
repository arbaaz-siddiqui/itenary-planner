"""Full results for the cards, slim results for the model."""

from mcp_tools.ui_payload import fetch, full_result, split, wrap_tool


def _result():
    return {
        "options": [
            {
                "name": "Atithi Veg Restaurant",
                "restaurant_id": 2,
                "price_per_adult_inr": 339.97,
                "image_url": "https://cdn/x.webp",
                "image_urls": ["https://cdn/x.webp", "https://cdn/y.webp"],
                "dish_photos": [{"name": "Thali", "image_url": "https://cdn/t.webp"}],
                "opening_hours": [{"day": "Monday", "hours": "07:30 AM-11:45 PM"}],
                "opening_hours_display": "Daily 07:30 AM-11:45 PM",
            }
        ],
        "total_results": 1,
    }


def test_model_gets_names_ids_and_prices_but_no_pictures():
    slim = split(_result())
    row = slim["options"][0]
    assert row["name"] == "Atithi Veg Restaurant"
    assert row["restaurant_id"] == 2 and row["price_per_adult_inr"] == 339.97
    for k in ("image_url", "image_urls", "dish_photos", "opening_hours"):
        assert k not in row
    assert row["opening_hours_display"]
    assert slim["ui_ref"] and slim["ui_note"]


def test_cards_get_the_full_result_back():
    slim = split(_result())
    full = full_result(slim)
    assert full["options"][0]["dish_photos"][0]["name"] == "Thali"
    assert fetch(slim["ui_ref"]) is full


def test_results_without_options_pass_through():
    refusal = {"error": True, "error_type": "NeedsCustomerInput", "options": []}
    assert split(refusal) is refusal
    assert split("text") == "text"


def test_wrapping_a_tool_keeps_its_schema():
    from langchain_core.tools import tool

    def search(city: str) -> dict:
        """Find things."""
        return _result()

    t = wrap_tool(tool(search))
    out = t.invoke({"city": "Dubai"})
    assert "image_url" not in out["options"][0]
    assert "city" in t.args


def test_hotels_get_their_cdn_photos_by_id():
    from parsers import hotel_image_urls

    urls = hotel_image_urls(509)
    assert urls[0].endswith("/hotel-images/509/1.webp") and len(urls) == 8
    assert hotel_image_urls(None) == [] and hotel_image_urls(0) == []


def test_every_kind_searched_in_a_turn_gets_its_cards():
    import json

    from langchain_core.messages import AIMessage, ToolMessage

    from agent import extract_all_search_options

    msgs = [
        AIMessage(content="", tool_calls=[
            {"id": "a", "name": "search_hotels", "args": {}},
            {"id": "b", "name": "search_airport_transfer_dubai", "args": {}},
        ]),
        ToolMessage(content=json.dumps(split({"options": [{"hotel_name": "Social Hotel", "image_urls": ["x"]}]})),
                    tool_call_id="a", name="search_hotels"),
        ToolMessage(content=json.dumps({"options": [{"vehicle_name": "Toyota Hiace"}]}),
                    tool_call_id="b", name="search_airport_transfer_dubai"),
    ]
    groups = extract_all_search_options({"messages": msgs})
    assert [g["kind"] for g in groups] == ["hotel", "transfer"]
    # Hotels come back in full for the cards, photos included.
    assert groups[0]["options"][0]["image_urls"] == ["x"]
