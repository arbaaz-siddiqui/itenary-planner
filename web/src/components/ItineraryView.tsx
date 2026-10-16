"use client";

import { BedDouble, CarFront, ChevronDown, Map as MapIcon, Plane, Sun, UtensilsCrossed, type LucideIcon } from "lucide-react";
import { useState } from "react";
import type { CardRef } from "@/lib/cardActions";
import { useCardActions } from "@/lib/cardActions";
import { formatInr } from "@/lib/cards";
import { dayHeading, type Itinerary, type StepKind } from "@/lib/itinerary";
import { SafeImage } from "./SafeImage";

const ICON: Record<StepKind, LucideIcon> = {
  flight: Plane,
  transfer: CarFront,
  hotel: BedDouble,
  tour: MapIcon,
  meal: UtensilsCrossed,
  free: Sun,
  other: Sun,
};

// One colour per kind of step, so a day reads at a glance.
const TONE: Record<StepKind, string> = {
  flight: "bg-brand-blue/15 text-brand-blue",
  transfer: "bg-violet-500/15 text-violet-400",
  hotel: "bg-amber-500/15 text-amber-500",
  tour: "bg-brand-red/15 text-brand-red",
  meal: "bg-success/15 text-success",
  free: "bg-surface-2 text-fg-muted",
  other: "bg-surface-2 text-fg-muted",
};

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

/**
 * The planned trip, day by day and time by time, with every paid part's own
 * price and the total. Tours open their card when one was found for them.
 */
export function ItineraryView({ plan, cards }: { plan: Itinerary; cards: CardRef[] }) {
  const { open } = useCardActions();
  const [showCosts, setShowCosts] = useState(true);
  const byName = new Map(cards.map((c) => [norm(String(c.item.name ?? c.item.hotel_name ?? "")), c]));
  const party = plan.adults ? `${plan.adults} adult${plan.adults === 1 ? "" : "s"}` : "";

  return (
    <section className="card-in mt-3 overflow-hidden rounded-2xl border border-border bg-surface" aria-label="Your itinerary">
      <header className="flex flex-wrap items-end justify-between gap-3 border-b border-border bg-gradient-to-r from-brand-blue/15 via-transparent to-brand-red/10 px-4 py-4 sm:px-5">
        <div className="min-w-0">
          <p className="text-xs font-medium text-fg-muted">Your Dubai itinerary</p>
          <h3 className="font-display text-lg font-semibold tracking-tight text-fg">
            {dayHeading(plan.startDate)} to {dayHeading(plan.endDate)}
          </h3>
          <p className="text-sm text-fg-muted">
            {[`${plan.nights} night${plan.nights === 1 ? "" : "s"}`, party].filter(Boolean).join(", ")}
          </p>
        </div>
        {plan.total != null && (
          <div className="sm:text-right">
            <p className="text-xs text-fg-muted">Total{party ? ` for ${party}` : ""}</p>
            <p className="font-display text-2xl font-semibold tracking-tight tabular-nums text-fg">{formatInr(plan.total)}</p>
          </div>
        )}
      </header>

      <ol className="divide-y divide-border">
        {plan.days.map((day) => (
          <li key={day.number} className="px-4 py-4 sm:px-5">
            <div className="mb-3 flex items-center gap-2">
              <span className="rounded-full bg-fg px-2.5 py-0.5 text-xs font-semibold text-bg">Day {day.number}</span>
              <span className="text-sm font-medium text-fg">{dayHeading(day.date)}</span>
              {day.label && <span className="text-xs text-fg-muted">{day.label}</span>}
            </div>
            <ol className="relative ml-1 space-y-3 border-l border-border pl-5">
              {day.steps.map((st, i) => {
                const Icon = ICON[st.kind];
                const card = st.kind === "tour" ? byName.get(norm(st.title)) : undefined;
                const image = card ? (card.item.image_url as string | undefined) : undefined;
                const body = (
                  <div className="flex min-w-0 flex-1 items-start gap-3">
                    {image && (
                      <div className="hidden w-16 shrink-0 overflow-hidden rounded-lg sm:block">
                        <SafeImage src={image} alt="" ratio="aspect-[4/3]" icon={MapIcon} />
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="text-[15px] leading-snug font-medium text-fg">{st.title}</p>
                      {st.detail && <p className="mt-0.5 text-xs leading-relaxed text-fg-muted">{st.detail}</p>}
                    </div>
                    <div className="shrink-0 text-right">
                      {st.price != null ? (
                        <span className="text-sm font-semibold tabular-nums text-fg">{formatInr(st.price)}</span>
                      ) : st.included ? (
                        <span className="rounded-full bg-success/15 px-2 py-0.5 text-[11px] font-medium text-success">Included</span>
                      ) : null}
                    </div>
                  </div>
                );
                return (
                  <li key={i} className="relative">
                    <span
                      className={`absolute top-0.5 -left-[33px] grid h-6 w-6 place-items-center rounded-full ring-4 ring-surface ${TONE[st.kind]}`}
                      aria-hidden
                    >
                      <Icon size={13} strokeWidth={2} />
                    </span>
                    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
                      <span className="shrink-0 pt-0.5 text-xs font-medium tabular-nums text-fg-muted sm:w-[88px]">
                        {st.start ? `${st.start}${st.end ? `-${st.end}` : ""}` : ""}
                      </span>
                      {card ? (
                        <button
                          type="button"
                          onClick={() => open(card)}
                          className="press flex min-w-0 flex-1 rounded-lg text-left hover:bg-surface-2/60 focus-visible:outline-2 focus-visible:outline-brand-blue"
                        >
                          {body}
                        </button>
                      ) : (
                        body
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
          </li>
        ))}
      </ol>

      {plan.lines.length > 0 && (
        <div className="border-t border-border bg-bg/40 px-4 py-4 sm:px-5">
          <button
            type="button"
            onClick={() => setShowCosts((v) => !v)}
            aria-expanded={showCosts}
            className="flex w-full items-center justify-between text-sm font-semibold text-fg"
          >
            What the total includes
            <ChevronDown size={16} aria-hidden className={`transition-transform ${showCosts ? "rotate-180" : ""}`} />
          </button>
          {showCosts && (
            <ul className="mt-3 space-y-2 text-sm">
              {plan.lines.map((l, i) => (
                <li key={i} className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-fg">{l.item}</p>
                    {l.detail && <p className="text-xs text-fg-muted">{l.detail}</p>}
                  </div>
                  <span className="shrink-0 tabular-nums text-fg">{formatInr(l.amount)}</span>
                </li>
              ))}
              {plan.total != null && (
                <li className="flex items-center justify-between gap-3 border-t border-border pt-2 font-semibold text-fg">
                  <span>Total{party ? ` for ${party}` : ""}</span>
                  <span className="tabular-nums">{formatInr(plan.total)}</span>
                </li>
              )}
              {plan.notes.map((n) => (
                <li key={n} className="text-xs font-medium text-amber-600 dark:text-amber-500">{n}</li>
              ))}
              <li className="pt-1 text-xs text-fg-muted">Meals are paid at the hotel or restaurant and are not in the total.</li>
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
