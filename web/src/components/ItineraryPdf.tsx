"use client";

import { useState } from "react";
import { Download, FileText, Loader2 } from "lucide-react";
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
    <section className="mt-3 flex flex-wrap items-center gap-3 rounded-2xl border border-brand-blue/30 bg-gradient-to-r from-brand-blue/15 via-brand-blue/5 to-brand-red/10 p-3 pr-4">
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-brand-blue/20 text-brand-blue" aria-hidden>
        <FileText size={20} strokeWidth={1.9} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-fg">Your itinerary PDF</p>
        <p className="truncate text-xs text-fg-muted">{filename}</p>
      </div>
      <button
        type="button"
        onClick={save}
        disabled={busy}
        className="press inline-flex items-center gap-2 rounded-full bg-brand-blue px-4 py-2 text-sm font-semibold text-white shadow-[0_8px_22px_-10px_rgba(43,127,200,0.9)] transition-colors hover:bg-brand-blue-dark disabled:opacity-60 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-blue"
      >
        {busy ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <Download size={16} aria-hidden />}
        {busy ? "Preparing" : "Download PDF"}
      </button>
      {error && <p className="basis-full text-xs text-amber-600 dark:text-amber-500">{error}</p>}
    </section>
  );
}
