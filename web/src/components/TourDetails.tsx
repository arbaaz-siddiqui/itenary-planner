"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { TourDetailSection } from "@/lib/types";

/**
 * "Show details" panel for one tour variant.
 *
 * The supplier publishes inclusions, exclusions and the cancellation/child
 * policies on a separate endpoint, one call per variant. That is why this
 * fetches on OPEN rather than with the search: a 17-tour result would
 * otherwise cost 17 extra supplier calls for panels nobody opened.
 *
 * The API returns the sections as plain text, already split into bullets. The
 * supplier's own `descriptionText` is HTML and is deliberately NOT forwarded
 * to the browser -- rendering unsanitised third-party markup on our origin is
 * an injection risk for a panel that only ever shows a list.
 */
export function TourDetails({
  tourId,
  optionId,
  supplierId,
  name,
}: {
  tourId: number;
  optionId: string | number;
  supplierId: number;
  name: string;
}) {
  const [open, setOpen] = useState(false);
  const [sections, setSections] = useState<TourDetailSection[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open || sections || loading) return;
    let live = true;
    setLoading(true);
    setError(null);

    // A request that never settles would leave the panel on "Loading details…"
    // forever, which is what a missing proxy rewrite produced. Fail visibly
    // instead, so the customer can close and retry.
    const timeout = setTimeout(() => {
      if (!live) return;
      setLoading(false);
      setError("Details are taking too long to load. Please try again.");
    }, 20_000);

    api
      .tourOptionDetails(tourId, optionId, supplierId)
      .then((r) => {
        if (live) setSections(r.sections ?? []);
      })
      .catch((e: unknown) => {
        if (live) setError(e instanceof Error ? e.message : "Could not load details.");
      })
      .finally(() => {
        clearTimeout(timeout);
        if (live) setLoading(false);
      });
    return () => {
      live = false;
      clearTimeout(timeout);
    };
  }, [open, sections, loading, tourId, optionId, supplierId]);

  // Escape closes, and focus moves to the dialog so a keyboard user is not
  // left behind on the card underneath.
  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="text-xs font-medium text-accent underline-offset-2 hover:underline"
      >
        Show details
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="dialog"
          aria-modal="true"
          aria-label={`Details for ${name}`}
          onClick={(e) => {
            if (e.target === e.currentTarget) setOpen(false);
          }}
        >
          <div className="flex max-h-[80vh] w-full max-w-lg flex-col overflow-hidden rounded-xl bg-bg shadow-xl">
            <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
              <h3 className="text-sm font-semibold">{name}</h3>
              <button
                ref={closeRef}
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close"
                className="shrink-0 rounded p-1 text-fg-muted hover:bg-surface-2"
              >
                ✕
              </button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
              {loading && <p className="text-xs text-fg-muted">Loading details…</p>}
              {error && (
                <p role="alert" className="text-xs text-red-500">
                  {error}
                </p>
              )}
              {sections?.length === 0 && !loading && (
                <p className="text-xs text-fg-muted">
                  The supplier has not published details for this option.
                </p>
              )}
              {sections?.map((s, i) => (
                <section key={i} className={i ? "mt-4" : undefined}>
                  <h4 className="text-xs font-semibold uppercase tracking-wide text-fg-muted">
                    {s.title}
                  </h4>
                  {s.items.length > 1 ? (
                    <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs leading-relaxed">
                      {s.items.map((it, j) => (
                        <li key={j}>{it}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-1.5 text-xs leading-relaxed">
                      {s.items[0] ?? s.summary}
                    </p>
                  )}
                </section>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
