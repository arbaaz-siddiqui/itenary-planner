"use client";

import { useState } from "react";
import type { OptionItem } from "@/lib/types";

const str = (o: OptionItem, k: string): string | null => {
  const v = o[k];
  return typeof v === "string" && v.trim() ? v : null;
};

/**
 * Download button for a generated itinerary PDF.
 *
 * Fetched and saved via a blob rather than a plain `<a download>`: the file is
 * served by the API host, and a cross-origin `download` attribute is ignored,
 * so the browser navigated to the PDF instead of saving it. Fetching also lets
 * a 404 (an id whose file was lost with the container's disk) surface as a
 * message instead of an empty tab.
 */
export function ItineraryPdf({ item }: { item: OptionItem }) {
  const id = str(item, "itinerary_id");
  const filename = str(item, "filename") ?? "itinerary.pdf";
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!id) return null;
  // Same-origin: the Next rewrite forwards /itinerary/* to the API, so this
  // works on localhost and in production without hardcoding either host.
  const href = `/itinerary/${id}.pdf`;

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(href);
      if (!res.ok) {
        throw new Error(
          res.status === 404
            ? "This PDF is no longer stored. Ask me to generate the itinerary PDF again."
            : "The PDF could not be downloaded. Please try again.",
        );
      }
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not download the PDF.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-3">
      <button
        type="button"
        onClick={save}
        disabled={busy}
        className="inline-flex items-center gap-2 rounded-lg border border-border
                   bg-surface px-3 py-2 text-sm font-medium hover:bg-surface-2
                   disabled:opacity-60"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <path d="M7 10l5 5 5-5" />
          <path d="M12 15V3" />
        </svg>
        {busy ? "Preparing…" : "Download itinerary PDF"}
      </button>
      {error && <p className="mt-1.5 text-xs text-amber-700 dark:text-amber-500">{error}</p>}
    </section>
  );
}
