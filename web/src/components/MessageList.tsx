"use client";

import { memo, useMemo, useState } from "react";
import { Check, Copy, CornerDownRight, Pencil, RotateCcw, ThumbsDown, ThumbsUp } from "lucide-react";
import { Markdown } from "./Markdown";
import { ItineraryPdf } from "./ItineraryPdf";
import { SegmentList } from "./SegmentList";
import { CardCarousel } from "./cards/CardCarousel";
import type { Message, OptionItem, Segment } from "@/lib/types";
import { KIND_LABEL } from "@/lib/cards";
import { KIND_TAG, toCardRefs } from "@/lib/cardRefs";
import { normaliseName, refersTo, sameName, shortenedFrom, tableRowNames } from "@/lib/optionMatch";

/** A GFM table always renders a header separator row. */
const SEPARATOR_ROW = /^[ \t]*\|?[ \t:|-]*-{3,}[ \t:|-]*\|/m;

/** Groups that ride the options channel but are not cards. */
const NOT_CARDS = new Set(["itinerary_pdf", "suggestions"]);

const actionBtn =
  "press grid h-8 w-8 place-items-center rounded-lg text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-2 focus-visible:outline-brand-blue";

function AssistantActions({
  m,
  canRegenerate,
  onRegenerate,
  onRate,
}: {
  m: Message;
  canRegenerate: boolean;
  onRegenerate: () => void;
  onRate: (id: string, value: "up" | "down" | null) => Promise<void>;
}) {
  const [copied, setCopied] = useState(false);
  const [vote, setVote] = useState<"up" | "down" | null>(m.feedback ?? null);
  const [rateError, setRateError] = useState(false);
  // A reply still carrying its client id has not been saved yet: nothing to rate.
  const saved = !m.id.startsWith("local-");

  const rate = async (v: "up" | "down") => {
    const next = vote === v ? null : v;
    const before = vote;
    setVote(next);
    setRateError(false);
    try {
      await onRate(m.id, next);
    } catch {
      setVote(before);
      setRateError(true);
    }
  };

  return (
    <div className="mt-2 flex items-center gap-0.5">
      <button
        type="button"
        aria-label={copied ? "Copied" : "Copy reply"}
        title={copied ? "Copied" : "Copy"}
        className={actionBtn}
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(m.content);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          } catch {
            /* clipboard blocked: nothing to do */
          }
        }}
      >
        {copied ? <Check size={15} className="text-success" aria-hidden /> : <Copy size={15} aria-hidden />}
      </button>
      {canRegenerate && (
        <button type="button" aria-label="Regenerate reply" title="Regenerate" className={actionBtn} onClick={onRegenerate}>
          <RotateCcw size={15} aria-hidden />
        </button>
      )}
      <button
        type="button"
        aria-label="Good reply"
        aria-pressed={vote === "up"}
        title="Good reply"
        className={`${actionBtn} ${vote === "up" ? "text-success" : ""}`}
        disabled={!saved}
        onClick={() => rate("up")}
      >
        <ThumbsUp size={15} aria-hidden />
      </button>
      <button
        type="button"
        aria-label="Bad reply"
        aria-pressed={vote === "down"}
        title="Bad reply"
        className={`${actionBtn} ${vote === "down" ? "text-brand-red" : ""}`}
        disabled={!saved}
        onClick={() => rate("down")}
      >
        <ThumbsDown size={15} aria-hidden />
      </button>
      {copied && <span className="ml-1 text-xs text-success" role="status">Copied</span>}
      {vote && !rateError && <span className="ml-1 text-xs text-fg-muted" role="status">Thanks for the feedback</span>}
      {rateError && <span className="ml-1 text-xs text-brand-red" role="alert">Not saved. Try again.</span>}
    </div>
  );
}

const Bubble = memo(function Bubble({
  m,
  segments,
  streaming,
  isLastAssistant,
  isLastUser,
  busy,
  onSuggest,
  onRegenerate,
  onEdit,
  onRate,
}: {
  m: Message;
  /** Live segments for the streaming bubble only. Persisted messages render
   *  from stored `content`, which is what keeps reload working unchanged. */
  segments?: Segment[];
  streaming?: boolean;
  isLastAssistant: boolean;
  isLastUser: boolean;
  /** A reply is streaming somewhere: actions that send are held back. */
  busy: boolean;
  onSuggest: (text: string) => void;
  onRegenerate: () => void;
  onEdit: (text: string) => void;
  onRate: (id: string, value: "up" | "down" | null) => Promise<void>;
}) {
  // Lookup from a table row's name to the result behind it, tagged with its
  // group's kind so the row can become the right card.
  const optionFor = useMemo(() => {
    const byName = new Map<string, OptionItem[]>();
    const fullNames = new Map<string, OptionItem[]>();
    const add = (map: Map<string, OptionItem[]>, key: string, item: OptionItem) =>
      map.set(key, [...(map.get(key) ?? []), item]);
    for (const group of m.options ?? []) {
      if (NOT_CARDS.has(group.kind)) continue;
      for (const raw of group.items ?? []) {
        const item: OptionItem = { ...raw, [KIND_TAG]: group.kind };
        // Each kind names itself differently: tours `name`, hotels
        // `hotel_name`, flights `airline`, transfers `vehicle_name`.
        const name = [item.name, item.hotel_name, item.airline, item.vehicle_name, item.visa_type].find(
          (v): v is string => typeof v === "string" && v.trim() !== "",
        );
        if (name) {
          add(byName, normaliseName(name), item);
          add(fullNames, name, item);
        }
      }
    }
    if (!byName.size) return undefined;

    const PRICE_KEYS = ["price_inr", "price_per_adult_inr", "per_night_inr", "price_per_person_inr", "total_inr"];
    const pick = (cands: OptionItem[], hint?: { price?: string | null; nth?: number }) => {
      if (cands.length === 1) return cands[0];
      const wanted = Number((hint?.price ?? "").replace(/[^0-9.]/g, ""));
      if (wanted > 0) {
        const byPrice = cands.filter((c) =>
          PRICE_KEYS.some((k) => typeof c[k] === "number" && Math.abs((c[k] as number) - wanted) < 1),
        );
        if (byPrice.length) return byPrice[Math.min(hint?.nth ?? 0, byPrice.length - 1)];
      }
      return cands[Math.min(hint?.nth ?? 0, cands.length - 1)];
    };

    return (name: string, hint?: { price?: string | null; nth?: number }) => {
      const key = normaliseName(name);
      const exact = byName.get(key);
      if (exact) return pick(exact, hint);
      // The agent shortens long supplier names. Accept a prefix match only
      // when it is unambiguous, so two similar names never swap.
      const hits = [...byName.entries()].filter(([k]) => sameName(k, key));
      if (hits.length === 1) return pick(hits[0][1], hint);
      if (hits.length > 1) return null;
      const short = [...fullNames.entries()].filter(([full]) => shortenedFrom(name, full));
      return short.length === 1 ? pick(short[0][1], hint) : null;
    };
  }, [m.options]);

  if (m.role === "user") {
    return (
      <div className="group flex items-start justify-end gap-1">
        {isLastUser && !busy && (
          <button
            type="button"
            onClick={() => onEdit(m.content)}
            aria-label="Edit message"
            title="Edit"
            className={`${actionBtn} mt-1 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100`}
          >
            <Pencil size={14} aria-hidden />
          </button>
        )}
        {/* min-w-0 lets long unbroken strings wrap instead of widening the row. */}
        <div className="max-w-[85%] min-w-0 rounded-2xl rounded-br-md bg-brand-blue px-4 py-2.5 text-[15px] whitespace-pre-wrap break-words text-white sm:max-w-[75%]">
          {m.content}
        </div>
      </div>
    );
  }

  // When the reply already contains a result comparison, those rows ARE the
  // cards; the group below would repeat each result a second time.
  // Any table can carry the results now (a package list has no price
  // column), so rows it names are not drawn a second time below it. Only the
  // named ones: a menu table names dishes, never the restaurant's own card.
  const hasResultTable = SEPARATOR_ROW.test(m.content ?? "");
  const tableNames = hasResultTable ? tableRowNames(m.content ?? "") : [];
  const notInTable = (item: OptionItem): boolean => {
    const name = [item.name, item.hotel_name, item.airline, item.vehicle_name, item.visa_type].find(
      (v): v is string => typeof v === "string" && v.trim() !== "",
    );
    return !name || !tableNames.some((row) => refersTo(row, name));
  };

  const live = Boolean(segments) && m.pending;
  const shown: Segment[] = segments && segments.length === 0 ? [{ type: "thinking" }] : (segments ?? []);

  const suggestions = (m.options ?? [])
    .filter((g) => g.kind === "suggestions")
    .flatMap((g) => g.items ?? [])
    .map((i) => (typeof i.text === "string" ? i.text : ""))
    .filter(Boolean);

  return (
    <div className="flex justify-start">
      {/* min-w-0 is what keeps a wide table inside its own scroll box. */}
      <div className="w-full min-w-0">
        {live ? (
          <SegmentList segments={shown} streaming={Boolean(streaming)} />
        ) : (
          m.content && <Markdown optionFor={optionFor}>{m.content}</Markdown>
        )}

        {/* The PDF is not a card and is never suppressed by a result table:
            it is the deliverable the customer asked for. */}
        {m.options
          ?.filter((g) => g.kind === "itinerary_pdf")
          .flatMap((g) => g.items ?? [])
          .slice(0, 1)
          .map((item, i) => <ItineraryPdf key={i} item={item} />)}

        {/* Cards arrive complete, after the text: never half-rendered. */}
        {!live &&
          m.options
            ?.filter((g) => !NOT_CARDS.has(g.kind))
            .map((g) => ({ g, items: (g.items ?? []).filter((it) => !hasResultTable || notInTable(it)) }))
            .filter(({ items }) => items.length)
            .map(({ g, items }, i) => {
              const cards = toCardRefs(items, g.kind);
              const kind = cards[0].kind;
              return (
                <CardCarousel
                  key={i}
                  cards={cards}
                  title={KIND_LABEL[kind]}
                  layout={kind === "flight" ? "list" : "carousel"}
                />
              );
            })}

        {m.error && (
          <div role="alert" className="mt-3 flex items-center gap-3 rounded-xl border border-brand-red/30 bg-brand-red/10 px-3.5 py-2.5 text-sm text-fg">
            <span className="min-w-0 flex-1">{m.error}</span>
            {isLastAssistant && !busy && (
              <button
                type="button"
                onClick={onRegenerate}
                className="press inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-brand-red px-3 py-1.5 text-xs font-medium text-white"
              >
                <RotateCcw size={13} aria-hidden />
                Retry
              </button>
            )}
          </div>
        )}

        {!m.pending && m.content && (
          <AssistantActions m={m} canRegenerate={isLastAssistant && !busy} onRegenerate={onRegenerate} onRate={onRate} />
        )}

        {isLastAssistant && !busy && suggestions.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2" aria-label="Suggested next steps">
            {suggestions.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => onSuggest(s)}
                className="press inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-3.5 py-1.5 text-sm text-fg hover:border-brand-blue hover:text-link"
              >
                <CornerDownRight size={14} className="text-brand-red" aria-hidden />
                {s}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
});

export function MessageList({
  messages,
  liveId,
  segments,
  streaming,
  onSuggest,
  onRegenerate,
  onEdit,
  onRate,
}: {
  messages: Message[];
  /** Id of the bubble currently streaming, if any. */
  liveId?: string | null;
  segments?: Segment[];
  streaming?: boolean;
  onSuggest: (text: string) => void;
  onRegenerate: () => void;
  onEdit: (text: string) => void;
  onRate: (id: string, value: "up" | "down" | null) => Promise<void>;
}) {
  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant")?.id;
  const lastUser = [...messages].reverse().find((m) => m.role === "user")?.id;
  return (
    <div className="mx-auto flex w-full max-w-[820px] min-w-0 flex-col gap-7 px-4 py-8">
      {messages.map((m) => {
        const isLive = Boolean(liveId) && m.id === liveId;
        return (
          <Bubble
            key={m.id}
            m={m}
            segments={isLive ? segments : undefined}
            streaming={isLive ? streaming : false}
            isLastAssistant={m.id === lastAssistant}
            isLastUser={m.id === lastUser}
            busy={Boolean(streaming)}
            onSuggest={onSuggest}
            onRegenerate={onRegenerate}
            onEdit={onEdit}
            onRate={onRate}
          />
        );
      })}
    </div>
  );
}
