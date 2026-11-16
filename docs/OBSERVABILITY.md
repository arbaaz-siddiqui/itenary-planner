# Observability (OpenTelemetry)

The chat API (`gujjutours-chat-api`) and the web server (`gujjutours-web`) export
traces, metrics and logs over OTLP/HTTP to any OpenTelemetry backend. Nothing is
sent, and the code paths are plain function calls, until an OTLP endpoint is set.

- Python: `observability.py` (root module), wired in `api/main.py`,
  `booking_api/http_client.py`, `mcp_tools/ui_payload.py` and `llm.py`.
- Web: `web/src/instrumentation.ts` (Node runtime only, via `@vercel/otel`),
  `web/src/lib/telemetry.ts`, and `traceparent` injection in
  `web/src/app/chat/stream/route.ts`.

## Turning it on

Set these on the Railway services (names only here; values are secrets where
noted). Both services read the standard OpenTelemetry variables.

| Variable | chat-api | web | Notes |
|---|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | yes | yes | Base OTLP/HTTP URL, e.g. `https://otlp-gateway-prod-eu-west-2.grafana.net/otlp`. Unset = telemetry off. `/v1/traces`, `/v1/metrics`, `/v1/logs` are appended by the SDK. |
| `OTEL_EXPORTER_OTLP_HEADERS` | yes | yes | **Secret.** Auth header(s), `key=value,key2=value2`. Python needs URL-encoded values (a space is `%20`). |
| `OTEL_SERVICE_NAME` | optional | optional | Defaults `gujjutours-chat-api` / `gujjutours-web`. |
| `OTEL_CAPTURE_CONTENT` | optional | - | `true` records message text, reply text, tool arguments and LLM prompts/completions (truncated to 4,000 chars). Default off. Keep it off in production unless you are debugging with consent. |
| `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG` | optional | optional | e.g. `parentbased_traceidratio` and `0.25` to keep a quarter of turns. Default: every trace. Use the same setting on both services so a turn is kept or dropped as a whole. |
| `OTEL_METRIC_EXPORT_INTERVAL` | optional | optional | Milliseconds, default 60000. |
| `OTEL_LOGS_LEVEL` | optional | - | Lowest Python log level exported (default `INFO`). Console logging is unchanged. |
| `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS` | optional | - | Comma list from `fastapi, requests, httpx, psycopg, threading`. `psycopg` is the noisy one (the LangGraph checkpointer writes ~40 rows per turn, one span each). |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` etc. | optional | optional | Per-signal endpoints. If only the traces one is set, metrics and logs stay off. |
| `OTEL_SDK_DISABLED=true` | optional | optional | Hard off switch even with an endpoint set. |

`service.version` comes from `RAILWAY_GIT_COMMIT_SHA` (first 12 chars) and
`deployment.environment` from `RAILWAY_ENVIRONMENT_NAME` (or `ENV`), both set by
Railway automatically.

Changing these needs a redeploy/restart of the service (not a rebuild of the web
image: the web reads them at server start).

### Grafana Cloud

1. Grafana Cloud portal → your stack → **OpenTelemetry** → **Configure**. Create a
   token with the `set:otlp` scopes (traces, metrics, logs write).
2. It shows the endpoint and an `Authorization=Basic <base64(instanceId:token)>` header.
3. Railway, both services:
   - `OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp-gateway-prod-<region>.grafana.net/otlp`
   - `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic%20<base64>` (note `%20`)
4. Traces land in Tempo, metrics in Mimir/Prometheus (dots become underscores,
   e.g. `chat_turn_duration_seconds_bucket`), logs in Loki with `trace_id`.

### Honeycomb

- `OTEL_EXPORTER_OTLP_ENDPOINT=https://api.honeycomb.io` (EU: `https://api.eu1.honeycomb.io`)
- `OTEL_EXPORTER_OTLP_HEADERS=x-honeycomb-team=<ingest key>`
- Metrics in Honeycomb need a dataset; add `,x-honeycomb-dataset=gujjutours-metrics`
  to the headers, or set `OTEL_EXPORTER_OTLP_METRICS_HEADERS` separately.
- Traces arrive in datasets named after the services.

### Self-hosted collector (or local testing)

Run an OpenTelemetry Collector with an OTLP/HTTP receiver on 4318 and point both
services at it (`OTEL_EXPORTER_OTLP_ENDPOINT=http://<collector-host>:4318`). On
Railway, run the collector as its own service and use its private
`*.railway.internal` address. Minimal config that forwards traces to Jaeger and
prints metrics and logs:

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318
processors:
  batch: {}
exporters:
  otlp/jaeger:
    endpoint: jaeger:4317
    tls: { insecure: true }
  debug:
    verbosity: detailed
service:
  pipelines:
    traces:  { receivers: [otlp], processors: [batch], exporters: [otlp/jaeger] }
    metrics: { receivers: [otlp], processors: [batch], exporters: [debug] }
    logs:    { receivers: [otlp], processors: [batch], exporters: [debug] }
```

Local run used for verification (2026-11-17):

```
docker network create otel-test-net
docker run -d --name otel-jaeger-test --network otel-test-net -p 127.0.0.1:16686:16686 jaegertracing/all-in-one:1.62.0
docker run -d --name otel-collector-test --network otel-test-net -p 127.0.0.1:4318:4318 \
  -v <dir>/config.yaml:/etc/otelcol/config.yaml otel/opentelemetry-collector:0.111.0 --config=/etc/otelcol/config.yaml
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 uvicorn api.main:app --port 8012
cd web && OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 API_PROXY_TARGET=http://127.0.0.1:8012 npx next start -p 3012
```

Jaeger UI: http://127.0.0.1:16686, service `gujjutours-chat-api`, operation
`chat.turn`. Screenshots and the collector output are in `docs/otel-qa/`.

## What is captured

### One trace per chat turn

```
gujjutours-web   POST /chat/stream route
 └ fetch POST <api>/chat/stream            (traceparent sent to the API only)
   └ gujjutours-chat-api  POST /chat/stream (FastAPI server span)
     └ chat.turn
       ├ llm.chat <model>                  (one per model call)
       ├ agent.tool <tool name>            (one per tool call)
       │  └ supplier <METHOD> <endpoint>   (one per supplier call, retries included)
       │     └ POST / GET                  (requests client span per attempt)
       ├ POST openrouter.ai ...            (httpx client span)
       └ SELECT / INSERT ...               (psycopg; transcript + LangGraph checkpoints)
```

The turn keeps running when the browser disconnects (detached turns); its span
then ends after the HTTP span, still in the same trace. Supplier fan-outs that run
in thread pools (hotel availability in 200-id chunks, flight providers, tour
slots) stay under their tool through the threading instrumentation.

### Span attributes

| Span | Attributes |
|---|---|
| `chat.turn` | `chat.session_id`, `chat.mode` (ask/plan), `chat.regenerate`, `chat.tools` (distinct names), `chat.tool_count`, `chat.message_chars`, `chat.reply_chars`, `chat.duration_ms`, `chat.outcome` (ok / error / cancelled), `error.type` |
| `agent.tool <name>` | `agent.tool.name`, `agent.tool.result_count` (options/results/items/days), `agent.tool.outcome` (ok / error / exception), `agent.tool.error` (short code from an error result), `error.type` |
| `supplier <METHOD> <endpoint>` | `supplier.endpoint` (path without query, numeric segments as `{id}`), `http.request.method`, `http.response.status_code`, `supplier.error_code` (e.g. `NOCOUNT_ERROR`), `supplier.body_status` (the body's own `statusCode`), `supplier.attempts`, `server.address`, `error.type` |
| `llm.chat <model>` | `gen_ai.system` / `gen_ai.provider.name` (openrouter / anthropic / selfhosted), `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.finish_reasons`, `gen_ai.usage.input_tokens` / `output_tokens` / `total_tokens` (when the provider returns them; OpenRouter does), `http.response.status_code` on errors (e.g. 401) |

### Metrics

| Metric | Type | Attributes |
|---|---|---|
| `chat.turns` | counter | `chat.mode`, `outcome` |
| `chat.turn.duration` | histogram (s) | `chat.mode`, `outcome` |
| `agent.tool.calls` | counter | `tool`, `outcome` |
| `agent.tool.duration` | histogram (s) | `tool`, `outcome` |
| `supplier.calls` | counter | `endpoint`, `status` |
| `supplier.duration` | histogram (s) | `endpoint`, `status` |
| `supplier.errors` | counter | `endpoint`, `code` (supplier code, exception type or HTTP status) |
| `llm.calls` | counter | `model`, `provider`, `outcome` |
| `llm.duration` | histogram (s) | `model`, `provider`, `outcome` |
| `llm.tokens` | counter | `model`, `provider`, `type` (input/output) |
| `llm.errors` | counter | `model`, `provider`, `status` (e.g. `401`) |

Plus the standard `http.server.*` (FastAPI), `http.client.*` (requests, httpx) and
`db.client.*` (psycopg) metrics from the instrumentation packages.

### Logs

Python `logging` records at `OTEL_LOGS_LEVEL` and above (default INFO) are
exported with the active `trace_id`/`span_id`, so a log line opens its trace.
Console logging is unchanged. The SDK's own logs are not exported.

## Privacy

- Never recorded: request or response headers (supplier bearer tokens, the
  `gt_session` cookie, the OpenRouter key), request/response bodies, SQL
  parameters (psycopg `capture_parameters` off, no sqlcommenter).
- Not recorded unless `OTEL_CAPTURE_CONTENT=true`: the customer's message, the
  reply, tool arguments (dates, party, names), LLM prompts and completions.
- Recorded always: session id (a UUID), tool names, counts, supplier endpoint
  paths and error codes, model names, token counts, durations. The supplier's
  free-text error messages are never used as a code.
- The supplier `requests` spans carry the full URL; supplier GET query strings are
  ids (`TourId`, `PackageId`, `tourId`), never credentials.
- Exception messages are recorded on error spans (e.g. the OpenRouter 401 text
  "User not found."). Python log records are exported as written; the codebase
  logs paths and ids, not message text, but keep that in mind when adding logs.

## Overhead when off

`init_telemetry` returns at once (no SDK objects created, no libraries patched);
each helper checks one module flag and calls straight through. `llm_callbacks()`
returns `None`, so the model is built exactly as before.
