// Folds stream events into an ORDERED list of typed segments.
//
// Why segments instead of one concatenated string: the agent interleaves prose
// and tool calls. "Let me check availability" -> search_tours -> "Here are 3
// options" is three distinct things that happened in an order, and a single
// string throws that order away — the tool chips end up in a debug drawer at
// the bottom, detached from the sentence that motivated them.
//
// This module is deliberately PURE: no DOM, no timers, no React. Every export
// takes a Segment[] and returns a new Segment[]. That is what makes the folding
// rules unit-testable without rendering anything, and it keeps the one piece of
// wall-clock behaviour (the idle "Thinking…" timer) out in the component where
// it can be cleaned up on unmount.

import type { Segment, StreamEvent, ToolCall } from "./types";

/** How long the stream must be silent before we admit we are waiting. */
export const QUIET_GAP_MS = 1200;

/**
 * Fold one stream event into the segment list, returning a NEW array.
 *
 * Returns the same array reference when the event changes nothing, so callers
 * can `if (next === prev) return prev` and skip a re-render.
 */
export function reduceSegments(segments: Segment[], event: StreamEvent): Segment[] {
  switch (event.type) {
    case "token":
      return appendText(segments, event.text);

    case "tool":
      return foldTool(segments, event.tool);

    // `start`, `options`, `error` and `done` do not produce segments.
    // Options render as cards below the message, errors as a banner, and both
    // are still kept on the Message by the caller. They DO count as stream
    // activity, which is why the idle timer lives in the caller and is re-armed
    // on every event regardless of whether the segments changed.
    default:
      return segments;
  }
}

/**
 * A text delta continues the current text segment, or starts one.
 *
 * The exception is a synthetic `thinking` placeholder: it is a guess that the
 * model has gone quiet, and the arrival of text proves the guess wrong. So the
 * placeholder is REPLACED, not appended after — otherwise a single reply that
 * happened to pause mid-sentence would render as "Thinking…" followed by a
 * second bubble, which looks like two separate replies.
 */
export function appendText(segments: Segment[], text: string): Segment[] {
  if (!text) return segments;

  const last = segments[segments.length - 1];

  if (last?.type === "text") {
    const next = segments.slice(0, -1);
    next.push({ type: "text", content: last.content + text });
    return next;
  }

  if (last?.type === "thinking") {
    const next = segments.slice(0, -1);
    next.push({ type: "text", content: text });
    return next;
  }

  return [...segments, { type: "text", content: text }];
}

/**
 * A tool frame is either the START of a call or its RESULT — the backend sends
 * both as `event: tool` with the same `id`.
 *
 *   start  -> status "running", carries `input`
 *   result -> status "done",    carries `output`
 *
 * A start ALWAYS pushes a new segment and never merges into neighbouring text:
 * a tool call is a real boundary in what happened, and collapsing it would put
 * the chip in the wrong place in the narrative.
 *
 * It does, however, consume a trailing `thinking` placeholder, for the same
 * reason text does: the placeholder was a guess that the model had gone quiet,
 * and a tool starting disproves it. Without this the placeholder is stranded
 * forever between two chips, since only the TAIL is ever replaced.
 *
 * A result patches the matching segment IN PLACE, found by id. It must never
 * append, or every call would render twice. Matching on id (not on position)
 * is what makes out-of-order and concurrent results correct — the agent runs
 * tools in parallel, so result B can land before result A.
 */
export function foldTool(segments: Segment[], tool: ToolFrame): Segment[] {
  const id = tool.id ?? "";
  const isResult = tool.status === "done" || tool.status === "error" || tool.output !== undefined;

  if (isResult && id) {
    const idx = segments.findIndex((s) => s.type === "tool_call" && s.id === id);
    if (idx !== -1) {
      const target = segments[idx] as Extract<Segment, { type: "tool_call" }>;
      const next = segments.slice();
      next[idx] = {
        ...target,
        // A result frame may omit the name; keep the one from the start frame.
        name: tool.name && tool.name !== "tool" ? tool.name : target.name,
        result: tool.output,
        status: tool.status === "error" ? "error" : "done",
      };
      return next;
    }
    // A result whose start we never saw (reconnect, or a dropped frame) still
    // deserves a row — showing it finished is better than dropping the call.
    const base = dropTrailingThinking(segments);
    return [
      ...base,
      {
        type: "tool_call",
        id: id || syntheticId(base),
        name: tool.name || "tool",
        args: tool.input,
        result: tool.output,
        status: tool.status === "error" ? "error" : "done",
      },
    ];
  }

  const base = dropTrailingThinking(segments);
  return [
    ...base,
    {
      type: "tool_call",
      id: id || syntheticId(base),
      name: tool.name || "tool",
      args: tool.input,
      status: tool.status === "error" ? "error" : "running",
      ...(tool.output !== undefined ? { result: tool.output } : {}),
    },
  ];
}

/** Shape of a live `tool` frame. Mirrors ToolCall's optional id/status. */
export interface ToolFrame {
  id?: string;
  name: string;
  input?: unknown;
  output?: unknown;
  status?: "running" | "done" | "error";
}

/** Ids are only needed for matching; an unidentified call gets a stable one. */
function syntheticId(segments: Segment[]): string {
  return `anon-${segments.length}`;
}

/**
 * Append the synthetic "Thinking…" placeholder.
 *
 * Called by the timer owner, never from the event path — the reducer has no
 * concept of elapsed time and adding one would make it untestable. No-ops when
 * a placeholder is already the tail, or when text is mid-flight (the caller
 * also guards this, but a pure guard here keeps the invariant local).
 */
export function appendThinking(segments: Segment[], content?: string): Segment[] {
  const last = segments[segments.length - 1];
  if (last?.type === "thinking") return segments;
  return [...segments, { type: "thinking", ...(content ? { content } : {}) }];
}

/** Drop a trailing placeholder — used when the stream settles or errors. */
export function dropTrailingThinking(segments: Segment[]): Segment[] {
  const last = segments[segments.length - 1];
  return last?.type === "thinking" ? segments.slice(0, -1) : segments;
}

/**
 * True when the tail is a text segment that is still being written, i.e. the
 * stream is mid-sentence and a "Thinking…" line would be wrong.
 */
export function isWritingText(segments: Segment[]): boolean {
  return segments[segments.length - 1]?.type === "text";
}

/**
 * Tool calls in the shape the transcript stores, for persisting the turn.
 *
 * A `tool_call` segment already carries everything except `api_calls`, which
 * the live `tool` frames do not include — the stored copy the backend writes
 * has them, and a refetch picks that up.
 */
export function segmentsToToolCalls(segments: Segment[]): ToolCall[] {
  return segments
    .filter((s): s is Extract<Segment, { type: "tool_call" }> => s.type === "tool_call")
    .map((s) => ({
      id: s.id,
      name: s.name,
      input: s.args,
      output: s.result,
      status: s.status,
    }));
}

/** Concatenated prose, for persisting the finished turn as `content`. */
export function segmentsToText(segments: Segment[]): string {
  return segments
    .filter((s): s is Extract<Segment, { type: "text" }> => s.type === "text")
    .map((s) => s.content)
    .join("");
}

// --- render-time grouping -------------------------------------------------

/** A row to render: either one segment, or a run of same-name tool calls. */
export type SegmentRow =
  | { kind: "segment"; segment: Segment; index: number }
  | {
      kind: "tool_group";
      name: string;
      calls: Extract<Segment, { type: "tool_call" }>[];
      index: number;
    };

/**
 * Collapse ADJACENT same-name tool calls into one row ("Searching tours ×4").
 *
 * Done here, at render time, rather than in the stored segments: the raw list
 * stays a faithful log of what happened, and only the presentation folds.
 *
 * "Adjacent" is strict — any other segment type between two calls breaks the
 * run. Ordering is real information: searching tours, writing a paragraph, then
 * searching tours again is a different story from four searches in a row, and
 * merging across the paragraph would tell the wrong one.
 */
export function groupSegments(segments: Segment[]): SegmentRow[] {
  const rows: SegmentRow[] = [];

  for (let i = 0; i < segments.length; i++) {
    const s = segments[i];
    if (s.type !== "tool_call") {
      rows.push({ kind: "segment", segment: s, index: i });
      continue;
    }

    const run = [s];
    let j = i + 1;
    while (j < segments.length) {
      const nxt = segments[j];
      if (nxt.type !== "tool_call" || nxt.name !== s.name) break;
      run.push(nxt);
      j++;
    }

    if (run.length === 1) rows.push({ kind: "segment", segment: s, index: i });
    else rows.push({ kind: "tool_group", name: s.name, calls: run, index: i });

    i = j - 1;
  }

  return rows;
}

// --- labels ---------------------------------------------------------------

// Tool names are internal identifiers. A customer should read what the agent is
// doing, not the function it called.
const TOOL_LABELS: Record<string, string> = {
  search_tours: "Searching tours",
  get_tour_options: "Checking tour options",
  search_hotels: "Searching hotels",
  get_hotel_details: "Checking hotel details",
  search_flights: "Searching flights",
  search_transfers: "Searching transfers",
  get_visa_info: "Checking visa rules",
  search_restaurants: "Searching restaurants",
  get_roe: "Converting currency",
};

const DONE_LABELS: Record<string, string> = {
  search_tours: "Searched tours",
  get_tour_options: "Checked tour options",
  search_hotels: "Searched hotels",
  get_hotel_details: "Checked hotel details",
  search_flights: "Searched flights",
  search_transfers: "Searched transfers",
  get_visa_info: "Checked visa rules",
  search_restaurants: "Searched restaurants",
  get_roe: "Converted currency",
};

/** Friendly label for a tool name, for the given status. */
export function toolLabel(name: string, status: "running" | "done" | "error"): string {
  const map = status === "running" ? TOOL_LABELS : DONE_LABELS;
  const hit = map[name];
  if (hit) return hit;
  // Unknown tool: "get_tour_addons" -> "Get tour addons".
  const words = name.replace(/[_-]+/g, " ").trim();
  if (!words) return "Working";
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * One short line summarising a finished call, for the collapsed chip.
 *
 * Must stay CHEAP: real tool output runs 200KB+, so this only ever reads a
 * couple of top-level keys and never stringifies the payload.
 */
export function summarizeResult(result: unknown): string | null {
  if (result == null) return null;
  if (Array.isArray(result)) return `${result.length} result${result.length === 1 ? "" : "s"}`;
  if (typeof result !== "object") return null;

  const o = result as Record<string, unknown>;

  if (typeof o.error === "string" && o.error) return "failed";
  if (typeof o.count === "number") return `${o.count} result${o.count === 1 ? "" : "s"}`;

  for (const key of ["results", "options", "items", "hotels", "tours", "flights"]) {
    const v = o[key];
    if (Array.isArray(v)) return `${v.length} result${v.length === 1 ? "" : "s"}`;
  }

  if (typeof o.message === "string" && o.message.length <= 60) return o.message;
  return "done";
}
