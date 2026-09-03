"""A tool call the provider cannot parse must not reach the customer.

The provider rejects a turn when the model emits an unparseable tool call, and
words the 400 differently each time. We matched one wording ("arguments must be
a JSON object"), so a live run died with a raw traceback on
`{'message': 'Extra data: line 1 column 85 (char 84)', 'code': 400}`.

The repair had a second hole: LangChain files an undecodable call under
`invalid_tool_calls`, not `tool_calls`, so the repair found nothing to drop,
left the thread untouched, and the retry re-sent the same poisoned history.
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent import _is_malformed_tool_args, _repair_thread


class TestTheProvider400IsRecognised:
    def test_json_parse_wordings_are_caught(self):
        for msg in (
            "Extra data: line 1 column 85 (char 84)",
            "Expecting value: line 1 column 1 (char 0)",
            "Expecting ',' delimiter: line 1 column 40",
            "Unterminated string starting at: line 1 column 12",
        ):
            assert _is_malformed_tool_args(ValueError({"message": msg, "code": 400})), msg

    def test_original_wording_still_caught(self):
        exc = ValueError({"message": "tool_calls.0.function.arguments must be a JSON object"})
        assert _is_malformed_tool_args(exc)

    def test_real_infrastructure_errors_propagate(self):
        # These must NOT be swallowed as a malformed tool call — retrying the
        # turn would hide a supplier or network fault.
        for msg in ("Connection reset by peer", "429 Too Many Requests",
                    "500 Server error for /api/tours/optionRate"):
            assert not _is_malformed_tool_args(ValueError(msg)), msg


class _FakeAgent:
    def __init__(self, msgs):
        self.msgs = msgs
        self.removed: list[str] | None = None

    def get_state(self, _cfg):
        class S:
            values = {"messages": self.msgs}
        s = S()
        s.values = {"messages": self.msgs}
        return s

    def update_state(self, _cfg, upd):
        self.removed = [m.id for m in upd["messages"]]


CFG = {"configurable": {"thread_id": "t"}}


class TestPoisonedThreadIsRepaired:
    def test_unparseable_call_and_its_orphan_are_dropped(self):
        bad = AIMessage(
            content="", id="ai-bad", tool_calls=[],
            invalid_tool_calls=[{"name": "get_tour_options", "args": "{tour_id:3064",
                                 "id": "call-1", "error": "Extra data: line 1 column 85"}],
        )
        orphan = ToolMessage(content="{}", id="tm-1", tool_call_id="call-1")
        agent = _FakeAgent([HumanMessage(content="hi", id="h1"), bad, orphan,
                            AIMessage(content="Here are the tours", id="ai-good")])
        _repair_thread(agent, CFG)
        assert agent.removed is not None
        assert "ai-bad" in agent.removed
        assert "tm-1" in agent.removed

    def test_good_messages_survive(self):
        bad = AIMessage(content="", id="ai-bad", tool_calls=[],
                        invalid_tool_calls=[{"name": "x", "args": "{", "id": "c1"}])
        agent = _FakeAgent([HumanMessage(content="hi", id="h1"), bad,
                            AIMessage(content="reply", id="ai-good")])
        _repair_thread(agent, CFG)
        assert "ai-good" not in (agent.removed or [])
        assert "h1" not in (agent.removed or [])

    def test_non_dict_args_shape_still_repaired(self):
        # pydantic rejects non-dict args at construction, so reproduce it the
        # way a checkpoint replay does: valid on creation, then mutated.
        bad = AIMessage(content="", id="ai-bad2",
                        tool_calls=[{"name": "search_tours", "args": {}, "id": "c2"}])
        bad.tool_calls[0]["args"] = "notadict"
        agent = _FakeAgent([bad])
        _repair_thread(agent, CFG)
        assert agent.removed == ["ai-bad2"]

    def test_clean_thread_is_left_alone(self):
        agent = _FakeAgent([HumanMessage(content="hi", id="h1"),
                            AIMessage(content="reply", id="ai-good")])
        _repair_thread(agent, CFG)
        assert agent.removed is None
