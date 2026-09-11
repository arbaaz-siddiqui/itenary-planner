"use client";

import { memo, useMemo } from "react";
import { Markdown } from "./Markdown";
import { OptionCards } from "./OptionCards";
import { SegmentList } from "./SegmentList";
import type { Message, OptionItem, Segment } from "@/lib/types";

/**
 * Key for matching a comparison-table row to its option card.
 *
 * The agent retypes the tour name into the table, so it can differ from the
 * option row by case, spacing, the recommended star, or a trailing marker.
 * Comparing on letters and digits alone is what makes the match hold.
 */
/** A GFM table always renders a header separator row. */
const SEPARATOR_ROW = /^[ 	]*\|?[ 	:|-]*-{3,}[ 	:|-]*\|/m;

/**
 * A price-ish header is what `parseResultTable` uses to decide a table becomes
 * cards, so this must agree with it: matching any table would also catch a
 * two-column glossary, which stays a plain table.
 */
const PRICE_HEADER = /\|[^|]*(price|fare|total|rate)[^|]*\|/i;

function normaliseName(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "");
}

const Bubble = memo(function Bubble({
  m,
  segments,
  streaming,
}: {
  m: Message;
  /** Live segments for the streaming bubble only. Persisted messages have
   *  none and render from stored `content`, which is what keeps reload
   *  working unchanged. */
  segments?: Segment[];
  streaming?: boolean;
}) {
  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        {/* min-w-0 lets long unbroken strings wrap instead of widening the row. */}
        <div className="max-w-[85%] min-w-0 rounded-2xl rounded-br-md bg-surface-2 px-3.5 py-2.5 text-sm whitespace-pre-wrap break-words sm:max-w-[75%]">
          {m.content}
        </div>
      </div>
    );
  }

  // The live bubble renders ordered segments; everything else (reloaded from
  // the transcript, or a finished turn) renders from stored content.
  //
  // `segments` being present but empty is the very first frames of a turn,
  // before anything has arrived. Render the placeholder immediately rather
  // than an empty bubble — waiting the full idle gap to show anything would
  // read as a hang.
  // The comparison table the agent writes is plain TEXT: no picture, no
  // supplier ids, so no "Show details". The option rows streamed alongside it
  // carry both. Matching them by name is what lets one card show the tour,
  // its image and its details link -- instead of the customer seeing the same
  // tour twice, once as a text card and again as a picture card below.
  //
  // Names are normalised because the agent retypes the name into the table,
  // so it can differ by case, spacing or a trailing marker.
  const optionFor = useMemo(() => {
    const byName = new Map<string, OptionItem>();
    for (const group of m.options ?? []) {
      for (const item of group.items ?? []) {
        const name = typeof item.name === "string" ? item.name : item.hotel_name;
        if (typeof name === "string" && name.trim()) {
          byName.set(normaliseName(name), item);
        }
      }
    }
    if (!byName.size) return undefined;
    return (name: string) => byName.get(normaliseName(name)) ?? null;
  }, [m.options]);

  // When the reply already contains a RESULT comparison, those rows ARE the
  // cards -- they carry the image and the details link via `optionFor`. The
  // option group below would then repeat every tour a second time, which is
  // exactly what the customer saw.
  //
  // The price-ish header is the same test `parseResultTable` uses to decide a
  // table becomes cards. Matching on any table would also catch a two-column
  // glossary, which stays a plain table and leaves the cards to the group.
  const hasResultTable =
    SEPARATOR_ROW.test(m.content ?? "") && PRICE_HEADER.test(m.content ?? "");

  const live = Boolean(segments) && m.pending;
  const shown: Segment[] =
    segments && segments.length === 0 ? [{ type: "thinking" }] : (segments ?? []);

  return (
    <div className="flex justify-start">
      {/* min-w-0 is what keeps a wide table inside its own scroll box. */}
      <div className="w-full min-w-0">
        {live ? (
          <SegmentList segments={shown} streaming={Boolean(streaming)} />
        ) : (
          m.content && (
            <>
              <Markdown optionFor={optionFor}>{m.content}</Markdown>
              {m.pending && <span className="caret text-fg-muted">▍</span>}
            </>
          )
        )}

        {!hasResultTable &&
          m.options?.map((g, i) => <OptionCards key={i} group={g} />)}

        {m.error && (
          <p role="alert" className="mt-2 rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-500">
            {m.error}
          </p>
        )}

        {/* No debug panel: the upstream supplier host, endpoint paths and raw
            request/response bodies are internal and must not reach a customer. */}
      </div>
    </div>
  );
});

export function MessageList({
  messages,
  liveId,
  segments,
  streaming,
}: {
  messages: Message[];
  /** Id of the bubble currently streaming, if any. */
  liveId?: string | null;
  segments?: Segment[];
  streaming?: boolean;
}) {
  return (
    <div className="mx-auto flex w-full max-w-3xl min-w-0 flex-col gap-6 px-4 py-6">
      {messages.map((m) => {
        const isLive = Boolean(liveId) && m.id === liveId;
        return (
          <Bubble
            key={m.id}
            m={m}
            segments={isLive ? segments : undefined}
            streaming={isLive ? streaming : false}
          />
        );
      })}
    </div>
  );
}
