"""The PDF is built from the chat's plan, even after the API restarts.

The plan cache is in memory. After a restart (every Railway deploy) "generate
the pdf" found no plan, the model re-typed the days from memory, and the PDF
said "Dinner 18:00" where the plan had the desert safari's BBQ at 19:30.
"""

from agent_tools import _LAST_PLANS, _last_plan


def _card() -> dict:
    return {
        "role": "assistant",
        "options": [
            {
                "kind": "itinerary",
                "items": [
                    {
                        "start_date": "2026-11-06",
                        "adults": 2,
                        "total_inr": 30911.52,
                        "days": [
                            {
                                "day_number": 1,
                                "date": "2026-11-06",
                                "items": [
                                    {
                                        "start": "19:30",
                                        "title": "Dinner (included in Dubai Desert Safari)",
                                        "kind": "meal",
                                    }
                                ],
                            },
                            {"day_number": 2, "date": "2026-11-07", "items": []},
                        ],
                        "package_lines": [{"item": "Hotel (Social Hotel)", "amount_inr": 11281.23}],
                    }
                ],
            }
        ],
    }


def test_the_saved_itinerary_card_restores_the_plan() -> None:
    import rules
    from api.main import _seed_plan_from_history

    _LAST_PLANS.pop("t-seed", None)
    _seed_plan_from_history("t-seed", [{"role": "user", "options": None}, _card()])
    rules.set_current_thread("t-seed")
    plan = _last_plan()
    assert plan["nights"] == 1 and plan["hotel_name"] == "Social Hotel"
    assert plan["days"][0]["items"][0]["start"] == "19:30"
    rules.set_current_thread("")
    _LAST_PLANS.pop("t-seed", None)


def test_a_newer_plan_in_memory_is_not_overwritten() -> None:
    from api.main import _seed_plan_from_history

    _LAST_PLANS["t-keep"] = {"days": [{"items": []}], "total_inr": 1.0}
    _seed_plan_from_history("t-keep", [_card()])
    assert _LAST_PLANS["t-keep"]["total_inr"] == 1.0
    _LAST_PLANS.pop("t-keep", None)
