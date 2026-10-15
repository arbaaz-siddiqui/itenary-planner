"""Ask / Plan mode and the profile name on the chat API."""

import pytest
from pydantic import ValidationError

from agent import _customer_part
from api.main import _MODE_CONTEXT, ChatRequest, ProfileUpdate, Registration, _user_out


def test_mode_defaults_to_ask_and_rejects_unknown_values():
    assert ChatRequest(session_id="s", message="hi").mode == "ask"
    assert ChatRequest(session_id="s", message="hi", mode="plan").mode == "plan"
    with pytest.raises(ValidationError):
        ChatRequest(session_id="s", message="hi", mode="book")


def test_plan_mode_is_context_not_the_customers_words():
    sent = "[system] Known: city Ahmedabad\n\n" + _MODE_CONTEXT["plan"] + "Plan a Dubai trip"
    # Tools that check what the customer said (dates, cities) must see only
    # their own words, never the instruction text.
    assert _customer_part(sent) == "Plan a Dubai trip"
    assert "ask" not in _MODE_CONTEXT  # Ask is the default behaviour


def test_registration_names_are_optional_for_older_clients():
    r = Registration(email="a@example.com", password="12345678")
    assert (r.first_name, r.last_name) == ("", "")
    with pytest.raises(ValidationError):
        ProfileUpdate(first_name="", last_name="X")


def test_user_out_always_carries_name_fields():
    out = _user_out({"id": 1, "email": "a@example.com"})
    assert out == {"id": "1", "email": "a@example.com", "first_name": "", "last_name": ""}


def test_preferences_validate_and_cover_the_remembered_keys():
    from api.main import _PREF_KEYS, Preferences

    p = Preferences(origin_city="Ahmedabad", usual_party_size=4, diet="Jain")
    assert set(p.model_dump()) == set(_PREF_KEYS)
    with pytest.raises(ValidationError):
        Preferences(diet="Keto")
    with pytest.raises(ValidationError):
        Preferences(usual_party_size=0)
