# AGENTS.md additions for OpenTelemetry (feat/otel-observability)

Facts to merge into AGENTS.md when this branch lands (the lead edits AGENTS.md).

## §3 Repository layout

- Add: `observability.py   OpenTelemetry setup + helpers (spans, metrics, log bridge); off unless OTEL_EXPORTER_OTLP_ENDPOINT is set`.
- Architecture boundaries: `observability.py` imports only the standard library and
  OpenTelemetry (LangChain's callback base lazily). `booking_api/`, `mcp_tools/`,
  `llm.py`, `agent*.py` and `api/` may import it; it imports none of them.

## §7 Chat API

- `api/main.py` calls `observability.init_telemetry("gujjutours-chat-api")` and
  `observability.instrument_fastapi(app)` at module level (the middleware and the
  psycopg hook must exist before the first request / connection), and
  `observability.bridge_logging()` in `lifespan` right after `configure_logging`
  (attaching earlier would make `logging.basicConfig` a no-op and silence the console).
- A turn is wrapped in `observability.turn_span(...)` inside `_stream_turn.run()`;
  `_produce` runs via `observability.bind_context(_produce)` so the worker thread
  joins the turn's trace; `annotate_turn(tools=..., reply=...)` after the turn;
  `turn_failed(e)` in `_produce`'s except.
- `/health` and `/health/db` are excluded from server spans. ASGI `send`/`receive`
  spans are off (an SSE turn sends hundreds of chunks).
- Incoming `traceparent` is honoured, so a turn from the web is one trace.

## §4 Tools / §6 ui_payload

- `ui_payload.wrap_tool` runs each tool through `observability.traced_tool(name, ...)`:
  span `agent.tool <name>` with `agent.tool.result_count`, `agent.tool.outcome`
  (ok / error / exception), metrics `agent.tool.calls`, `agent.tool.duration`.

## booking_api

- `BookingApiClient.request` is now a wrapper around `_request(..., call=...)` inside
  `observability.supplier_span(method, path, host)`: one span per supplier call
  (retries included) with `supplier.endpoint`, `http.response.status_code`,
  `supplier.error_code` (from the body, e.g. `NOCOUNT_ERROR`: hotel availability answers
  HTTP 200 with `error.code` NOCOUNT_ERROR when a chunk has no rooms), `supplier.body_status`,
  `supplier.attempts`. Metric `supplier.errors{endpoint,code}`. Headers are never recorded.
  Tests that monkeypatch `BookingApiClient.request` still work.

## llm.py

- Every chat model gets `callbacks=observability.llm_callbacks("<provider>")`
  (None while off): span `llm.chat <model>` with `gen_ai.*` model, finish reason and
  token usage (OpenRouter returns usage on the streamed final chunk), errors with
  `http.response.status_code` (401 when the key is revoked), metrics `llm.calls`,
  `llm.duration`, `llm.tokens`, `llm.errors`. Streamed chunks concatenate strings, so
  `_unrepeat` turns "stopstop" back into "stop".

## Threading

- Supplier fan-outs use `ThreadPoolExecutor` in many tools; the
  `opentelemetry-instrumentation-threading` package (enabled with the rest) carries the
  OTel context into pool threads so those spans stay under their tool. It carries only
  the OpenTelemetry context, not the `rules` ContextVars.

## §11 Testing

- `tests/test_observability.py` (12 tests): disabled = no-op in a fresh interpreter;
  enabled with in-memory span/metric/log exporters (`init_telemetry(span_exporter=...,
  metric_reader=..., log_exporter=..., instrument_libraries=False)`); supplier span has
  endpoint/status/NOCOUNT_ERROR and no Authorization; tool, turn and LLM spans; content
  not captured by default. The global tracer provider can be set once per process, so
  the enabled tests share one module fixture.
- Web: `web/src/lib/telemetry.test.ts`.

## §12 Environment variables

- Add **Telemetry (chat-api and web):** `OTEL_EXPORTER_OTLP_ENDPOINT` (unset = off),
  `OTEL_EXPORTER_OTLP_HEADERS` (secret; Python needs URL-encoded values, `%20` for a
  space), `OTEL_SERVICE_NAME`, `OTEL_CAPTURE_CONTENT` (chat-api; default off),
  `OTEL_TRACES_SAMPLER` / `_ARG`, `OTEL_METRIC_EXPORT_INTERVAL`, `OTEL_LOGS_LEVEL`,
  `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS`, `OTEL_SDK_DISABLED`. Setup per backend:
  `docs/OBSERVABILITY.md`.

## §9 Web

- `web/src/instrumentation.ts` (in `src/`, because the app uses the `src/` layout; Next
  ignores a root `instrumentation.ts` then): registers `@vercel/otel` only when
  `NEXT_RUNTIME === "nodejs"` and an OTLP endpoint is set; service `gujjutours-web`;
  fetch propagates `traceparent` only to the `API_PROXY_TARGET` origin.
  `src/app/chat/stream/route.ts` also injects `traceparent` explicitly
  (`propagation.inject`, a no-op without a registered SDK).
- Deps (exact): `@vercel/otel` 2.1.3, `@opentelemetry/api` 1.9.1, `api-logs` 0.223.0,
  `sdk-logs` 0.223.0, `instrumentation` 0.223.0, `resources` 2.12.0, `sdk-metrics` 2.12.0,
  `sdk-trace-base` 2.12.0 (npm warns it is deprecated in favour of `sdk-trace`; it is
  the peer `@vercel/otel` 2.1.3 asks for).
- Gotcha seen while testing: `next.config.ts` rewrites are resolved at BUILD time, so
  `API_PROXY_TARGET` for the JSON endpoints must be set when running `npm run build`
  (the SSE route reads it at runtime). On Windows `npm start` fails because the script
  uses `${PORT:-3000}`; run `npx next start -p <port>` locally.

## §16 Gotchas

- Python deps (exact, in requirements.txt): `opentelemetry-api/sdk/exporter-otlp-proto-http`
  1.39.1, `opentelemetry-instrumentation(-fastapi/-requests/-httpx/-psycopg/-threading)` 0.60b1.
  1.39.1 was already in the venv (pulled in by other packages); keep SDK and
  instrumentation versions in step (1.39.x ↔ 0.60bx).
- With psycopg instrumented, each turn has ~40 `INSERT` spans from the LangGraph
  Postgres checkpointer; drop them with `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS=psycopg`
  if they cost too much at the backend.
- The OTLP log handler's `flush` starts a thread; at interpreter exit that raises, so
  `bridge_logging` uses a subclass that swallows the RuntimeError.
