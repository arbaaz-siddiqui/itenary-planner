"use client";

import { useState } from "react";
import { QUICK_STARTS, serviceById, type ServiceId } from "@/lib/services";

/**
 * Asked once, on the home screen, of accounts made before sign-up collected a
 * name. The greeting uses the first name.
 */
function NamePrompt({ onSave }: { onSave: (first: string, last: string) => Promise<void> }) {
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const field =
    "min-w-0 flex-1 rounded-full border border-border bg-surface px-3.5 py-2 text-sm text-fg outline-none placeholder:text-fg-muted focus:border-brand-blue";

  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        if (!first.trim()) return;
        setSaving(true);
        setError(null);
        try {
          await onSave(first.trim(), last.trim());
        } catch {
          setError("Could not save your name. Please try again.");
          setSaving(false);
        }
      }}
      className="mx-auto mb-6 max-w-[560px] rounded-2xl border border-border bg-surface/85 p-3 backdrop-blur"
    >
      <p className="mb-2 px-1 text-sm text-fg">What should we call you?</p>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          value={first}
          onChange={(e) => setFirst(e.target.value)}
          placeholder="First name"
          aria-label="First name"
          autoComplete="given-name"
          maxLength={80}
          required
          className={field}
        />
        <input
          value={last}
          onChange={(e) => setLast(e.target.value)}
          placeholder="Last name"
          aria-label="Last name"
          autoComplete="family-name"
          maxLength={80}
          className={field}
        />
        <button
          type="submit"
          disabled={!first.trim() || saving}
          className="rounded-full bg-brand-blue px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-blue-dark disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
      {error && <p className="mt-2 px-1 text-xs text-brand-red">{error}</p>}
    </form>
  );
}

// On the gradient, controls sit on a dark (or, in light theme, white) veil
// so text keeps its contrast wherever the colour behind it is brightest.
const ON_GLOW =
  "border-white/15 bg-black/35 text-white hover:border-white/35 hover:bg-black/50 " +
  "[[data-theme=light]_&]:border-black/10 [[data-theme=light]_&]:bg-white/70 [[data-theme=light]_&]:text-fg [[data-theme=light]_&]:hover:bg-white";

/**
 * The empty-chat screen: greeting, the big composer (passed in as children)
 * and quick-start chips. Picking a service in the sidebar swaps the chips for
 * that service's starter prompts.
 */
export function HomeHero({
  name,
  needsName,
  onSaveName,
  service,
  onPick,
  children,
}: {
  name: string;
  needsName: boolean;
  onSaveName: (first: string, last: string) => Promise<void>;
  service: ServiceId | null;
  onPick: (prompt: string) => void;
  children: React.ReactNode;
}) {
  const svc = serviceById(service);
  const chips = svc
    ? svc.starters.map((text) => ({ text, Icon: svc.icon }))
    : QUICK_STARTS.map((q) => ({ text: q.text, Icon: serviceById(q.service)!.icon }));

  return (
    // A rounded panel inset from the sidebar, as in the reference layout.
    <div className="flex min-h-full flex-col p-2">
      <div className="hero-glow hero-bloom flex flex-1 flex-col items-center justify-center overflow-hidden rounded-[22px] px-4 py-16">
        <div className="w-full max-w-[720px]">
          {needsName && <NamePrompt onSave={onSaveName} />}

          <div className="hero-rise mb-5 flex justify-center">
            <span className={`inline-flex items-center gap-2 rounded-full border py-1 pl-1 pr-3 text-xs backdrop-blur ${ON_GLOW}`}>
              <span className="rounded-full bg-brand-blue px-2 py-0.5 text-[11px] font-semibold text-white">New</span>
              Live GujjuTours prices in every answer
            </span>
          </div>

          <h2
            className="hero-rise mb-8 text-balance text-center text-[34px] font-semibold leading-[1.1] tracking-[-0.03em] text-white sm:text-[44px] [[data-theme=light]_&]:text-fg"
            style={{ "--rise-delay": "60ms" } as React.CSSProperties}
          >
            {svc ? `${svc.label}: what do you need?` : <>Where to next{name ? `, ${name}` : ""}?</>}
          </h2>

          <div className="hero-rise" style={{ "--rise-delay": "120ms" } as React.CSSProperties}>
            {children}
          </div>

          <div
            className="hero-rise mt-6 flex flex-wrap justify-center gap-2"
            style={{ "--rise-delay": "180ms" } as React.CSSProperties}
            aria-label="Suggestions"
          >
            {chips.map(({ text, Icon }) => (
              <button
                key={text}
                type="button"
                onClick={() => onPick(text)}
                className={`press inline-flex items-center gap-2 rounded-full border px-3.5 py-2 text-sm backdrop-blur focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white ${ON_GLOW}`}
              >
                <Icon size={15} strokeWidth={1.75} aria-hidden />
                {text}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
