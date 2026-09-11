"use client";

import { memo } from "react";
import { Markdown } from "./Markdown";
import { groupSegments, summarizeResult, toolLabel } from "@/lib/segments";
import type { Segment } from "@/lib/types";

type ToolSegment = Extract<Segment, { type: "tool_call" }>;

/** Reuses the same three-dot animation the old typing indicator used. */
function ThinkingLine({ content }: { content?: string }) {
  return (
    <div
      className="flex items-center gap-2 py-0.5 text-xs text-fg-muted"
      aria-live="polite"
    >
      <span className="inline-flex items-center gap-1" aria-hidden="true">
        <span className="dot h-1.5 w-1.5 rounded-full bg-fg-muted" />
        <span className="dot h-1.5 w-1.5 rounded-full bg-fg-muted" />
        <span className="dot h-1.5 w-1.5 rounded-full bg-fg-muted" />
      </span>
      <span className="animate-pulse">{content ?? "Thinking…"}</span>
    </div>
  );
}

function Spinner() {
  return (
    <svg
      className="h-3 w-3 shrink-0 animate-spin text-fg-muted"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.25" />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

function Check() {
  return (
    <svg
      className="h-3 w-3 shrink-0 text-emerald-600 dark:text-emerald-400"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="3"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

function Cross() {
  return (
    <svg
      className="h-3 w-3 shrink-0 text-red-500"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="3"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}

function StatusIcon({ status }: { status: ToolSegment["status"] }) {
  if (status === "running") return <Spinner />;
  if (status === "error") return <Cross />;
  return <Check />;
}

/**
 * One tool chip: a single progress line, not expandable.
 *
 * It shows only a human label and a result count. Arguments and results are
 * NOT rendered anywhere: they carry the supplier host, endpoint paths and raw
 * request/response bodies, which are internal and must never reach a customer.
 */
const ToolChip = memo(function ToolChip({ calls }: { calls: ToolSegment[] }) {
  const head = calls[0];
  const running = calls.some((c) => c.status === "running");
  const errored = !running && calls.some((c) => c.status === "error");
  const status: ToolSegment["status"] = running ? "running" : errored ? "error" : "done";

  const label = toolLabel(head.name, status);
  // A collapsed run of N calls reads as one action with a count.
  const count = calls.length > 1 ? ` ×${calls.length}` : "";

  // Summary only makes sense once something came back. For a collapsed run we
  // summarise the run, not each call, so the line stays one line.
  const summary =
    status === "running"
      ? null
      : calls.length === 1
        ? summarizeResult(head.result)
        : `${calls.filter((c) => c.status !== "error").length} of ${calls.length} succeeded`;

  return (
    <div className="flex items-center gap-2 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs">
      <StatusIcon status={status} />
      <span className="min-w-0 flex-1 truncate">
        <span className={running ? "text-fg-muted" : "font-medium"}>
          {label}
          {count}
        </span>
        {summary && <span className="ml-1.5 text-fg-muted">· {summary}</span>}
      </span>
    </div>
  );
});

/**
 * Walk the segments in order.
 *
 * `streaming` gates the blinking caret, and it only ever goes on the LAST text
 * segment — a caret on an earlier paragraph would claim text is still arriving
 * somewhere it is not.
 */
export const SegmentList = memo(function SegmentList({
  segments,
  streaming,
}: {
  segments: Segment[];
  streaming: boolean;
}) {
  const rows = groupSegments(segments);
  const lastIndex = segments.length - 1;

  return (
    <div className="flex flex-col gap-2">
      {rows.map((row) => {
        if (row.kind === "tool_group") {
          return <ToolChip key={`g-${row.index}`} calls={row.calls} />;
        }

        const s = row.segment;

        if (s.type === "thinking") {
          return <ThinkingLine key={`th-${row.index}`} content={s.content} />;
        }

        if (s.type === "tool_call") {
          return <ToolChip key={s.id || `t-${row.index}`} calls={[s]} />;
        }

        const isLast = row.index === lastIndex;
        return (
          <div key={`tx-${row.index}`} className="min-w-0">
            <Markdown>{s.content}</Markdown>
            {streaming && isLast && <span className="caret text-fg-muted">▍</span>}
          </div>
        );
      })}
    </div>
  );
});
