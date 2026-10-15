"use client";

import { memo, useMemo } from "react";
import { Markdown } from "./Markdown";
import { OptionCards } from "./OptionCards";
import { ItineraryPdf } from "./ItineraryPdf";
import { SegmentList } from "./SegmentList";
import type { Message, OptionItem, Segment } from "@/lib/types";
import { normaliseName, refersTo, sameName, shortenedFrom, tableRowNames } from "@/lib/optionMatch";

/** A GFM table always renders a header separator row. */
const SEPARATOR_ROW = /^[ 	]*\|?[ 	:|-]*-{3,}[ 	:|-]*\|/m;

/**
 * A price-ish header is what `parseResultTable` uses to decide a table becomes
 * cards, so this must agree with it: matching any table would also catch a
 * two-column glossary, which stays a plain table.
 */
const PRICE_HEADER = /\|[^|]*(price|fare|total|rate)[^|]*\|/i;

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
    const fullNames = new Map<string, OptionItem>();
    for (const group of m.options ?? []) {
      for (const item of group.items ?? []) {
        // Each kind names itself differently: tours `name`, hotels
        // `hotel_name`, flights `airline`. Indexing only the first two left a
        // flights-only reply with an empty map, so `optionFor` returned
        // undefined and the table rows lost both image and details link.
        const name = [item.name, item.hotel_name, item.airline].find(
          (v): v is string => typeof v === "string" && v.trim() !== "",
        );
        if (name) {
          byName.set(normaliseName(name), item);
          fullNames.set(name, item);
        }
      }
    }
    if (!byName.size) return undefined;
    return (name: string) => {
      const key = normaliseName(name);
      const exact = byName.get(key);
      if (exact) return exact;
      // The agent shortens long supplier names ("Khandani Rajdhani" for
      // "Khandani Rajdhani - Indian Restaurant in Karama"). Accept a prefix
      // match only when it is unambiguous, so two similar names never swap.
      const hits = [...byName.entries()].filter(([k]) => sameName(k, key));
      if (hits.length === 1) return hits[0][1];
      if (hits.length > 1) return null;
      const short = [...fullNames.entries()].filter(([full]) => shortenedFrom(name, full));
      return short.length === 1 ? short[0][1] : null;
    };
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

  // Only the cards the table actually shows are suppressed. A restaurant's
  // menu is a priced table of DISHES: suppressing every card on "has a
  // result table" hid the restaurant card, and with it every food photo.
  const tableNames = useMemo(() => (hasResultTable ? tableRowNames(m.content ?? "") : []), [
    hasResultTable,
    m.content,
  ]);
  const notInTable = (item: OptionItem): boolean => {
    const name = [item.name, item.hotel_name, item.airline].find(
      (v): v is string => typeof v === "string" && v.trim() !== "",
    );
    return !name || !tableNames.some((row) => refersTo(row, name));
  };

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

        {/* The PDF is not a card and is never suppressed by a result table:
            it is the deliverable the customer asked for. */}
        {m.options
          ?.filter((g) => g.kind === "itinerary_pdf")
          .flatMap((g) => g.items ?? [])
          .slice(0, 1)
          .map((item, i) => <ItineraryPdf key={i} item={item} />)}

        {m.options
          ?.filter((g) => g.kind !== "itinerary_pdf")
          .map((g) => (hasResultTable ? { ...g, items: (g.items ?? []).filter(notInTable) } : g))
          .filter((g) => g.items?.length)
          .map((g, i) => <OptionCards key={i} group={g} />)}

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
