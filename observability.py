"""OpenTelemetry for the chat API, the agent, its tools and the supplier client.

Off unless an OTLP endpoint is configured (``OTEL_EXPORTER_OTLP_ENDPOINT`` or
``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT``). While off, every helper here is a cheap
no-op: no providers are installed, nothing is exported, nothing raises.

When on, it installs a tracer, meter and logger provider exporting over OTLP/HTTP
to whatever backend the standard ``OTEL_*`` env vars point at (Grafana Cloud,
Honeycomb, a self-hosted collector), auto-instruments FastAPI, ``requests``
(supplier calls), ``httpx`` (the OpenRouter / OpenAI client) and ``psycopg``, and
gives the code a few helpers for the spans and metrics only it can describe:
a chat turn, a tool call, a supplier call, an LLM call.

Privacy: message text, tool arguments and LLM prompts/completions are recorded
only when ``OTEL_CAPTURE_CONTENT=true``. Request headers (the supplier bearer
tokens, the session cookie) are never recorded.

Layering: imports only the standard library and OpenTelemetry (plus LangChain's
callback base class, lazily). ``booking_api``, ``mcp_tools``, ``llm``, ``agent``
and ``api`` may import this module; it imports none of them.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_SERVICE_NAME = "gujjutours-chat-api"
_SCOPE = "gujjutours"
_MAX_CONTENT = 4000

_LOCK = threading.Lock()
_ENABLED = False
_INITIALISED = False
_LIBS_INSTRUMENTED = False
_LOG_HANDLER: logging.Handler | None = None

_tracer: Any = None
_meter: Any = None
_providers: dict[str, Any] = {}
_instruments: dict[str, Any] = {}

# Key under which the turn's recorder rides the OpenTelemetry context, so the
# worker thread running the agent can annotate the turn it belongs to.
_TURN_KEY = "gt.chat.turn"


# ---------------------------------------------------------------------- config
def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _endpoint_configured(signal: str | None = None) -> bool:
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip():
        return True
    if signal:
        return bool(os.environ.get(f"OTEL_EXPORTER_OTLP_{signal}_ENDPOINT", "").strip())
    return False


def telemetry_configured() -> bool:
    """True when the environment asks for telemetry (an OTLP endpoint is set)."""
    if _truthy(os.environ.get("OTEL_SDK_DISABLED")):
        return False
    return _endpoint_configured("TRACES")


def enabled() -> bool:
    """True once `init_telemetry` has installed the providers."""
    return _ENABLED


def capture_content() -> bool:
    """Whether message text, tool arguments and prompts may be recorded.

    Off by default (`OTEL_CAPTURE_CONTENT` unset). Read per call so it can be
    flipped without restarting tests.
    """
    return _truthy(os.environ.get("OTEL_CAPTURE_CONTENT"))


def _resource_attributes(service_name: str) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "service.name": os.environ.get("OTEL_SERVICE_NAME", "").strip() or service_name,
        "service.namespace": "gujjutours",
    }
    version = (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or "").strip()
    if version:
        attrs["service.version"] = version[:12]
    env = (
        os.environ.get("RAILWAY_ENVIRONMENT_NAME")
        or os.environ.get("ENV")
        or os.environ.get("ENVIRONMENT")
        or ""
    ).strip()
    if env:
        attrs["deployment.environment"] = env
        attrs["deployment.environment.name"] = env
    return attrs


# ------------------------------------------------------------------------ init
def init_telemetry(
    service_name: str = DEFAULT_SERVICE_NAME,
    *,
    instrument_libraries: bool = True,
    span_exporter: Any = None,
    metric_reader: Any = None,
    log_exporter: Any = None,
) -> bool:
    """Install tracing, metrics and logs once. Returns whether telemetry is on.

    Idempotent and never raises: a broken exporter config must not stop the
    API from serving. The exporter / reader arguments exist for tests (an
    in-memory exporter); given one, telemetry is turned on regardless of env.
    """
    global _ENABLED, _INITIALISED, _tracer, _meter
    with _LOCK:
        if _INITIALISED:
            return _ENABLED
        _INITIALISED = True
        testing = span_exporter is not None
        if not testing and not telemetry_configured():
            return False
        try:
            from opentelemetry import metrics, trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import (
                BatchSpanProcessor,
                SimpleSpanProcessor,
            )

            resource = Resource.create(_resource_attributes(service_name))

            # The sampler comes from OTEL_TRACES_SAMPLER / _ARG when set.
            tracer_provider = TracerProvider(resource=resource)
            if testing:
                tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
            else:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            trace.set_tracer_provider(tracer_provider)
            _providers["trace"] = tracer_provider
            _tracer = tracer_provider.get_tracer(_SCOPE)

            from opentelemetry.sdk.metrics import MeterProvider

            readers: list[Any] = []
            if metric_reader is not None:
                readers.append(metric_reader)
            elif not testing and _endpoint_configured("METRICS"):
                from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                    OTLPMetricExporter,
                )
                from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

                readers.append(PeriodicExportingMetricReader(OTLPMetricExporter()))
            meter_provider = MeterProvider(resource=resource, metric_readers=readers)
            metrics.set_meter_provider(meter_provider)
            _providers["metrics"] = meter_provider
            _meter = meter_provider.get_meter(_SCOPE)
            _create_instruments()

            if log_exporter is not None or (not testing and _endpoint_configured("LOGS")):
                from opentelemetry._logs import set_logger_provider
                from opentelemetry.sdk._logs import LoggerProvider
                from opentelemetry.sdk._logs.export import (
                    BatchLogRecordProcessor,
                    SimpleLogRecordProcessor,
                )

                logger_provider = LoggerProvider(resource=resource)
                if log_exporter is not None:
                    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(log_exporter))
                else:
                    from opentelemetry.exporter.otlp.proto.http._log_exporter import (
                        OTLPLogExporter,
                    )

                    logger_provider.add_log_record_processor(
                        BatchLogRecordProcessor(OTLPLogExporter())
                    )
                set_logger_provider(logger_provider)
                _providers["logs"] = logger_provider

            _ENABLED = True
        except Exception as e:
            log.warning("telemetry_init_failed: %s", e)
            _ENABLED = False
            return False

    if instrument_libraries:
        _instrument_libraries()
    log.info("telemetry_enabled service=%s", _resource_attributes(service_name)["service.name"])
    return True


def _instrument_libraries() -> None:
    """threading, requests (supplier), httpx (OpenRouter via the openai client), psycopg."""
    global _LIBS_INSTRUMENTED
    if _LIBS_INSTRUMENTED or not _ENABLED:
        return
    _LIBS_INSTRUMENTED = True
    # Standard switch, e.g. "psycopg" to drop the per-statement DB spans.
    disabled = {
        x.strip().lower()
        for x in os.environ.get("OTEL_PYTHON_DISABLED_INSTRUMENTATIONS", "").split(",")
        if x.strip()
    }
    for name, factory in (
        ("threading", _threading_instrumentor),
        ("requests", _requests_instrumentor),
        ("httpx", _httpx_instrumentor),
        ("psycopg", _psycopg_instrumentor),
    ):
        if name in disabled:
            continue
        try:
            factory()
        except Exception as e:
            log.warning("telemetry_instrument_failed lib=%s: %s", name, e)


def _threading_instrumentor() -> None:
    from opentelemetry.instrumentation.threading import ThreadingInstrumentor

    # The tools fan supplier calls out over ThreadPoolExecutors (hotel
    # availability in 200-id chunks, flight providers, tour slots). Without
    # this their spans start new traces instead of sitting under the tool.
    # Carries only the OpenTelemetry context, not other ContextVars.
    ThreadingInstrumentor().instrument()


def _requests_instrumentor() -> None:
    from opentelemetry.instrumentation.requests import RequestsInstrumentor

    RequestsInstrumentor().instrument()


def _httpx_instrumentor() -> None:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument()


def _psycopg_instrumentor() -> None:
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

    # No sqlcommenter: it rewrites every statement, and LangGraph's
    # checkpointer issues many. Parameters are never captured (customer data).
    PsycopgInstrumentor().instrument(enable_commenter=False, capture_parameters=False)


def instrument_fastapi(app: Any) -> None:
    """Server spans for the API, `traceparent` honoured. /health is skipped.

    The per-chunk ASGI `send`/`receive` spans are left out: an SSE turn sends
    hundreds of chunks and they would bury the useful spans.
    """
    if (
        not _ENABLED
        or "fastapi" in os.environ.get("OTEL_PYTHON_DISABLED_INSTRUMENTATIONS", "").lower()
    ):
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls=os.environ.get("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS")
            or r"^/health$,^/health/db$",
            exclude_spans=["receive", "send"],
        )
    except Exception as e:
        log.warning("telemetry_instrument_failed lib=fastapi: %s", e)


class _DropOwnLogs(logging.Filter):
    """The exporter's own complaints must not be exported (feedback loop)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith(("opentelemetry", "urllib3.connectionpool"))


def bridge_logging(level: int | str | None = None) -> None:
    """Send Python `logging` records to OTLP logs, keeping the console handlers.

    Call AFTER the app's own logging setup: `logging.basicConfig` does nothing
    once the root logger has a handler, so attaching this first would silence
    the console. Level from `OTEL_LOGS_LEVEL` (default INFO).
    """
    global _LOG_HANDLER
    provider = _providers.get("logs")
    if not _ENABLED or provider is None or _LOG_HANDLER is not None:
        return
    try:
        from opentelemetry.sdk._logs import LoggingHandler

        class _Handler(LoggingHandler):
            def flush(self) -> None:
                # The SDK flushes on a new thread, which logging's own
                # shutdown hook cannot start at interpreter exit.
                with contextlib.suppress(RuntimeError):
                    super().flush()

        lvl = level or os.environ.get("OTEL_LOGS_LEVEL", "INFO")
        handler = _Handler(
            level=logging.getLevelName(str(lvl).upper()) if isinstance(lvl, str) else lvl,
            logger_provider=provider,
        )
        handler.addFilter(_DropOwnLogs())
        logging.getLogger().addHandler(handler)
        _LOG_HANDLER = handler
    except Exception as e:
        log.warning("telemetry_logging_bridge_failed: %s", e)


def shutdown() -> None:
    """Flush and stop the providers (also done at interpreter exit)."""
    for key in ("trace", "metrics", "logs"):
        p = _providers.get(key)
        if p is not None:
            with contextlib.suppress(Exception):
                p.shutdown()


def force_flush() -> None:
    for key in ("trace", "metrics", "logs"):
        p = _providers.get(key)
        if p is not None and hasattr(p, "force_flush"):
            with contextlib.suppress(Exception):
                p.force_flush()


# --------------------------------------------------------------------- metrics
def _create_instruments() -> None:
    m = _meter
    _instruments.update(
        {
            "turns": m.create_counter(
                "chat.turns", unit="{turn}", description="Chat turns by mode and outcome"
            ),
            "turn_duration": m.create_histogram(
                "chat.turn.duration", unit="s", description="Chat turn wall time"
            ),
            "tool_calls": m.create_counter(
                "agent.tool.calls",
                unit="{call}",
                description="Agent tool calls by tool and outcome",
            ),
            "tool_duration": m.create_histogram(
                "agent.tool.duration", unit="s", description="Agent tool call duration"
            ),
            "supplier_calls": m.create_counter(
                "supplier.calls",
                unit="{call}",
                description="Supplier API calls by endpoint and status",
            ),
            "supplier_errors": m.create_counter(
                "supplier.errors",
                unit="{error}",
                description="Supplier API errors by endpoint and code",
            ),
            "supplier_duration": m.create_histogram(
                "supplier.duration", unit="s", description="Supplier API call duration"
            ),
            "llm_calls": m.create_counter(
                "llm.calls", unit="{call}", description="LLM calls by model"
            ),
            "llm_errors": m.create_counter(
                "llm.errors", unit="{error}", description="LLM call errors by model and status"
            ),
            "llm_duration": m.create_histogram(
                "llm.duration", unit="s", description="LLM call duration"
            ),
            "llm_tokens": m.create_counter(
                "llm.tokens", unit="{token}", description="LLM tokens by model and type"
            ),
        }
    )


def add(name: str, value: float = 1, **attrs: Any) -> None:
    """Add to a counter by its short name. No-op while telemetry is off."""
    inst = _instruments.get(name) if _ENABLED else None
    if inst is not None:
        with contextlib.suppress(Exception):
            inst.add(value, _clean(attrs))


def record(name: str, value: float, **attrs: Any) -> None:
    """Record a histogram value by its short name. No-op while telemetry is off."""
    inst = _instruments.get(name) if _ENABLED else None
    if inst is not None:
        with contextlib.suppress(Exception):
            inst.record(value, _clean(attrs))


# ----------------------------------------------------------------------- spans
def _clean(attrs: dict[str, Any]) -> dict[str, Any]:
    """OpenTelemetry accepts str/bool/int/float and homogeneous lists of them."""
    out: dict[str, Any] = {}
    for k, v in attrs.items():
        if v is None:
            continue
        if isinstance(v, (str, bool, int, float)):
            out[k] = v
        elif isinstance(v, (list, tuple)):
            out[k] = [str(x) for x in v]
        else:
            out[k] = str(v)
    return out


def content(value: Any) -> str | None:
    """`value` as text for a span attribute, or None unless content capture is on."""
    if not capture_content() or value is None:
        return None
    text = value if isinstance(value, str) else repr(value)
    return text[:_MAX_CONTENT]


class _NoopSpan:
    """Stands in for a span while telemetry is off."""

    def set_attribute(self, *_a: Any, **_k: Any) -> None:
        return None

    def set_attributes(self, *_a: Any, **_k: Any) -> None:
        return None

    def record_exception(self, *_a: Any, **_k: Any) -> None:
        return None

    def set_status(self, *_a: Any, **_k: Any) -> None:
        return None

    def add_event(self, *_a: Any, **_k: Any) -> None:
        return None

    def is_recording(self) -> bool:
        return False


_NOOP = _NoopSpan()


@contextmanager
def span(name: str, *, kind: str = "internal", **attrs: Any) -> Iterator[Any]:
    """A span as a context manager; yields a no-op object while telemetry is off.

    Exceptions are recorded and re-raised.
    """
    if not _ENABLED or _tracer is None:
        yield _NOOP
        return
    from opentelemetry.trace import SpanKind

    sk = {"client": SpanKind.CLIENT, "server": SpanKind.SERVER}.get(kind, SpanKind.INTERNAL)
    with _tracer.start_as_current_span(name, kind=sk, attributes=_clean(attrs)) as s:
        yield s


def set_attributes(**attrs: Any) -> None:
    """Set attributes on the current span (no-op while off)."""
    if not _ENABLED:
        return
    from opentelemetry import trace

    trace.get_current_span().set_attributes(_clean(attrs))


def mark_error(s: Any, exc: BaseException | None = None, description: str | None = None) -> None:
    if not _ENABLED or s is None or s is _NOOP:
        return
    from opentelemetry.trace import Status, StatusCode

    if exc is not None:
        s.record_exception(exc)
        s.set_attribute("error.type", type(exc).__name__)
    s.set_status(Status(StatusCode.ERROR, description or (str(exc)[:200] if exc else None)))


def bind_context[T](fn: Callable[..., T]) -> Callable[..., T]:
    """Wrap `fn` to run inside the CURRENT trace context, from any thread.

    `loop.run_in_executor` does not carry context variables into the worker
    thread, so spans made there would start new traces. Only the
    OpenTelemetry context is carried, not every ContextVar.
    """
    if not _ENABLED:
        return fn
    from opentelemetry import context as otel_context

    ctx = otel_context.get_current()

    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> T:
        token = otel_context.attach(ctx)
        try:
            return fn(*args, **kwargs)
        finally:
            otel_context.detach(token)

    return run


# ------------------------------------------------------------------ chat turns
class _Turn:
    __slots__ = ("error_type", "outcome", "span")

    def __init__(self, s: Any) -> None:
        self.span = s
        self.outcome = "ok"
        self.error_type: str | None = None


def _current_turn() -> _Turn | None:
    if not _ENABLED:
        return None
    from opentelemetry import context as otel_context

    turn = otel_context.get_value(_TURN_KEY)
    return turn if isinstance(turn, _Turn) else None


@contextmanager
def turn_span(*, session_id: str, mode: str, regenerate: bool, message: str = "") -> Iterator[None]:
    """One span and the turn metrics for a chat turn (`chat.turn`)."""
    if not _ENABLED or _tracer is None:
        yield None
        return
    from opentelemetry import context as otel_context

    start = time.perf_counter()
    with _tracer.start_as_current_span(
        "chat.turn",
        attributes=_clean(
            {
                "chat.session_id": session_id,
                "chat.mode": mode,
                "chat.regenerate": bool(regenerate),
                "chat.message_chars": len(message or ""),
                "chat.message": content(message),
            }
        ),
        record_exception=False,
        set_status_on_exception=False,
    ) as s:
        turn = _Turn(s)
        token = otel_context.attach(otel_context.set_value(_TURN_KEY, turn))
        try:
            yield None
        except BaseException as e:
            if e.__class__.__name__ == "CancelledError":
                turn.outcome = "cancelled"
            else:
                turn.outcome = "error"
                mark_error(s, e)
            raise
        finally:
            otel_context.detach(token)
            elapsed = time.perf_counter() - start
            s.set_attribute("chat.outcome", turn.outcome)
            s.set_attribute("chat.duration_ms", round(elapsed * 1000, 1))
            attrs = {"chat.mode": mode, "outcome": turn.outcome}
            add("turns", 1, **attrs)
            record("turn_duration", elapsed, **attrs)


def annotate_turn(*, tools: list[str] | None = None, reply: str | None = None) -> None:
    """Record what the turn did on its span (tools used, reply length)."""
    turn = _current_turn()
    if turn is None:
        return
    names = [t for t in (tools or []) if t]
    turn.span.set_attributes(
        _clean(
            {
                "chat.tools": sorted(set(names)),
                "chat.tool_count": len(names),
                "chat.reply_chars": len(reply or "") if reply is not None else None,
                "chat.reply": content(reply),
            }
        )
    )


def turn_failed(exc: BaseException) -> None:
    """The turn hit an error it handled (the customer saw an apology)."""
    turn = _current_turn()
    if turn is None:
        return
    turn.outcome = "error"
    mark_error(turn.span, exc)


# ------------------------------------------------------------------ tool calls
def result_count(result: Any) -> int | None:
    if isinstance(result, dict):
        for key in ("options", "results", "items", "days"):
            if isinstance(result.get(key), list):
                return len(result[key])
    if isinstance(result, list):
        return len(result)
    return None


def _result_error(result: Any) -> str | None:
    if isinstance(result, dict) and result.get("error"):
        err = result.get("error")
        if isinstance(err, str) and len(err) <= 64 and " " not in err:
            return err
        return str(result.get("error_type") or result.get("code") or "tool_error")[:64]
    return None


def traced_tool(
    name: str, func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    """Run a tool function under an `agent.tool {name}` span plus metrics."""
    if not _ENABLED or _tracer is None:
        return func(*args, **kwargs)
    start = time.perf_counter()
    outcome = "ok"
    with _tracer.start_as_current_span(
        f"agent.tool {name}",
        attributes=_clean({"agent.tool.name": name, "agent.tool.args": content(kwargs or args)}),
        record_exception=False,
        set_status_on_exception=False,
    ) as s:
        try:
            result = func(*args, **kwargs)
        except BaseException as e:
            outcome = "exception"
            mark_error(s, e)
            raise
        else:
            count = result_count(result)
            err = _result_error(result)
            if err:
                outcome = "error"
                s.set_attribute("agent.tool.error", err)
            if count is not None:
                s.set_attribute("agent.tool.result_count", count)
            return result
        finally:
            elapsed = time.perf_counter() - start
            s.set_attribute("agent.tool.outcome", outcome)
            add("tool_calls", 1, tool=name, outcome=outcome)
            record("tool_duration", elapsed, tool=name, outcome=outcome)


# --------------------------------------------------------------- supplier calls
_SAFE_CODE_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-.")


def _as_code(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value))
    if isinstance(value, str):
        v = value.strip()
        if 0 < len(v) <= 64 and set(v) <= _SAFE_CODE_CHARS:
            return v
    return None


def supplier_error_code(body: Any) -> str | None:
    """The supplier's own error code from a response body (e.g. NOCOUNT_ERROR).

    Bodies vary by endpoint: `{"error": {"code": ..}}`, `{"error": [{"code": ..}]}`,
    `{"errors": [...]}`, `{"errorCode": ..}`. Only short code-like strings are
    returned; free text never is (it can echo request data).
    """
    if not isinstance(body, dict):
        return None
    for key in ("errorCode", "ErrorCode", "error_code"):
        code = _as_code(body.get(key))
        if code:
            return code
    for key in ("error", "Error", "errors", "Errors"):
        err = body.get(key)
        items = err if isinstance(err, list) else [err]
        for item in items:
            if isinstance(item, dict):
                code = _as_code(item.get("code") or item.get("Code") or item.get("errorCode"))
                if code:
                    return code
            else:
                code = _as_code(item)
                if code and code.isupper():
                    return code
    return None


class SupplierCall:
    """What `supplier_span` records about one supplier request (all attempts)."""

    __slots__ = ("attempts", "body_status", "error_code", "error_type", "span", "status")

    def __init__(self, s: Any) -> None:
        self.span = s
        self.status: int | None = None
        self.error_code: str | None = None
        self.error_type: str | None = None
        self.attempts = 0
        self.body_status: int | None = None

    def response(self, status: int, body: Any = None) -> None:
        self.status = status
        code = supplier_error_code(body)
        if code:
            self.error_code = code
        if isinstance(body, dict) and isinstance(body.get("statusCode"), int):
            self.body_status = body["statusCode"]


_NOOP_CALL = SupplierCall(_NOOP)


def _endpoint_name(path: str) -> str:
    """The path without a query string, numbers collapsed so it groups well."""
    p = path.split("?", 1)[0]
    parts = ["{id}" if seg.isdigit() else seg for seg in p.split("/")]
    return "/".join(parts) or "/"


@contextmanager
def supplier_span(method: str, path: str, host: str = "") -> Iterator[SupplierCall]:
    """Span + metrics around one supplier request, including its retries.

    Records the endpoint path, HTTP status, the supplier's error code and the
    duration. Never headers, never bodies (unless content capture is on, and
    even then only the request body, which carries no credentials).
    """
    if not _ENABLED or _tracer is None:
        yield _NOOP_CALL
        return
    endpoint = _endpoint_name(path)
    start = time.perf_counter()
    with _tracer.start_as_current_span(
        f"supplier {method} {endpoint}",
        kind=_client_kind(),
        attributes=_clean(
            {
                "supplier.endpoint": endpoint,
                "http.request.method": method,
                "server.address": host or None,
            }
        ),
        record_exception=False,
        set_status_on_exception=False,
    ) as s:
        call = SupplierCall(s)
        try:
            yield call
        except BaseException as e:
            call.error_type = type(e).__name__
            mark_error(s, e)
            raise
        finally:
            elapsed = time.perf_counter() - start
            status = call.status
            attrs = _clean(
                {
                    "http.response.status_code": status,
                    "supplier.error_code": call.error_code,
                    "supplier.body_status": call.body_status,
                    "supplier.attempts": call.attempts or None,
                    "error.type": call.error_type,
                }
            )
            s.set_attributes(attrs)
            failed = bool(
                call.error_type
                or call.error_code
                or (status is not None and status >= 400)
                or (call.body_status is not None and call.body_status >= 400)
            )
            status_label = str(status) if status is not None else "none"
            add("supplier_calls", 1, endpoint=endpoint, status=status_label)
            record("supplier_duration", elapsed, endpoint=endpoint, status=status_label)
            if failed:
                code = (
                    call.error_code
                    or call.error_type
                    or (
                        str(call.body_status)
                        if call.body_status and call.body_status >= 400
                        else status_label
                    )
                )
                add("supplier_errors", 1, endpoint=endpoint, code=code)
                if not call.error_type and status is not None and status >= 400:
                    mark_error(s, None, f"HTTP {status}")


def _client_kind() -> Any:
    from opentelemetry.trace import SpanKind

    return SpanKind.CLIENT


# -------------------------------------------------------------------- LLM calls
def llm_callbacks(provider: str) -> list[Any] | None:
    """LangChain callbacks that put a span around every model call, or None.

    Pass as `callbacks=` to the chat model. None while telemetry is off, so the
    model is built exactly as before.
    """
    if not _ENABLED:
        return None
    try:
        return [_make_llm_handler(provider)]
    except Exception as e:
        log.warning("telemetry_llm_callback_failed: %s", e)
        return None


def _make_llm_handler(provider: str) -> Any:
    from langchain_core.callbacks import BaseCallbackHandler

    class _LlmSpans(BaseCallbackHandler):
        """`llm.chat {model}` spans with model, provider, tokens and latency."""

        run_inline = True  # stay in the caller's thread and trace context
        raise_error = False

        def __init__(self) -> None:
            self._open: dict[Any, tuple[Any, float, str]] = {}
            self._lock = threading.Lock()

        def _start(self, run_id: Any, model: str, prompt: Any) -> None:
            s = _tracer.start_span(
                f"llm.chat {model}",
                kind=_client_kind(),
                attributes=_clean(
                    {
                        "gen_ai.operation.name": "chat",
                        "gen_ai.system": provider,
                        "gen_ai.provider.name": provider,
                        "gen_ai.request.model": model,
                        "gen_ai.prompt": content(prompt),
                    }
                ),
            )
            with self._lock:
                self._open[run_id] = (s, time.perf_counter(), model)

        def _pop(self, run_id: Any) -> tuple[Any, float, str] | None:
            with self._lock:
                return self._open.pop(run_id, None)

        def on_chat_model_start(
            self, serialized: Any, messages: Any, *, run_id: Any, **kwargs: Any
        ) -> None:
            params = kwargs.get("invocation_params") or {}
            meta = kwargs.get("metadata") or {}
            model = str(
                params.get("model")
                or params.get("model_name")
                or meta.get("ls_model_name")
                or "unknown"
            )
            last = None
            try:
                last = messages[-1][-1].content if messages and messages[-1] else None
            except Exception:
                last = None
            self._start(run_id, model, last)

        def on_llm_start(
            self, serialized: Any, prompts: Any, *, run_id: Any, **kwargs: Any
        ) -> None:
            params = kwargs.get("invocation_params") or {}
            model = str(params.get("model") or params.get("model_name") or "unknown")
            self._start(run_id, model, prompts[-1] if prompts else None)

        def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
            opened = self._pop(run_id)
            if opened is None:
                return
            s, start, model = opened
            elapsed = time.perf_counter() - start
            usage, resp_model, finish, text = _llm_result_facts(response)
            attrs = {
                "gen_ai.response.model": resp_model,
                "gen_ai.response.finish_reasons": [finish] if finish else None,
                "gen_ai.usage.input_tokens": usage.get("input"),
                "gen_ai.usage.output_tokens": usage.get("output"),
                "gen_ai.usage.total_tokens": usage.get("total"),
                "gen_ai.completion": content(text),
            }
            s.set_attributes(_clean(attrs))
            s.end()
            add("llm_calls", 1, model=model, provider=provider, outcome="ok")
            record("llm_duration", elapsed, model=model, provider=provider, outcome="ok")
            for kind in ("input", "output"):
                if isinstance(usage.get(kind), int):
                    add("llm_tokens", usage[kind], model=model, provider=provider, type=kind)

        def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
            opened = self._pop(run_id)
            if opened is None:
                return
            s, start, model = opened
            elapsed = time.perf_counter() - start
            status = getattr(error, "status_code", None)
            s.set_attributes(_clean({"http.response.status_code": status}))
            mark_error(s, error)
            s.end()
            status_label = str(status) if status is not None else type(error).__name__
            add("llm_calls", 1, model=model, provider=provider, outcome="error")
            add("llm_errors", 1, model=model, provider=provider, status=status_label)
            record("llm_duration", elapsed, model=model, provider=provider, outcome="error")

    return _LlmSpans()


def _llm_result_facts(response: Any) -> tuple[dict[str, Any], str | None, str | None, str | None]:
    """Token usage, response model, finish reason and text from an LLMResult."""
    usage: dict[str, Any] = {}
    model = finish = text = None
    try:
        out = getattr(response, "llm_output", None) or {}
        tu = out.get("token_usage") or out.get("usage") or {}
        if tu:
            usage = {
                "input": tu.get("prompt_tokens") or tu.get("input_tokens"),
                "output": tu.get("completion_tokens") or tu.get("output_tokens"),
                "total": tu.get("total_tokens"),
            }
        model = out.get("model_name") or out.get("model")
        gens = response.generations[0] if response.generations else []
        if gens:
            g = gens[0]
            msg = getattr(g, "message", None)
            um = getattr(msg, "usage_metadata", None) or {}
            if um and not any(isinstance(v, int) for v in usage.values()):
                usage = {
                    "input": um.get("input_tokens"),
                    "output": um.get("output_tokens"),
                    "total": um.get("total_tokens"),
                }
            meta = getattr(msg, "response_metadata", None) or {}
            model = model or meta.get("model_name") or meta.get("model")
            finish = meta.get("finish_reason") or (getattr(g, "generation_info", None) or {}).get(
                "finish_reason"
            )
            text = getattr(g, "text", None) or (getattr(msg, "content", None) if msg else None)
    except Exception:
        pass
    usage = {k: v for k, v in usage.items() if isinstance(v, int)}
    return usage, _unrepeat(model), _unrepeat(finish), text


def _unrepeat(value: Any) -> str | None:
    """Streamed chunks are merged by string concatenation, so a model name or
    finish reason sent on two chunks arrives as "stopstop". Undo that."""
    if not isinstance(value, str) or not value:
        return value if isinstance(value, str) else None
    n = len(value)
    for size in range(1, n // 2 + 1):
        if n % size == 0 and value == value[:size] * (n // size):
            return value[:size]
    return value
