"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import type { CardRef } from "@/lib/cardActions";
import { imagesOf } from "@/lib/cards";
import { normalizeImageUrl } from "../SafeImage";
import { CARD_FOR } from "./Cards";

/** How many leading photos must be ready before the row is revealed. */
const PRELOAD_FIRST = 3;
/** ...or how long to wait for them before revealing anyway. */
const PRELOAD_TIMEOUT_MS = 2000;

/** Resolves true once the first few photos have loaded or failed, or time ran out. */
function useFirstImagesReady(cards: CardRef[]): boolean {
  const key = cards.map((c) => c.id).join("|");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const urls = cards
      .slice(0, PRELOAD_FIRST)
      .map((c) => normalizeImageUrl(imagesOf(c.item)[0]))
      .filter((u): u is string => Boolean(u));
    if (!urls.length) {
      setReady(true);
      return;
    }
    setReady(false);
    let left = urls.length;
    let done = false;
    const finish = () => {
      if (!done) {
        done = true;
        setReady(true);
      }
    };
    const timer = setTimeout(finish, PRELOAD_TIMEOUT_MS);
    const imgs = urls.map((u) => {
      const img = new Image();
      img.onload = img.onerror = () => {
        left -= 1;
        if (left <= 0) finish();
      };
      img.src = u;
      return img;
    });
    return () => {
      clearTimeout(timer);
      imgs.forEach((i) => (i.onload = i.onerror = null));
    };
    // `key` stands for the card list: a new array with the same cards must
    // not restart the preload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return ready;
}

export function CardSkeleton({ wide = false }: { wide?: boolean }) {
  if (wide) {
    return <div className="skeleton h-28 w-full rounded-2xl" aria-hidden />;
  }
  return (
    <div className="w-[min(78vw,290px)] shrink-0 overflow-hidden rounded-2xl border border-border bg-surface" aria-hidden>
      <div className="skeleton aspect-[3/2]" />
      <div className="space-y-2.5 p-4">
        <div className="skeleton h-4 w-3/4 rounded" />
        <div className="skeleton h-3 w-1/2 rounded" />
        <div className="skeleton h-3 w-2/3 rounded" />
        <div className="flex items-end justify-between pt-3">
          <div className="skeleton h-6 w-20 rounded" />
          <div className="skeleton h-9 w-24 rounded-[10px]" />
        </div>
      </div>
    </div>
  );
}

/** The loading state a card block shows before its data or photos are ready. */
export function CardsLoading({ layout = "carousel", label }: { layout?: "carousel" | "list"; label?: string }) {
  return (
    <div className="my-3" role="status" aria-label={label ?? "Loading results"}>
      {layout === "list" ? (
        <div className="flex flex-col gap-3">
          <CardSkeleton wide />
          <CardSkeleton wide />
        </div>
      ) : (
        <div className="no-scrollbar flex gap-4 overflow-hidden">
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
        </div>
      )}
    </div>
  );
}

/**
 * One card block. Every section uses it; only the card inside changes.
 * Flights render as a vertical list of wide rows; everything else scrolls
 * sideways.
 */
export function CardCarousel({
  cards,
  title,
  layout = "carousel",
}: {
  cards: CardRef[];
  title?: string;
  layout?: "carousel" | "list";
}) {
  const ready = useFirstImagesReady(cards);
  const scroller = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ left: false, right: false });

  const measure = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    const max = el.scrollWidth - el.clientWidth;
    setEdges({ left: el.scrollLeft > 4, right: el.scrollLeft < max - 4 });
  }, []);

  useEffect(() => {
    if (!ready) return;
    measure();
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ready, measure, cards.length]);

  const step = (dir: 1 | -1) => {
    const el = scroller.current;
    const first = el?.firstElementChild as HTMLElement | null;
    if (!el || !first) return;
    // One card plus the gap per click.
    el.scrollBy({ left: dir * (first.offsetWidth + 16), behavior: "smooth" });
  };

  if (!cards.length) return null;
  if (!ready) return <CardsLoading layout={layout} />;

  const header = title && (
    <div className="mb-2.5 flex items-baseline justify-between gap-3">
      <h3 className="text-sm font-semibold text-fg">{title}</h3>
      <p className="text-xs text-fg-muted">
        {cards.length} result{cards.length === 1 ? "" : "s"}
      </p>
    </div>
  );

  if (layout === "list") {
    return (
      <section className="my-3">
        {header}
        <div className="flex flex-col gap-3">
          {cards.map((c, i) => {
            const Card = CARD_FOR[c.kind];
            return (
              <div key={c.id} style={{ "--i": i } as React.CSSProperties}>
                <Card card={c} eager={i < PRELOAD_FIRST} />
              </div>
            );
          })}
        </div>
      </section>
    );
  }

  // The edge with more cards behind it fades out, hinting there is more.
  const mask =
    edges.left && edges.right
      ? "[mask-image:linear-gradient(to_right,transparent,black_40px,black_calc(100%-40px),transparent)]"
      : edges.right
        ? "[mask-image:linear-gradient(to_right,black_calc(100%-48px),transparent)]"
        : edges.left
          ? "[mask-image:linear-gradient(to_right,transparent,black_48px)]"
          : "";

  const arrow =
    "press absolute top-[38%] z-10 hidden h-10 w-10 -translate-y-1/2 place-items-center rounded-full border border-border bg-surface/95 text-fg shadow-lg backdrop-blur transition-opacity hover:bg-surface-2 disabled:pointer-events-none disabled:opacity-0 sm:grid";

  return (
    <section className="my-3">
      {header}
      <div className="relative">
        <div
          ref={scroller}
          onScroll={measure}
          tabIndex={0}
          role="region"
          aria-label={title ?? "Results"}
          onKeyDown={(e) => {
            if (e.key === "ArrowRight") {
              e.preventDefault();
              step(1);
            }
            if (e.key === "ArrowLeft") {
              e.preventDefault();
              step(-1);
            }
          }}
          className={`no-scrollbar flex snap-x snap-mandatory gap-4 overflow-x-auto scroll-smooth pb-2 outline-none focus-visible:ring-2 focus-visible:ring-brand-blue/50 ${mask}`}
        >
          {cards.map((c, i) => {
            const Card = CARD_FOR[c.kind];
            return (
              <div
                key={c.id}
                className="flex w-[min(78vw,290px)] shrink-0 snap-start"
                style={{ "--i": i } as React.CSSProperties}
              >
                <div className="w-full">
                  <Card card={c} eager={i < PRELOAD_FIRST} />
                </div>
              </div>
            );
          })}
        </div>
        <button type="button" onClick={() => step(-1)} disabled={!edges.left} aria-label="Previous" className={`${arrow} -left-3`}>
          <ChevronLeft size={20} aria-hidden />
        </button>
        <button type="button" onClick={() => step(1)} disabled={!edges.right} aria-label="Next" className={`${arrow} -right-3`}>
          <ChevronRight size={20} aria-hidden />
        </button>
      </div>
    </section>
  );
}
