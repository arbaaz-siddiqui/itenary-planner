"use client";

import { memo } from "react";
import { TravelLoader } from "./TravelLoader";
import { Markdown } from "./Markdown";
import { toolLabel } from "@/lib/segments";
import type { Segment } from "@/lib/types";

type ToolSegment = Extract<Segment, { type: "tool_call" }>;

/** Shown before anything else has arrived. */
function ThinkingLine({ content }: { content?: string }) {
  return (
    <div className="status-in flex items-center gap-2.5 py-1 text-sm text-fg-muted" aria-live="polite">
      <TravelLoader />
      <span>{content ?? "Thinking"}</span>
    </div>
  );
}

/** The one status line: the step running now, with a spinner. */
function StepLine({ step }: { step: ToolSegment }) {
  return (
    <div key={step.id} className="status-in flex items-center gap-2.5 py-1 text-sm" aria-live="polite">
      <TravelLoader />
      <span className="text-fg">{toolLabel(step.name, "running")}</span>
    </div>
  );
}

/**
 * Text as it streams, and ONE status line while nothing is being written.
 *
 * The status line shows what is happening now: the step running ("Searching
 * hotels"), or "Thinking" between steps. Never both, never a list of finished
 * steps with counts, and it goes away as soon as the answer starts arriving.
 * Steps that finished before a paragraph are not shown at all.
 */
export const SegmentList = memo(function SegmentList({
  segments,
  streaming,
}: {
  segments: Segment[];
  streaming: boolean;
}) {
  const texts = segments
    .map((seg, index) => ({ seg, index }))
    .filter((x): x is { seg: Extract<Segment, { type: "text" }>; index: number } => x.seg.type === "text");
  const lastText = texts.length ? texts[texts.length - 1].index : -1;
  const trailing = segments.slice(lastText + 1);
  const running = [...trailing].reverse().find((x): x is ToolSegment => x.type === "tool_call" && x.status === "running");
  const thinking = trailing.find((x): x is Extract<Segment, { type: "thinking" }> => x.type === "thinking");
  // Something is still to come once the last thing that happened is not text.
  const waiting = streaming && (texts.length === 0 || trailing.length > 0);

  return (
    <div className="flex flex-col gap-2">
      {texts.map(({ seg, index }) => (
        <div key={`tx-${index}`} className="min-w-0">
          <Markdown streaming={streaming}>{seg.content}</Markdown>
          {streaming && index === lastText && trailing.length === 0 && <span className="caret text-fg-muted">▍</span>}
        </div>
      ))}
      {waiting &&
        (running ? (
          <StepLine key={running.id} step={running} />
        ) : (
          <ThinkingLine content={thinking?.content ?? (trailing.some((x) => x.type === "tool_call") ? "Putting your answer together" : undefined)} />
        ))}
    </div>
  );
});
