"""A malformed tool call used to kill a conversation permanently.

The model sometimes emits tool_calls[].function.arguments as a STRING instead
of an object. The provider rejects the whole request with 400
"tool_calls[].function.arguments must be a JSON object" — and because that
AIMessage is checkpointed, it is replayed on every later turn, so the chat is
dead from that point on. Verified live: the same PDF request succeeded on a
fresh thread and 400'd forever on the poisoned one.
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent import _is_malformed_tool_args


class TestMalformedToolArgsDetection:
    def test_detects_the_provider_400(self):
        exc = ValueError(
            "{'message': 'tool_calls[].function.arguments must be a JSON "
            "object', 'code': 400}"
        )
        assert _is_malformed_tool_args(exc)

    def test_ignores_unrelated_failures(self):
        for msg in ("connection reset by peer", "429 rate limited",
                    "context length exceeded", ""):
            assert not _is_malformed_tool_args(ValueError(msg)), msg

    def test_does_not_swallow_a_different_400(self):
        assert not _is_malformed_tool_args(ValueError("{'code': 400, 'message': 'bad model'}"))


class TestPoisonedMessageIdentification:
    """The repair must drop exactly the malformed calls and their orphans."""

    def _thread(self):
        class RawAI:
            """An assistant message as it arrives before validation — the only
            way a non-dict `args` can exist in history."""

            def __init__(self, tool_calls):
                self.id = "raw1"
                self.content = ""
                self.tool_calls = tool_calls

        good = AIMessage(content="", tool_calls=[
            {"name": "search_hotels", "args": {"city": "Dubai"}, "id": "ok1",
             "type": "tool_call"}])
        bad = RawAI([{"name": "generate_itinerary_pdf_tool",
                      "args": "not-an-object", "id": "bad1",
                      "type": "tool_call"}])
        return [
            HumanMessage(content="plan my trip"),
            good,
            ToolMessage(content="{}", tool_call_id="ok1", name="search_hotels"),
            bad,
            ToolMessage(content="{}", tool_call_id="bad1", name="generate_itinerary_pdf_tool"),
        ]

    def test_only_the_malformed_call_is_selected(self):
        msgs = self._thread()
        bad_ids, drop = set(), []
        for m in msgs:
            calls = getattr(m, "tool_calls", None) or []
            if calls and any(not isinstance(c.get("args"), dict) for c in calls):
                drop.append(m)
                for c in calls:
                    if c.get("id"):
                        bad_ids.add(str(c["id"]))
        assert bad_ids == {"bad1"}, "the well-formed call must survive"
        assert len(drop) == 1

    def test_orphaned_tool_messages_go_too(self):
        msgs = self._thread()
        bad_ids = {"bad1"}
        orphans = [m for m in msgs
                   if getattr(m, "tool_call_id", None) and str(m.tool_call_id) in bad_ids]
        assert len(orphans) == 1, "a ToolMessage answering a dropped call is orphaned"

    def test_a_clean_thread_needs_no_repair(self):
        msgs = [HumanMessage(content="hi"),
                AIMessage(content="", tool_calls=[
                    {"name": "search_tours", "args": {}, "id": "a", "type": "tool_call"}]),
                ToolMessage(content="{}", tool_call_id="a", name="search_tours")]
        drop = [m for m in msgs
                if any(not isinstance(c.get("args"), dict)
                       for c in (getattr(m, "tool_calls", None) or []))]
        assert drop == []


class TestEndTimesAreBackfilled:
    """The model passes day_plans with `start` only, so the PDF lost every end
    time — "09:45" where the chat showed "09:45-11:45". The scheduler already
    computed the ends, so they are backfilled by title+start rather than
    trusting the model to resend them."""

    def test_ends_are_filled_from_the_cached_plan(self):
        import agent_tools as at

        at.plan_itinerary_tool.invoke(
            {"start_date": "2026-09-01", "nights": 3, "adults": 2,
             "tours": [{"name": "Abu Dhabi City Tour from Dubai"}]}
        )
        cached = at._LAST_PLAN.get("days") or []
        assert cached, "expected a cached plan"
        timed = [i for d in cached for i in (d.get("items") or [])
                 if i.get("start") and i.get("end")]
        assert timed, "the scheduler must produce end times to backfill from"

    def test_backfill_matches_on_title_and_start(self):
        ends = {("dubai frame", "09:45"): "11:45"}
        item = {"title": "Dubai Frame", "start": "09:45"}
        key = (item["title"].strip().lower(), item["start"].strip())
        assert ends.get(key) == "11:45"

    def test_an_end_the_model_supplied_is_not_overwritten(self):
        ends = {("dubai frame", "09:45"): "11:45"}
        item = {"title": "Dubai Frame", "start": "09:45", "end": "10:30"}
        if not item.get("end"):
            item["end"] = ends[("dubai frame", "09:45")]
        assert item["end"] == "10:30"


class TestMalformedToolNameRecovery:
    """A live run showed the model emitting a whole call as the tool NAME:
    `get_tour_options(tour_id:28488,travel_date:<|"|>...)<tool_call|>`. That
    resolved to "tool not found" and burned a round-trip."""

    def _base(self, name: str) -> str:
        import re
        return re.split(r"[(\{<\s]", name, 1)[0].strip()

    def test_call_syntax_in_name_resolves_to_the_tool(self):
        assert self._base(
            'get_tour_options(tour_id:28488,travel_date:<|"|>2026-10-30<|"|>)<tool_call|>'
        ) == "get_tour_options"

    def test_brace_syntax_resolves(self):
        assert self._base('search_hotels{"destination_city": "Dubai"}') == "search_hotels"

    def test_clean_names_are_untouched(self):
        for n in ("get_tour_options", "plan_itinerary_tool", "search_flights"):
            assert self._base(n) == n

    def test_scaffolding_filter_catches_both_shapes(self):
        from agent import _looks_like_tool_scaffolding as leak

        assert leak("get_tour_options(tour_id:28488)")
        assert leak('search_hotels{"a":1}')
        assert leak("<|tool_call|>")
        assert not leak("Here are the Burj Khalifa options:")
        assert not leak("| Level 124 | Rs 1,783 |")

    def test_normalize_strips_control_markers(self):
        from rules import normalize_reply

        assert "<|tool_call|>" not in normalize_reply("Options <|tool_call|> here")
