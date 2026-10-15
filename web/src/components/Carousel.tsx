"use client";

import { useRef } from "react";

/**
 * Horizontal carousel for tour cards.
 *
 * A grid left a lone card stretched across three columns, and a 17-tour
 * search became a wall the customer had to scroll past to reach the reply.
 * Two cards at a time with arrows keeps the row compact and comparable.
 *
 * Scrolling is native (`snap-x` + `overflow-x-auto`), so touch and trackpad
 * work with no JS; the arrows only nudge `scrollLeft` for mouse users.
 */
export function Carousel({ children, count }: { children: React.ReactNode; count: number }) {
  const ref = useRef<HTMLDivElement>(null);

  const nudge = (dir: 1 | -1) => {
    const el = ref.current;
    if (!el) return;
    // One "page" is whatever is on screen, so the arrows always advance by
    // the number of cards actually visible at this width.
    el.scrollBy({ left: dir * el.clientWidth, behavior: "smooth" });
  };

  // A single card has nothing to scroll to: render it plainly, at card width
  // rather than stretched across the row. The child is still a carousel slide
  // (`w-[85%] shrink-0`), and a percentage width inside a shrink-to-fit box
  // collapses to the content width -- which rendered a lone result as a ~90px
  // column. `w-full [&>*]:w-full` gives the slide a real basis to resolve
  // against and overrides its own percentage.
  if (count <= 1) {
    return <div className="w-full max-w-sm [&>*]:w-full">{children}</div>;
  }

  return (
    <div className="relative">
      <div
        ref={ref}
        className="flex snap-x snap-mandatory gap-3 overflow-x-auto scroll-smooth pb-1
                   [scrollbar-width:thin]"
      >
        {children}
      </div>

      {/* Arrows sit over the photo band at the top of the cards: centred on
          the row they covered the card's name and rating. */}
      <button
        type="button"
        onClick={() => nudge(-1)}
        aria-label="Previous"
        className="absolute top-24 left-1 hidden -translate-y-1/2 rounded-full border
                   border-border bg-bg/90 p-1.5 shadow-sm hover:bg-surface-2 sm:block"
      >
        ‹
      </button>
      <button
        type="button"
        onClick={() => nudge(1)}
        aria-label="Next"
        className="absolute top-24 right-1 hidden -translate-y-1/2 rounded-full border
                   border-border bg-bg/90 p-1.5 shadow-sm hover:bg-surface-2 sm:block"
      >
        ›
      </button>
    </div>
  );
}

