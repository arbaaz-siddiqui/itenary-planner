import { describe, expect, it } from "vitest";
import {
  appendText,
  appendThinking,
  dropTrailingThinking,
  groupSegments,
  isWritingText,
  reduceSegments,
  segmentsToText,
  summarizeResult,
  toolLabel,
} from "./segments";
import type { Segment, StreamEvent } from "./types";

const token = (text: string): StreamEvent => ({ type: "token", text });

const toolStart = (id: string, name: string, input?: unknown): StreamEvent => ({
  type: "tool",
  tool: { id, name, input, status: "running" },
});

const toolResult = (id: string, name: string, output: unknown): StreamEvent => ({
  type: "tool",
  tool: { id, name, output, status: "done" },
});

/** Fold a whole script of events, the way the component does. */
const run = (events: StreamEvent[], initial: Segment[] = []): Segment[] =>
  events.reduce(reduceSegments, initial);

describe("text appending", () => {
  it("starts a text segment from the first token", () => {
    expect(run([token("Hello")])).toEqual([{ type: "text", content: "Hello" }]);
  });

  it("appends consecutive tokens into ONE segment", () => {
    const out = run([token("Here are "), token("three "), token("hotels.")]);
    expect(out).toHaveLength(1);
    expect(out[0]).toEqual({ type: "text", content: "Here are three hotels." });
  });

  it("ignores an empty delta rather than starting a blank segment", () => {
    const before: Segment[] = [{ type: "text", content: "hi" }];
    expect(appendText(before, "")).toBe(before);
  });

  it("returns a NEW array (never mutates the input)", () => {
    const before: Segment[] = [{ type: "text", content: "a" }];
    const after = appendText(before, "b");
    expect(after).not.toBe(before);
    expect(before[0]).toEqual({ type: "text", content: "a" });
  });

  it("starts a fresh text segment after a tool call", () => {
    const out = run([
      token("Checking. "),
      toolStart("t1", "search_tours"),
      toolResult("t1", "search_tours", { count: 2 }),
      token("Found two."),
    ]);
    expect(out.map((s) => s.type)).toEqual(["text", "tool_call", "text"]);
    expect(out[2]).toEqual({ type: "text", content: "Found two." });
  });
});

describe("thinking replacement", () => {
  it("REPLACES a trailing thinking placeholder with the text segment", () => {
    const withThinking = appendThinking([]);
    expect(withThinking).toEqual([{ type: "thinking" }]);

    const out = appendText(withThinking, "Here we go");
    expect(out).toEqual([{ type: "text", content: "Here we go" }]);
  });

  it("does not leave two bubbles when text arrives after a pause mid-reply", () => {
    // A reply that paused: text, placeholder appears, text resumes.
    let segs = run([token("One moment")]);
    segs = appendThinking(segs);
    segs = run([token(" — done.")], segs);

    // The placeholder is gone, but note the resumed text starts a NEW text
    // segment because the placeholder replaced the tail. Still ONE bubble each
    // side of nothing: exactly one thinking-free run of text segments.
    expect(segs.some((s) => s.type === "thinking")).toBe(false);
    expect(segmentsToText(segs)).toBe("One moment — done.");
  });

  it("keeps earlier segments intact when replacing the placeholder", () => {
    const segs = appendThinking([
      { type: "text", content: "First." },
      { type: "tool_call", id: "t1", name: "search_tours", args: {}, status: "done" },
    ]);
    const out = appendText(segs, "Second.");
    expect(out.map((s) => s.type)).toEqual(["text", "tool_call", "text"]);
    expect(out[2]).toEqual({ type: "text", content: "Second." });
  });

  it("never stacks two placeholders", () => {
    const once = appendThinking([]);
    expect(appendThinking(once)).toBe(once);
  });

  it("drops a trailing placeholder on settle, and no-ops otherwise", () => {
    expect(dropTrailingThinking([{ type: "thinking" }])).toEqual([]);
    const text: Segment[] = [{ type: "text", content: "x" }];
    expect(dropTrailingThinking(text)).toBe(text);
  });

  it("reports whether text is mid-flight (the timer's guard)", () => {
    expect(isWritingText([{ type: "text", content: "x" }])).toBe(true);
    expect(isWritingText([{ type: "thinking" }])).toBe(false);
    expect(isWritingText([])).toBe(false);
  });
});

describe("tool_call never merges", () => {
  it("pushes a new segment for every start, even back to back", () => {
    const out = run([
      toolStart("t1", "search_tours", { city: "Dubai" }),
      toolStart("t2", "search_tours", { city: "Abu Dhabi" }),
    ]);
    expect(out).toHaveLength(2);
    expect(out.map((s) => s.type)).toEqual(["tool_call", "tool_call"]);
  });

  it("does not fold a tool call into surrounding text", () => {
    const out = run([token("before "), toolStart("t1", "search_tours"), token("after")]);
    expect(out.map((s) => s.type)).toEqual(["text", "tool_call", "text"]);
    expect(out[0]).toEqual({ type: "text", content: "before " });
  });

  it("consumes a trailing thinking placeholder, so it is never stranded", () => {
    // A tool starting disproves "the model has gone quiet" just as text does.
    // Without this the placeholder would sit between two chips forever, since
    // only the TAIL is ever replaced.
    const segs = appendThinking([]);
    const out = reduceSegments(segs, toolStart("t1", "search_tours"));
    expect(out.map((s) => s.type)).toEqual(["tool_call"]);
  });

  it("does not strand a placeholder between two tool chips", () => {
    let segs = run([toolStart("t1", "search_hotels")]);
    segs = appendThinking(segs); // gap while the supplier is slow
    segs = run([toolStart("t2", "search_tours")], segs);
    expect(segs.map((s) => s.type)).toEqual(["tool_call", "tool_call"]);
    expect(segs.some((x) => x.type === "thinking")).toBe(false);
  });

  it("consumes a trailing placeholder before an orphan result too", () => {
    const segs = appendThinking([]);
    const out = reduceSegments(segs, toolResult("orphan", "search_tours", { count: 1 }));
    expect(out.map((s) => s.type)).toEqual(["tool_call"]);
  });

  it("leaves a placeholder alone when the result patches an existing call", () => {
    // Nothing new is appended here, so the tail placeholder is still the
    // truthful "waiting for the next thing" state.
    let segs = run([toolStart("t1", "search_tours")]);
    segs = appendThinking(segs);
    segs = run([toolResult("t1", "search_tours", { count: 2 })], segs);
    expect(segs.map((s) => s.type)).toEqual(["tool_call", "thinking"]);
  });

  it("records args and running status from the start frame", () => {
    const out = run([toolStart("t1", "search_tours", { city: "Dubai" })]);
    expect(out[0]).toEqual({
      type: "tool_call",
      id: "t1",
      name: "search_tours",
      args: { city: "Dubai" },
      status: "running",
    });
  });
});

describe("result patching by id", () => {
  it("patches the matching call IN PLACE without appending", () => {
    const out = run([
      toolStart("t1", "search_tours", { city: "Dubai" }),
      toolResult("t1", "search_tours", { count: 4 }),
    ]);
    expect(out).toHaveLength(1);
    expect(out[0]).toEqual({
      type: "tool_call",
      id: "t1",
      name: "search_tours",
      args: { city: "Dubai" },
      result: { count: 4 },
      status: "done",
    });
  });

  it("patches across intervening text without disturbing it", () => {
    const out = run([
      toolStart("t1", "search_tours"),
      token("Meanwhile…"),
      toolResult("t1", "search_tours", { count: 1 }),
    ]);
    expect(out.map((s) => s.type)).toEqual(["tool_call", "text"]);
    const call = out[0] as Extract<Segment, { type: "tool_call" }>;
    expect(call.status).toBe("done");
    expect(out[1]).toEqual({ type: "text", content: "Meanwhile…" });
  });

  it("marks an error result as errored", () => {
    const out = run([
      toolStart("t1", "search_tours"),
      { type: "tool", tool: { id: "t1", name: "search_tours", output: { error: "boom" }, status: "error" } },
    ]);
    const call = out[0] as Extract<Segment, { type: "tool_call" }>;
    expect(call.status).toBe("error");
  });

  it("keeps the start frame's name when the result omits it", () => {
    const out = run([
      toolStart("t1", "search_tours"),
      { type: "tool", tool: { id: "t1", name: "tool", output: { count: 1 }, status: "done" } },
    ]);
    const call = out[0] as Extract<Segment, { type: "tool_call" }>;
    expect(call.name).toBe("search_tours");
  });

  it("appends a row for a result whose start was never seen", () => {
    const out = run([toolResult("orphan", "search_tours", { count: 3 })]);
    expect(out).toHaveLength(1);
    const call = out[0] as Extract<Segment, { type: "tool_call" }>;
    expect(call.status).toBe("done");
    expect(call.id).toBe("orphan");
  });
});

describe("out-of-order and concurrent results", () => {
  it("resolves results that arrive in reverse start order", () => {
    const out = run([
      toolStart("a", "search_tours", { city: "Dubai" }),
      toolStart("b", "search_hotels", { city: "Dubai" }),
      toolResult("b", "search_hotels", { count: 9 }),
      toolResult("a", "search_tours", { count: 4 }),
    ]);

    expect(out).toHaveLength(2);
    // Order is the START order — that is the order things actually happened.
    const [first, second] = out as Extract<Segment, { type: "tool_call" }>[];
    expect(first.id).toBe("a");
    expect(first.result).toEqual({ count: 4 });
    expect(second.id).toBe("b");
    expect(second.result).toEqual({ count: 9 });
  });

  it("does not let one result patch a different call's id", () => {
    const out = run([
      toolStart("a", "search_tours"),
      toolStart("b", "search_tours"),
      toolResult("a", "search_tours", { count: 1 }),
    ]);
    const [a, b] = out as Extract<Segment, { type: "tool_call" }>[];
    expect(a.status).toBe("done");
    expect(b.status).toBe("running");
  });

  it("a second result for the same id overwrites rather than duplicating", () => {
    const out = run([
      toolStart("a", "search_tours"),
      toolResult("a", "search_tours", { count: 1 }),
      toolResult("a", "search_tours", { count: 2 }),
    ]);
    expect(out).toHaveLength(1);
    expect((out[0] as Extract<Segment, { type: "tool_call" }>).result).toEqual({ count: 2 });
  });
});

describe("unknown and non-segment events are ignored", () => {
  it("ignores start, options, error and done", () => {
    const before: Segment[] = [{ type: "text", content: "hi" }];
    const events: StreamEvent[] = [
      { type: "start", session_id: "s1" },
      { type: "options", group: { kind: "hotels", items: [] } },
      { type: "error", message: "nope" },
      { type: "done", message_id: "m1" },
    ];
    for (const ev of events) {
      // Same reference back — the caller can skip the re-render.
      expect(reduceSegments(before, ev)).toBe(before);
    }
  });

  it("ignores a wholly unknown event shape without throwing", () => {
    const before: Segment[] = [{ type: "text", content: "hi" }];
    const bogus = { type: "sparkle", payload: 1 } as unknown as StreamEvent;
    expect(reduceSegments(before, bogus)).toBe(before);
  });
});

describe("render-time grouping", () => {
  const call = (id: string, name: string): Segment => ({
    type: "tool_call",
    id,
    name,
    args: {},
    status: "done",
  });

  it("collapses a run of adjacent same-name calls", () => {
    const rows = groupSegments([call("1", "search_tours"), call("2", "search_tours"), call("3", "search_tours")]);
    expect(rows).toHaveLength(1);
    expect(rows[0].kind).toBe("tool_group");
    if (rows[0].kind === "tool_group") {
      expect(rows[0].calls).toHaveLength(3);
      expect(rows[0].name).toBe("search_tours");
    }
  });

  it("leaves a lone call as a plain segment row", () => {
    const rows = groupSegments([call("1", "search_tours")]);
    expect(rows[0].kind).toBe("segment");
  });

  it("does NOT collapse across an intervening text segment", () => {
    const rows = groupSegments([
      call("1", "search_tours"),
      { type: "text", content: "one sec" },
      call("2", "search_tours"),
    ]);
    expect(rows).toHaveLength(3);
    expect(rows.map((r) => r.kind)).toEqual(["segment", "segment", "segment"]);
  });

  it("does not collapse different tool names that sit next to each other", () => {
    const rows = groupSegments([call("1", "search_tours"), call("2", "search_hotels")]);
    expect(rows).toHaveLength(2);
  });

  it("collapses two separate runs independently", () => {
    const rows = groupSegments([
      call("1", "search_tours"),
      call("2", "search_tours"),
      { type: "text", content: "and now" },
      call("3", "search_hotels"),
      call("4", "search_hotels"),
    ]);
    expect(rows.map((r) => r.kind)).toEqual(["tool_group", "segment", "tool_group"]);
  });

  it("preserves every segment exactly once", () => {
    const segs: Segment[] = [
      { type: "text", content: "a" },
      call("1", "search_tours"),
      call("2", "search_tours"),
      { type: "thinking" },
      call("3", "search_tours"),
    ];
    const rows = groupSegments(segs);
    const flat = rows.flatMap((r) => (r.kind === "segment" ? [r.segment] : r.calls));
    expect(flat).toEqual(segs);
  });
});

describe("labels and result summaries", () => {
  it("maps internal tool names to friendly labels", () => {
    expect(toolLabel("search_tours", "running")).toBe("Searching tours");
    expect(toolLabel("search_tours", "done")).toBe("Searched tours");
    expect(toolLabel("get_tour_options", "running")).toBe("Checking tour options");
  });

  it("humanises an unknown tool name instead of showing snake_case", () => {
    expect(toolLabel("get_tour_addons", "running")).toBe("Get tour addons");
  });

  it("summarises common result shapes without stringifying them", () => {
    expect(summarizeResult({ count: 4 })).toBe("4 results");
    expect(summarizeResult({ count: 1 })).toBe("1 result");
    expect(summarizeResult({ results: [1, 2] })).toBe("2 results");
    expect(summarizeResult([1, 2, 3])).toBe("3 results");
    expect(summarizeResult({ error: "supplier down" })).toBe("failed");
    expect(summarizeResult(null)).toBeNull();
    expect(summarizeResult(undefined)).toBeNull();
  });
});

describe("persisting the finished turn", () => {
  it("concatenates only the text segments, in order", () => {
    const segs = run([
      token("Checking availability. "),
      toolStart("t1", "search_tours"),
      toolResult("t1", "search_tours", { count: 2 }),
      token("Found 2 tours."),
    ]);
    expect(segmentsToText(segs)).toBe("Checking availability. Found 2 tours.");
  });

  it("ignores placeholders when building the persisted content", () => {
    const segs = appendThinking([{ type: "text", content: "hi" }]);
    expect(segmentsToText(segs)).toBe("hi");
  });
});

describe("a realistic interleaved turn", () => {
  it("keeps thinking -> tool -> text ordering", () => {
    let segs: Segment[] = [];
    segs = reduceSegments(segs, { type: "start", session_id: "s1" });
    segs = appendThinking(segs); // idle timer fires before the model decides
    segs = reduceSegments(segs, toolStart("t1", "search_tours", { city: "Dubai" }));
    segs = reduceSegments(segs, toolStart("t2", "search_tours", { city: "Dubai" }));
    segs = reduceSegments(segs, toolResult("t1", "search_tours", { count: 4 }));
    segs = reduceSegments(segs, toolResult("t2", "search_tours", { count: 6 }));
    segs = reduceSegments(segs, token("Here are the best "));
    segs = reduceSegments(segs, token("tours."));
    segs = dropTrailingThinking(segs);

    // The placeholder was consumed by the first tool start, so the finished
    // turn is exactly what happened: two searches, then the reply.
    expect(segs.map((s) => s.type)).toEqual(["tool_call", "tool_call", "text"]);
    expect(segmentsToText(segs)).toBe("Here are the best tours.");

    // At render time the two searches collapse into one row.
    const rows = groupSegments(segs);
    expect(rows.map((r) => r.kind)).toEqual(["tool_group", "segment"]);
  });
});
