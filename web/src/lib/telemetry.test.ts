import { describe, expect, it } from "vitest";

import { apiOrigin, telemetryEnabled, webServiceName } from "./telemetry";

describe("telemetryEnabled", () => {
  it("is off without an OTLP endpoint", () => {
    expect(telemetryEnabled({})).toBe(false);
    expect(telemetryEnabled({ OTEL_EXPORTER_OTLP_ENDPOINT: "  " })).toBe(false);
  });

  it("is on with the general or the traces endpoint", () => {
    expect(telemetryEnabled({ OTEL_EXPORTER_OTLP_ENDPOINT: "http://127.0.0.1:4318" })).toBe(true);
    expect(telemetryEnabled({ OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: "https://x/v1/traces" })).toBe(true);
  });

  it("honours OTEL_SDK_DISABLED", () => {
    expect(
      telemetryEnabled({ OTEL_EXPORTER_OTLP_ENDPOINT: "http://x", OTEL_SDK_DISABLED: "true" }),
    ).toBe(false);
  });
});

describe("webServiceName", () => {
  it("defaults to gujjutours-web", () => {
    expect(webServiceName({})).toBe("gujjutours-web");
    expect(webServiceName({ OTEL_SERVICE_NAME: "web-staging" })).toBe("web-staging");
  });
});

describe("apiOrigin", () => {
  it("returns the origin of the API target only", () => {
    expect(apiOrigin("http://127.0.0.1:8012")).toBe("http://127.0.0.1:8012");
    expect(apiOrigin("http://chat-api.railway.internal:8080/x")).toBe(
      "http://chat-api.railway.internal:8080",
    );
    expect(apiOrigin(undefined)).toBeNull();
    expect(apiOrigin("not a url")).toBeNull();
  });
});
