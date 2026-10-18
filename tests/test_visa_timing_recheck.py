"""A visa-timing question is answered from a lookup for that date.

Asked on 18 Oct 2026 "my flight is on 22 oct, will the visa be ready in
time?", the model answered from an earlier 6 Nov lookup: "Yes, it will be
ready in time ... plenty of time". The timing check only ran on turns that
called get_visa_info, so the promise went out unchecked.
"""

from langchain_core.messages import AIMessage, ToolMessage

import rules
from agent import _visa_timing_unchecked


def _turn(with_lookup: bool) -> list:
    if not with_lookup:
        return [AIMessage(content="Yes, it will be ready in time.")]
    return [
        AIMessage(content="", tool_calls=[{"id": "1", "name": "get_visa_info", "args": {}}]),
        ToolMessage(content="{}", tool_call_id="1", name="get_visa_info"),
        AIMessage(content="Normal processing may not finish."),
    ]


def test_a_timing_question_without_a_lookup_is_rechecked() -> None:
    rules.set_conversation_text("x", latest="my flight is on 22 oct, will the visa be ready in time?")
    assert _visa_timing_unchecked(_turn(False))
    assert not _visa_timing_unchecked(_turn(True))
    rules.set_conversation_text("")


def test_other_visa_questions_are_left_alone() -> None:
    rules.set_conversation_text("x", latest="if my visa is rejected do i get a refund?")
    assert not _visa_timing_unchecked(_turn(False))
    rules.set_conversation_text("x", latest="will the hotel be ready at 10am?")
    assert not _visa_timing_unchecked(_turn(False))
    rules.set_conversation_text("")
