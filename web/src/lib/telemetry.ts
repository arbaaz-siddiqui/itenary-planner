// Server-side telemetry switches, kept pure so they can be unit tested.
// Telemetry is OFF unless an OTLP endpoint is configured, same rule as the
// chat API (observability.py).

type Env = Record<string, string | undefined>;

export const WEB_SERVICE_NAME = "gujjutours-web";

/** True when the environment asks for OpenTelemetry export. */
export function telemetryEnabled(env: Env): boolean {
  if ((env.OTEL_SDK_DISABLED ?? "").trim().toLowerCase() === "true") return false;
  return Boolean(
    (env.OTEL_EXPORTER_OTLP_ENDPOINT ?? "").trim() ||
      (env.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT ?? "").trim(),
  );
}

/** Service name: OTEL_SERVICE_NAME when set, else gujjutours-web. */
export function webServiceName(env: Env): string {
  return (env.OTEL_SERVICE_NAME ?? "").trim() || WEB_SERVICE_NAME;
}

/**
 * The URL prefix that may receive `traceparent`: the chat API only. Context is
 * never propagated to third-party hosts (it would leak trace ids).
 */
export function apiOrigin(target: string | undefined): string | null {
  if (!target) return null;
  try {
    return new URL(target).origin;
  } catch {
    return null;
  }
}
