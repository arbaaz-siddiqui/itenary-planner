"use client";

import { useState } from "react";
import { Heart, Star } from "lucide-react";
import type { CardRef } from "@/lib/cardActions";
import { useCardActions } from "@/lib/cardActions";

/*
 * Shape rules for every card, so the seven types read as one family:
 *   cards 16px radius, buttons 10px, chips and pills fully round.
 */

/**
 * The card frame. The whole card opens the detail view; the heart and the
 * book button stop propagation so they do their own job. Rendered as a div
 * with role=button (not a <button>) because it contains other buttons.
 */
export function CardShell({
  card,
  children,
  className = "",
}: {
  card: CardRef;
  children: React.ReactNode;
  className?: string;
}) {
  const { open } = useCardActions();
  return (
    <article
      role="button"
      tabIndex={0}
      aria-label={`Open details`}
      onClick={() => open(card)}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open(card);
        }
      }}
      className={`card-in group/card flex h-full cursor-pointer flex-col overflow-hidden rounded-2xl border border-border bg-surface text-left transition-[box-shadow,border-color] duration-200 hover:border-fg-muted/40 hover:shadow-[0_10px_30px_-12px_rgb(0_0_0/0.45)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-blue ${className}`}
    >
      {children}
    </article>
  );
}

export function HeartButton({ card, className = "" }: { card: CardRef; className?: string }) {
  const { isSaved, toggleSave } = useCardActions();
  const saved = isSaved(card.id);
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        toggleSave(card);
      }}
      aria-pressed={saved}
      aria-label={saved ? "Remove from saved" : "Save"}
      title={saved ? "Saved" : "Save"}
      className={`press grid h-9 w-9 place-items-center rounded-full bg-white/90 text-brand-black shadow-sm backdrop-blur hover:bg-white ${className}`}
    >
      <Heart size={17} strokeWidth={2} className={saved ? "fill-brand-red text-brand-red" : ""} aria-hidden />
    </button>
  );
}

/** Yellow pill with dark text, as on the website, whatever the score. */
export function RatingPill({ value }: { value: number | null }) {
  if (value == null || value <= 0) return null;
  return (
    <span className="inline-flex shrink-0 items-center gap-1 rounded-md bg-rating px-1.5 py-0.5 text-xs font-semibold text-brand-black">
      <Star size={11} className="fill-brand-black" aria-hidden />
      {value.toFixed(1)}
    </span>
  );
}

export function PriceTag({
  value,
  label,
  align = "right",
}: {
  value: string | null;
  label?: string | null;
  align?: "left" | "right";
}) {
  if (!value) return null;
  return (
    <div className={align === "right" ? "text-right" : ""}>
      {label && <p className="text-[11px] leading-tight text-fg-muted">{label}</p>}
      <p className="text-lg leading-tight font-semibold tracking-tight tabular-nums text-fg">{value}</p>
    </div>
  );
}

/** First four tags, the rest behind "+N more" that expands in place. */
export function TagChips({ tags, max = 4 }: { tags: string[]; max?: number }) {
  const [all, setAll] = useState(false);
  if (!tags.length) return null;
  const shown = all ? tags : tags.slice(0, max);
  return (
    <ul className="ui-list flex flex-wrap gap-1.5">
      {shown.map((t) => (
        <li key={t} className="rounded-full border border-brand-blue/25 bg-brand-blue/10 px-2 py-0.5 text-[11px] text-link">
          {t}
        </li>
      ))}
      {!all && tags.length > max && (
        <li>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setAll(true);
            }}
            className="rounded-full border border-border px-2 py-0.5 text-[11px] text-fg-muted hover:text-fg"
          >
            +{tags.length - max} more
          </button>
        </li>
      )}
    </ul>
  );
}

export function BookButton({ card, label = "Book now" }: { card: CardRef; label?: string }) {
  const { book } = useCardActions();
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        book(card);
      }}
      className="press inline-flex h-9 shrink-0 items-center justify-center rounded-[10px] bg-brand-blue px-4 text-sm font-medium whitespace-nowrap text-white hover:bg-brand-blue-dark"
    >
      {label}
    </button>
  );
}

/** One line of secondary facts; empty values drop out, never "undefined". */
export function MetaLine({ items, className = "" }: { items: (string | null | undefined | false)[]; className?: string }) {
  const shown = items.filter((x): x is string => typeof x === "string" && x.trim() !== "");
  if (!shown.length) return null;
  return (
    <p className={`flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-fg-muted ${className}`}>
      {shown.map((s, i) => (
        <span key={i}>{s}</span>
      ))}
    </p>
  );
}
