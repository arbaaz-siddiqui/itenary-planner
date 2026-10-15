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
