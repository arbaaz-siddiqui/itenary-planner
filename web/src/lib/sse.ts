import type { ApiCall, StreamEvent } from "./types";

/**
 * Parse a text/event-stream body into StreamEvents.
 *
 * We hand-roll this instead of using EventSource because EventSource can only
 * issue GETs and the contract puts the message in a POST body. Frames are
 * separated by a blank line; a frame may carry multiple `data:` lines, which
 * the spec says to join with "\n".
 */
export async function* parseSSE(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const onAbort = () => void reader.cancel().catch(() => {});
  signal?.addEventListener("abort", onAbort);

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Tolerate \r\n as well as \n frame separators.
      let sep: number;
      while ((sep = findFrameEnd(buffer)) !== -1) {
        const raw = buffer.slice(0, sep);
        buffer = buffer.slice(sep).replace(/^(\r?\n){2}/, "");
        const parsed = parseFrame(raw);
        if (parsed) yield parsed;
      }
    }
    // Flush a trailing frame that arrived without its blank-line terminator.
    const tail = parseFrame(buffer);
    if (tail) yield tail;
  } finally {
    signal?.removeEventListener("abort", onAbort);
    reader.releaseLock?.();
  }
}

function toolStatus(v: unknown): "running" | "done" | "error" | undefined {
  return v === "running" || v === "done" || v === "error" ? v : undefined;
}

function findFrameEnd(buf: string): number {
  const a = buf.indexOf("\n\n");
  const b = buf.indexOf("\r\n\r\n");
  if (a === -1) return b;
  if (b === -1) return a;
  return Math.min(a, b);
}

/** Turn one raw SSE frame into a StreamEvent, or null if unusable. */
export function parseFrame(raw: string): StreamEvent | null {
  const trimmed = raw.trim();
  if (!trimmed || trimmed.startsWith(":")) return null; // comment / keep-alive

  let event = "message";
  const dataLines: string[] = [];

  for (const line of trimmed.split(/\r?\n/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }

  const data = dataLines.join("\n");
  if (!data) return null;

  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data) as Record<string, unknown>;
  } catch {
    // A malformed frame should not kill the stream; treat token text as raw.
    return event === "token" ? { type: "token", text: data } : null;
  }

  switch (event) {
    // The turn opens with `start` before the model has decided anything, which
    // is the cue to show "Thinking…" from the first frame rather than waiting
    // for the idle gap to elapse.
    case "start":
      return { type: "start", session_id: String(payload.session_id ?? "") };
    case "token":
      return { type: "token", text: String(payload.text ?? "") };
    case "tool":
      // The backend sends the START and the RESULT of one call as two `tool`
      // frames sharing an `id` (start: status=running + input, result:
      // status=done + output). Both must survive parsing or the UI cannot
      // match them up and would render a call twice.
      return {
        type: "tool",
        tool: {
          id: payload.id != null ? String(payload.id) : undefined,
          name: String(payload.name ?? "tool"),
          input: payload.input,
          output: payload.output,
          status: toolStatus(payload.status),
          api_calls: Array.isArray(payload.api_calls)
            ? (payload.api_calls as ApiCall[])
            : undefined,
        },
      };
    case "options":
      return {
        type: "options",
        group: {
          kind: String(payload.kind ?? "tours"),
          items: Array.isArray(payload.items) ? (payload.items as Record<string, unknown>[]) : [],
        },
      };
    case "error":
      return { type: "error", message: String(payload.message ?? "Unknown error") };
    case "done":
      return { type: "done", message_id: String(payload.message_id ?? "") };
    default:
      return null;
  }
}
