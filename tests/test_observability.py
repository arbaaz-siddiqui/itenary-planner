"""OpenTelemetry helpers: off by default and harmless; on, they record the
right spans without secrets or customer text.

The global tracer provider can be set once per process, so the enabled tests
share one in-memory setup (module fixture). The disabled behaviour is checked
in a fresh interpreter, where nothing has been initialised.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import observability

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ disabled
def test_disabled_is_a_noop_in_a_fresh_process() -> None:
    """No endpoint: init returns False, every helper runs and records nothing."""
    code = r"""
import os, json
for k in list(os.environ):
    if k.startswith("OTEL_"):
        del os.environ[k]
import observability as o
assert o.init_telemetry() is False
assert o.enabled() is False
assert o.init_telemetry() is False  # idempotent
with o.span("x", a=1) as s:
    s.set_attribute("b", 2)
with o.turn_span(session_id="s", mode="ask", regenerate=False, message="hi"):
    o.annotate_turn(tools=["search_hotels"], reply="r")
    o.turn_failed(RuntimeError("x"))
with o.supplier_span("POST", "/api/x") as call:
    call.response(200, {"ok": True})
assert o.traced_tool("t", lambda **k: {"options": [1]}, (), {"a": 1}) == {"options": [1]}
assert o.llm_callbacks("openrouter") is None
f = lambda: 5
assert o.bind_context(f) is f
o.add("turns", 1); o.record("turn_duration", 1.0)
o.instrument_fastapi(object()); o.bridge_logging()
from opentelemetry import trace
print(json.dumps({"provider": type(trace.get_tracer_provider()).__name__}))
"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr
    # The SDK provider was never installed.
    assert "ProxyTracerProvider" in out.stdout.strip().splitlines()[-1]


def test_capture_content_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_CAPTURE_CONTENT", raising=False)
    assert observability.capture_content() is False
    assert observability.content("secret text") is None
    monkeypatch.setenv("OTEL_CAPTURE_CONTENT", "true")
    assert observability.capture_content() is True
    assert observability.content("hello") == "hello"


def test_streamed_model_names_are_not_doubled() -> None:
    assert observability._unrepeat("tool_callstool_calls") == "tool_calls"
    assert observability._unrepeat("google/gemma-4-26b-a4b-itgoogle/gemma-4-26b-a4b-it") == (
        "google/gemma-4-26b-a4b-it"
    )
    assert observability._unrepeat("stop") == "stop"
    assert observability._unrepeat(None) is None


def test_supplier_error_code_shapes() -> None:
    f = observability.supplier_error_code
    assert f({"error": {"code": "NOCOUNT_ERROR", "description": "No hotels"}}) == "NOCOUNT_ERROR"
    assert f({"error": [{"code": "NOCOUNT_ERROR"}]}) == "NOCOUNT_ERROR"
    assert f({"errorCode": "E42"}) == "E42"
    assert f({"error": "NOCOUNT_ERROR"}) == "NOCOUNT_ERROR"
    # Free text is never taken as a code (it can echo request data).
    assert f({"error": "No result for guest Ramesh Patel"}) is None
    assert f({"result": []}) is None
    assert f("text") is None


# ------------------------------------------------------------------- enabled
@pytest.fixture(scope="module")
def otel() -> Any:
    """One in-memory telemetry setup for this module's enabled tests."""
    try:
        from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter as InMemoryLogExporter
    except ImportError:  # older SDKs
        from opentelemetry.sdk._logs.export import InMemoryLogExporter  # type: ignore[no-redef]
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    if observability.enabled():
        pytest.skip("telemetry already initialised in this process")
    spans = InMemorySpanExporter()
    reader = InMemoryMetricReader()
    logs = InMemoryLogExporter()
    # The idempotence guard is per process; this module owns the first init.
    observability._INITIALISED = False
    assert observability.init_telemetry(
        "gujjutours-test",
        instrument_libraries=False,
        span_exporter=spans,
        metric_reader=reader,
        log_exporter=logs,
    )
    yield {"spans": spans, "reader": reader, "logs": logs}


@pytest.fixture()
def spans(otel: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv("OTEL_CAPTURE_CONTENT", raising=False)
    otel["spans"].clear()
    return otel["spans"]


def _metric_points(reader: Any, name: str) -> list[Any]:
    data = reader.get_metrics_data()
    points: list[Any] = []
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == name:
                    points.extend(m.data.data_points)
    return points


class _FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> Any:
        return self._body


def test_supplier_span_has_endpoint_status_code_and_no_secrets(spans: Any, otel: Any) -> None:
    from booking_api.http_client import BookingApiClient

    client = BookingApiClient("https://supplier.example", token="tok", tenant_id="t", max_retries=0)
    seen: dict[str, Any] = {}

    def fake_request(**kw: Any) -> _FakeResponse:
        seen.update(kw)
        return _FakeResponse(200, {"statusCode": 404, "error": {"code": "NOCOUNT_ERROR"}})

    client.session.request = fake_request  # type: ignore[method-assign]
    body = client.post(
        "/api/hotel/availability",
        json={"cityId": 1},
        headers={"Authorization": "Bearer SUPERSECRET-TOKEN", "X-Trace-Id": "abc"},
    )
    assert body["error"]["code"] == "NOCOUNT_ERROR"
    assert seen["headers"]["Authorization"].endswith("SUPERSECRET-TOKEN")

    found = [s for s in spans.get_finished_spans() if s.name.startswith("supplier ")]
    assert len(found) == 1
    s = found[0]
    assert s.name == "supplier POST /api/hotel/availability"
    assert s.attributes["supplier.endpoint"] == "/api/hotel/availability"
    assert s.attributes["http.response.status_code"] == 200
    assert s.attributes["supplier.error_code"] == "NOCOUNT_ERROR"
    assert s.attributes["supplier.body_status"] == 404
    dumped = json.dumps({k: str(v) for k, v in s.attributes.items()})
    assert "SUPERSECRET" not in dumped and "Bearer" not in dumped
    assert not any("authorization" in k.lower() for k in s.attributes)

    errors = _metric_points(otel["reader"], "supplier.errors")
    assert any(
        p.attributes.get("code") == "NOCOUNT_ERROR"
        and p.attributes.get("endpoint") == "/api/hotel/availability"
        for p in errors
    )


def test_supplier_span_marks_http_error(spans: Any) -> None:
    from booking_api.http_client import BookingApiClient
    from core import BookingApiUnauthorized

    client = BookingApiClient("https://supplier.example", token="tok", tenant_id="t", max_retries=0)
    client.session.request = lambda **kw: _FakeResponse(401, {"message": "bad token"})  # type: ignore[method-assign]
    with pytest.raises(BookingApiUnauthorized):
        client.get("/api/Currency/ROE/INR")
    (s,) = [x for x in spans.get_finished_spans() if x.name.startswith("supplier ")]
    assert s.attributes["http.response.status_code"] == 401
    assert s.attributes["error.type"] == "BookingApiUnauthorized"
    assert s.status.status_code.name == "ERROR"


def test_tool_span_counts_results_without_args(spans: Any, otel: Any) -> None:
    from langchain_core.tools import StructuredTool

    from mcp_tools.ui_payload import wrap_tool

    def search_hotels(city: str) -> dict[str, Any]:
        """Search hotels."""
        return {"options": [{"name": "A"}, {"name": "B"}]}

    tool = wrap_tool(StructuredTool.from_function(search_hotels, name="search_hotels"))
    out = tool.invoke({"city": "Ramesh secret city"})
    assert out["ui_ref"]

    (s,) = [x for x in spans.get_finished_spans() if x.name == "agent.tool search_hotels"]
    assert s.attributes["agent.tool.name"] == "search_hotels"
    assert s.attributes["agent.tool.result_count"] == 2
    assert s.attributes["agent.tool.outcome"] == "ok"
    assert "agent.tool.args" not in s.attributes
    calls = _metric_points(otel["reader"], "agent.tool.calls")
    assert any(p.attributes == {"tool": "search_hotels", "outcome": "ok"} for p in calls)


def test_tool_span_records_error_result_and_args_when_capture_on(
    spans: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_CAPTURE_CONTENT", "true")
    result = observability.traced_tool(
        "get_visa_info", lambda **k: {"error": "not_found", "message": "x"}, (), {"country": "UAE"}
    )
    assert result["error"] == "not_found"
    (s,) = spans.get_finished_spans()
    assert s.attributes["agent.tool.outcome"] == "error"
    assert s.attributes["agent.tool.error"] == "not_found"
    assert "UAE" in s.attributes["agent.tool.args"]


def test_turn_span_wraps_children_and_threads(spans: Any, otel: Any) -> None:
    import threading

    with observability.turn_span(
        session_id="sess-1", mode="plan", regenerate=False, message="3 nights for Ramesh"
    ):

        def work() -> None:
            observability.traced_tool("search_tours", lambda: {"options": [1]}, (), {})
            observability.annotate_turn(tools=["search_tours", "search_tours"], reply="done")

        bound = observability.bind_context(work)
        t = threading.Thread(target=bound)
        t.start()
        t.join()

    finished = {s.name: s for s in spans.get_finished_spans()}
    turn = finished["chat.turn"]
    tool = finished["agent.tool search_tours"]
    assert tool.parent is not None and tool.parent.span_id == turn.context.span_id
    assert tool.context.trace_id == turn.context.trace_id
    assert turn.attributes["chat.session_id"] == "sess-1"
    assert turn.attributes["chat.mode"] == "plan"
    assert turn.attributes["chat.tool_count"] == 2
    assert list(turn.attributes["chat.tools"]) == ["search_tours"]
    assert turn.attributes["chat.outcome"] == "ok"
    # No customer text by default.
    assert "chat.message" not in turn.attributes and "chat.reply" not in turn.attributes
    assert not any("Ramesh" in str(v) for s in finished.values() for v in s.attributes.values())
    turns = _metric_points(otel["reader"], "chat.turns")
    assert any(p.attributes == {"chat.mode": "plan", "outcome": "ok"} for p in turns)
    assert _metric_points(otel["reader"], "chat.turn.duration")


def test_turn_failed_marks_outcome(spans: Any) -> None:
    with observability.turn_span(session_id="s2", mode="ask", regenerate=True):
        observability.turn_failed(RuntimeError("boom"))
    (turn,) = [s for s in spans.get_finished_spans() if s.name == "chat.turn"]
    assert turn.attributes["chat.outcome"] == "error"
    assert turn.attributes["chat.regenerate"] is True
    assert turn.status.status_code.name == "ERROR"


def test_llm_callback_span_with_tokens_and_errors(spans: Any, otel: Any) -> None:
    import uuid

    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    (handler,) = observability.llm_callbacks("openrouter") or []
    rid = uuid.uuid4()
    handler.on_chat_model_start(
        {},
        [[HumanMessage(content="private question")]],
        run_id=rid,
        invocation_params={"model": "google/gemma-4-26b-a4b-it"},
    )
    msg = AIMessage(
        content="private answer",
        usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
        response_metadata={"model_name": "google/gemma-4-26b-a4b-it", "finish_reason": "stop"},
    )
    handler.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]), run_id=rid)

    class _Err(Exception):
        status_code = 401

    rid2 = uuid.uuid4()
    handler.on_chat_model_start(
        {}, [[HumanMessage(content="q")]], run_id=rid2, invocation_params={"model": "m2"}
    )
    handler.on_llm_error(_Err("Unauthorized"), run_id=rid2)

    finished = {s.name: s for s in spans.get_finished_spans()}
    ok = finished["llm.chat google/gemma-4-26b-a4b-it"]
    assert ok.attributes["gen_ai.system"] == "openrouter"
    assert ok.attributes["gen_ai.usage.input_tokens"] == 120
    assert ok.attributes["gen_ai.usage.output_tokens"] == 30
    assert "gen_ai.prompt" not in ok.attributes and "gen_ai.completion" not in ok.attributes
    bad = finished["llm.chat m2"]
    assert bad.attributes["http.response.status_code"] == 401
    assert bad.status.status_code.name == "ERROR"
    errs = _metric_points(otel["reader"], "llm.errors")
    assert any(p.attributes.get("status") == "401" for p in errs)


def test_logging_bridge_exports_records(otel: Any) -> None:
    import logging

    observability.bridge_logging()
    logging.getLogger("chat_api").warning("telemetry log check %s", 7)
    observability.force_flush()
    bodies = [str(r.log_record.body) for r in otel["logs"].get_finished_logs()]
    assert any("telemetry log check 7" in b for b in bodies)
    # Detach so the rest of the session logs normally.
    if observability._LOG_HANDLER is not None:
        logging.getLogger().removeHandler(observability._LOG_HANDLER)
