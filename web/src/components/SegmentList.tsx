"use client";

import { memo } from "react";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { Markdown } from "./Markdown";
import { groupSegments, summarizeResult, toolLabel } from "@/lib/segments";
import type { Segment } from "@/lib/types";

type ToolSegment = Extract<Segment, { type: "tool_call" }>;

/** Shown before anything else has arrived. */
function ThinkingLine({ content }: { content?: string }) {
  return (
    <div className="flex items-center gap-2.5 py-1 text-sm text-fg-muted" aria-live="polite">
      <span className="inline-flex items-center gap-1" aria-hidden="true">
        <span className="dot h-1.5 w-1.5 rounded-full bg-brand-blue" />
        <span className="dot h-1.5 w-1.5 rounded-full bg-brand-blue" />
        <span className="dot h-1.5 w-1.5 rounded-full bg-brand-red" />
      </span>
      <span>{content ?? "Thinking"}</span>
    </div>
  );
}

function Spinner() {
  return <Loader2 size={14} className="shrink-0 animate-spin text-brand-blue" aria-hidden />;
}

function StatusIcon({ status }: { status: ToolSegment["status"] }) {
  if (status === "running") return <Spinner />;
  if (status === "error") return <XCircle size={14} className="shrink-0 text-brand-red" aria-hidden />;
  return <CheckCircle2 size={14} className="shrink-0 text-success" aria-hidden />;
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
    // A status line, not a box: "Searching flights" while it runs, then a
    // quiet "Searched flights, 9 results" once it is done.
    <div className="flex items-center gap-2 py-0.5 text-[13px]" aria-live={running ? "polite" : undefined}>
      <StatusIcon status={status} />
      <span className="min-w-0 flex-1 truncate">
        <span className={running ? "text-fg" : "text-fg-muted"}>
          {label}
          {count}
        </span>
        {summary && <span className="text-fg-muted">, {summary}</span>}
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
            <Markdown streaming={streaming}>{s.content}</Markdown>
            {streaming && isLast && <span className="caret text-fg-muted">▍</span>}
          </div>
        );
      })}
    </div>
  );
});
