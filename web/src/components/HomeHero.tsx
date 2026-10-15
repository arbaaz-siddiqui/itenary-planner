"use client";

import { Sparkles } from "lucide-react";
import { QUICK_STARTS, serviceById, type ServiceId } from "@/lib/services";

/**
 * The empty-chat screen: greeting, the big composer (passed in as children)
 * and quick-start chips. Picking a service in the sidebar swaps the chips for
 * that service's starter prompts.
 */
export function HomeHero({
  name,
  service,
  onPick,
  children,
}: {
  name: string;
  service: ServiceId | null;
  onPick: (prompt: string) => void;
  children: React.ReactNode;
}) {
  const svc = serviceById(service);
  const chips = svc ? svc.starters : QUICK_STARTS;

  return (
    <div className="hero-glow flex min-h-full flex-col items-center justify-center px-4 py-12">
      <div className="w-full max-w-[720px]">
        <div className="mb-5 flex justify-center">
          <span className="inline-flex items-center gap-2 rounded-full border border-border bg-surface/70 py-1 pl-1 pr-3 text-xs text-fg backdrop-blur">
            <span className="rounded-full bg-brand-blue px-2 py-0.5 text-[11px] font-semibold text-white">New</span>
            Live GujjuTours prices for flights, hotels, tours and more
          </span>
        </div>
        <h2 className="mb-7 text-center text-[32px] font-semibold leading-tight tracking-tight text-fg sm:text-[40px]">
          {svc ? (
            <>
              <svc.icon className="mr-2 inline-block align-[-0.12em] text-brand-red" size={34} aria-hidden />
              {svc.label}: what do you need?
            </>
          ) : (
            <>Where to next{name ? `, ${name}` : ""}?</>
          )}
        </h2>

        {children}

        <div className="mt-5 flex flex-wrap justify-center gap-2" aria-label="Suggestions">
          {chips.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => onPick(c)}
              className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface/80 px-3.5 py-1.5 text-sm text-fg backdrop-blur transition hover:border-brand-blue hover:text-link focus-visible:outline-2 focus-visible:outline-brand-blue"
            >
              <Sparkles size={13} className="text-brand-red" aria-hidden />
              {c}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
