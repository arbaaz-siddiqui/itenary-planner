"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

/**
 * Sideways strip for the agent's text-only result cards (rows with no
 * supplier data behind them). Same behaviour as the photo carousel: hidden
 * scrollbar, snapping, arrows that disable at either end.
 */
export function Carousel({ children, count }: { children: React.ReactNode; count: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ left: false, right: false });

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    setEdges({ left: el.scrollLeft > 4, right: el.scrollLeft < el.scrollWidth - el.clientWidth - 4 });
  }, []);

  useEffect(() => {
    measure();
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [measure, count]);

  if (count <= 1) return <div className="w-full max-w-sm [&>*]:w-full">{children}</div>;

  const nudge = (dir: 1 | -1) => ref.current?.scrollBy({ left: dir * ref.current.clientWidth * 0.8, behavior: "smooth" });
  const arrow =
    "press absolute top-1/2 z-10 hidden h-9 w-9 -translate-y-1/2 place-items-center rounded-full border border-border bg-surface/95 text-fg shadow-lg hover:bg-surface-2 disabled:pointer-events-none disabled:opacity-0 sm:grid";

  return (
    <div className="relative">
      <div ref={ref} onScroll={measure} className="no-scrollbar flex snap-x snap-mandatory gap-3 overflow-x-auto scroll-smooth pb-1">
        {children}
      </div>
      <button type="button" onClick={() => nudge(-1)} disabled={!edges.left} aria-label="Previous" className={`${arrow} -left-3`}>
        <ChevronLeft size={18} aria-hidden />
      </button>
      <button type="button" onClick={() => nudge(1)} disabled={!edges.right} aria-label="Next" className={`${arrow} -right-3`}>
        <ChevronRight size={18} aria-hidden />
      </button>
    </div>
  );
}
