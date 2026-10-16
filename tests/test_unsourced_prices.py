"""A tool-less reply may only quote prices a tool returned in this chat.

Live case (16 Oct 2026): asked for the same Hyderabad-Dubai flights twice, the
model skipped the second search, said it had "already found" them and retyped
a table with fares no supplier had quoted.
"""

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import agent
from agent import _research_unsourced, _unsourced_prices

FLIGHTS = {"options": [
    {"airline": "IndiGo", "price_inr": 32242.87, "price_display": "₹32,242.87"},
    {"airline": "Flydubai", "price_inr": 36380.55},
]}


def _history(reply: str, *, searched_this_turn: bool = False):
    first = [
        HumanMessage(content="flights hyderabad to dubai 4 nov, 2 adults"),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "search_flights", "args": {}}]),
        ToolMessage(content=json.dumps(FLIGHTS), tool_call_id="c1", name="search_flights"),
        AIMessage(content="IndiGo ₹32,242.87, Flydubai ₹36,380.55"),
        HumanMessage(content="same flights again please"),
    ]
    turn = []
    if searched_this_turn:
        turn += [
            AIMessage(content="", tool_calls=[{"id": "c2", "name": "search_flights", "args": {}}]),
            ToolMessage(content=json.dumps(FLIGHTS), tool_call_id="c2", name="search_flights"),
        ]
    turn.append(AIMessage(content=reply))
    return first + turn, turn


def test_invented_fares_are_flagged():
    history, turn = _history("Flydubai ₹56,494.41, IndiGo ₹61,545.94")
    assert _unsourced_prices(history, turn) == ["56,494.41", "61,545.94"]


def test_fares_from_an_earlier_search_are_fine():
    history, turn = _history("The cheapest is IndiGo at ₹32,242.87.")
    assert _unsourced_prices(history, turn) == []


def test_customer_figures_may_be_echoed():
    history, turn = _history("Within your ₹50,000 budget, yes.")
    history[4] = HumanMessage(content="budget is 50,000")
    assert _unsourced_prices(history, turn) == []


def test_turn_with_a_search_is_left_to_other_guards():
    history, turn = _history("Flydubai ₹56,494.41", searched_this_turn=True)
    assert _unsourced_prices(history, turn) == []


def test_reply_without_prices_passes():
    history, turn = _history("Shall we look at hotels next?")
    assert _unsourced_prices(history, turn) == []


class _FakeAgent:
    def __init__(self, searches: bool):
        self.searches = searches
        self.prompts: list[str] = []

    def invoke(self, payload, config):
        self.prompts.append(payload["messages"][0]["content"])
        msgs = [HumanMessage(content=payload["messages"][0]["content"])]
        if self.searches:
            msgs += [
                AIMessage(content="", tool_calls=[{"id": "r1", "name": "search_flights", "args": {}}]),
                ToolMessage(content=json.dumps(FLIGHTS), tool_call_id="r1", name="search_flights"),
            ]
        msgs.append(AIMessage(content="IndiGo ₹32,242.87" if self.searches else "Flydubai ₹56,494.41"))
        return {"messages": msgs}

    def update_state(self, config, values):
        pass


def test_invented_reply_triggers_a_real_search():
    history, turn = _history("Flydubai ₹56,494.41")
    fake = _FakeAgent(searches=True)
    retry = _research_unsourced(fake, {}, history, turn, thread_id="t")
    assert fake.prompts == [agent._RESEARCH_MARKER]
    assert agent._final_reply(retry) == "IndiGo ₹32,242.87"


def test_retry_that_still_invents_is_not_shown():
    history, turn = _history("Flydubai ₹56,494.41")
    retry = _research_unsourced(_FakeAgent(searches=False), {}, history, turn, thread_id="t")
    assert agent._final_reply(retry) == agent._COULD_NOT_FINISH


def test_sourced_reply_makes_no_retry():
    history, turn = _history("IndiGo ₹32,242.87")
    fake = _FakeAgent(searches=True)
    assert _research_unsourced(fake, {}, history, turn, thread_id="t") is None
    assert fake.prompts == []


# --- Misspelt cities (live: "Ahemdabad" made the search impossible) ---------

def test_misspelt_city_resolves():
    from reference_data_loader import resolve_iata

    assert resolve_iata("Ahemdabad") == "AMD"
    assert resolve_iata("hydrabad") == "HYD"


def test_ordinary_words_never_become_cities():
    from reference_data_loader import resolve_city

    for word in ("flights", "return", "journey", "adults", "looking", "tomorrow", "desert"):
        assert resolve_city(word) is None, word


def test_corrected_spelling_counts_as_what_the_customer_said():
    from rules import customer_said, set_conversation_text

    set_conversation_text("flights from Ahemdabad to DXB 20 oct to 26 oct, 2 adults")
    assert customer_said("Ahmedabad")
    assert not customer_said("Mumbai")


def test_airline_logo_url_uses_cdn_uploads():
    from parsers import MEDIA_CDN_BASE, airline_logo_url

    assert airline_logo_url("/Flightlogos/AI.svg") == MEDIA_CDN_BASE + "/uploads/Flightlogos/AI.svg"
    assert airline_logo_url("") == ""


def test_repeated_request_answered_from_memory_reruns_the_search():
    history, turn = _history("I have already pulled them. IndiGo ₹32,242.87 is cheapest.")
    history[4] = HumanMessage(content=history[0].content)
    assert _unsourced_prices(history, turn)


def test_repeated_request_pointing_to_table_above_reruns():
    history, turn = _history("You can see them in the table above.")
    history[4] = HumanMessage(content=history[0].content)
    assert _unsourced_prices(history, turn) == ["(repeated request)"]


def test_new_question_answered_from_earlier_results_is_fine():
    history, turn = _history("IndiGo ₹32,242.87 is the cheapest of those.")
    assert _unsourced_prices(history, turn) == []
