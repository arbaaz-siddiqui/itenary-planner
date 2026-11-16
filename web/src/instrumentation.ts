// Next.js calls register() once per server runtime at startup.
//
// OpenTelemetry for the web server (route handlers, server fetches), exported
// over OTLP to whatever backend the standard OTEL_* env vars name. OFF unless
// OTEL_EXPORTER_OTLP_ENDPOINT (or ..._TRACES_ENDPOINT) is set, and only in the
// Node runtime: nothing here reaches the browser bundle or the edge runtime.
//
// `traceparent` is sent only to the chat API (API_PROXY_TARGET), so a chat
// turn is one trace from this server into gujjutours-chat-api.

import { apiOrigin, telemetryEnabled, webServiceName } from "./lib/telemetry";

export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  if (!telemetryEnabled(process.env)) return;

  const { registerOTel } = await import("@vercel/otel");
  const api = apiOrigin(process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000");
  const environment =
    process.env.RAILWAY_ENVIRONMENT_NAME ?? process.env.ENV ?? process.env.NODE_ENV ?? "";
  const version = process.env.RAILWAY_GIT_COMMIT_SHA?.slice(0, 12);

  registerOTel({
    serviceName: webServiceName(process.env),
    attributes: {
      "service.namespace": "gujjutours",
      ...(environment ? { "deployment.environment": environment } : {}),
      ...(version ? { "service.version": version } : {}),
    },
    instrumentationConfig: {
      fetch: {
        propagateContextUrls: api ? [api] : [],
      },
    },
  });
}
