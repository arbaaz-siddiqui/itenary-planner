"""The customer received a completely blank message. Two separate causes.

1. OpenRouter routes a model across upstream hosts, and three of them return
   HTTP 200 with `content: null`, no tool_calls and finish_reason "stop" while
   billing completion tokens — the reply is generated and dropped upstream.
   Measured 2026-09-06 with the real agent payload (11 KB prompt + 36 tool
   schemas), 4 calls each: NextBit 4/4 null, Parasail 4/4, SiliconFlow 4/4;
   Google, DeepInfra, Cloudflare and Novita 0/4. With OPENROUTER_PROVIDERS
   empty we were routed onto the broken ones and one query came back blank in
   4 runs out of 5.

2. Asked to compare two restaurants the model wants two `lookup_entity` calls
   and emits ONE whose arguments are two JSON objects concatenated:
   `{"query": "Rangoli", ...}{"query": "Bombay Bites", ...}`. LangChain accepts
   it, but the provider rejects the message when it is replayed in history
   ("Extra data: line 1 column 64") — char 63 is exactly where the second `{`
   starts. The turn dies, and it stays dead because the poisoned message is now
   in the thread. Reproduced 3 times out of 3.
"""

from langchain_core.messages import AIMessage, HumanMessage

from agent import _split_concatenated_args

CONCATENATED = (
    '{"city": "Dubai", "query": "Rangoli", "service": "restaurants"}'
    '{"city": "Dubai", "query": "Bombay Bites", "service": "restaurants"}'
)


def _msg_with(args):
    m = AIMessage(content="", id="ai-1",
                  tool_calls=[{"name": "lookup_entity", "args": {}, "id": "c1"}])
    # pydantic rejects a non-dict at construction, so set it the way a provider
    # response does.
    m.tool_calls[0]["args"] = args
    return m


class TestConcatenatedArgsAreRecovered:
    def test_two_objects_become_two_calls(self):
        out = _split_concatenated_args({"messages": [_msg_with(CONCATENATED)]})
        assert out is not None, "concatenated args were not split"
        calls = out["messages"][0].tool_calls
        assert len(calls) == 2
        assert [c["args"]["query"] for c in calls] == ["Rangoli", "Bombay Bites"]

    def test_both_calls_keep_the_tool_name(self):
        out = _split_concatenated_args({"messages": [_msg_with(CONCATENATED)]})
        assert {c["name"] for c in out["messages"][0].tool_calls} == {"lookup_entity"}

    def test_call_ids_stay_unique(self):
        # Two ToolMessages cannot answer one id; duplicate ids corrupt the thread.
        out = _split_concatenated_args({"messages": [_msg_with(CONCATENATED)]})
        ids = [c["id"] for c in out["messages"][0].tool_calls]
        assert len(set(ids)) == len(ids)

    def test_the_repaired_message_replaces_the_original(self):
        out = _split_concatenated_args({"messages": [_msg_with(CONCATENATED)]})
        assert out["messages"][0].id == "ai-1"
        assert not out["messages"][0].invalid_tool_calls

    def test_separator_comma_is_tolerated(self):
        joined = CONCATENATED.replace("}{", "}, {")
        out = _split_concatenated_args({"messages": [_msg_with(joined)]})
        assert out is not None
        assert len(out["messages"][0].tool_calls) == 2


class TestWellFormedTurnsAreUntouched:
    def test_a_normal_reply_is_left_alone(self):
        assert _split_concatenated_args(
            {"messages": [AIMessage(content="Here are the hotels", id="a")]}) is None

    def test_a_single_valid_tool_call_is_left_alone(self):
        m = AIMessage(content="", id="a",
                      tool_calls=[{"name": "search_tours",
                                   "args": {"destination_city": "Dubai"}, "id": "c1"}])
        assert _split_concatenated_args({"messages": [m]}) is None

    def test_unparseable_junk_is_left_for_the_normal_error_path(self):
        # Only cleanly separable objects are recovered; anything else must not
        # be silently reshaped into a call the model never made.
        assert _split_concatenated_args({"messages": [_msg_with("not json at all")]}) is None

    def test_a_human_message_is_ignored(self):
        assert _split_concatenated_args({"messages": [HumanMessage(content="hi")]}) is None

    def test_empty_state_is_safe(self):
        assert _split_concatenated_args({"messages": []}) is None
        assert _split_concatenated_args({}) is None


class TestProviderShortlistIsPinned:
    """With OPENROUTER_PROVIDERS empty, OpenRouter routed us onto hosts that
    return null content and the customer saw a blank message."""

    def test_broken_hosts_are_not_in_the_shortlist(self):
        from settings import get_llm_settings

        hosts = {h.strip() for h in
                 (get_llm_settings().openrouter_providers or "").split(",") if h.strip()}
        assert hosts, "OPENROUTER_PROVIDERS is empty — routing is unconstrained"
        # Verified null-content on 4 of 4 calls with the real tool payload.
        assert not hosts & {"NextBit", "Parasail", "SiliconFlow"}


class TestTheLlmClientCannotHangForever:
    """One turn sat for 10,766s (~3 hours) and then failed, and every request
    after it failed in ~1.5s on the poisoned connection pool.

    `timeout=120` was set on the LangChain wrapper, but the underlying OpenAI
    client still reported timeout=None, so nothing enforced it."""

    def test_timeout_reaches_the_real_client(self):
        from llm import build_llm

        m = build_llm()
        root = getattr(m, "root_client", None)
        assert root is not None
        assert root.timeout, "underlying client has no timeout — a stall hangs forever"

    def test_retries_are_configured(self):
        # A dropped connection killed the turn outright; 19 dropped in one run.
        from llm import build_llm

        assert (build_llm().max_retries or 0) >= 1
