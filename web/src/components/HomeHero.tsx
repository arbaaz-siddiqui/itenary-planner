"use client";

import { SlidersHorizontal } from "lucide-react";
import { QUICK_STARTS, serviceById, type ServiceId } from "@/lib/services";

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
  service,
  onPick,
  onBuildPackage,
  children,
}: {
  name: string;
  service: ServiceId | null;
  onPick: (prompt: string) => void;
  /** Opens the "Build a package" form; offered on the Packages screen. */
  onBuildPackage?: () => void;
  children: React.ReactNode;
}) {
  const svc = serviceById(service);
  const chips = svc
    ? svc.starters.map((text) => ({ text, Icon: svc.icon }))
    : QUICK_STARTS.map((q) => ({ text: q.text, Icon: serviceById(q.service)!.icon }));

  return (
    // Fills the rounded page panel the chat page provides.
    <div className="flex min-h-full flex-col">
      <div className="hero-glow hero-bloom flex flex-1 flex-col items-center justify-center px-4 py-8 sm:py-16">
        <div className="w-full max-w-[720px]">
          <div className="hero-rise mb-5 flex justify-center">
            <span className={`inline-flex items-center gap-2 rounded-full border py-1 pl-1 pr-3 text-xs backdrop-blur ${ON_GLOW}`}>
              <span className="rounded-full bg-brand-blue px-2 py-0.5 text-[11px] font-semibold text-white">New</span>
              Live GujjuTours prices in every answer
            </span>
          </div>

          <h2
            className="hero-rise mb-6 text-balance text-center text-[30px] sm:mb-8 font-semibold leading-[1.1] tracking-[-0.03em] text-white sm:text-[44px] [[data-theme=light]_&]:text-fg"
            style={{ "--rise-delay": "60ms" } as React.CSSProperties}
          >
            {svc ? `${svc.label}: what do you need?` : <>Where to next{name ? `, ${name}` : ""}?</>}
          </h2>

          <div className="hero-rise" style={{ "--rise-delay": "120ms" } as React.CSSProperties}>
            {children}
          </div>

          <div
            className="hero-rise mt-5 flex flex-wrap justify-center gap-2 sm:mt-6"
            style={{ "--rise-delay": "180ms" } as React.CSSProperties}
            aria-label="Suggestions"
          >
            {service === "packages" && onBuildPackage && (
              <button
                type="button"
                onClick={onBuildPackage}
                className="press inline-flex items-center gap-2 rounded-full bg-brand-blue px-3.5 py-1.5 text-[13px] font-medium text-white shadow-sm hover:bg-brand-blue-dark sm:py-2 sm:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
              >
                <SlidersHorizontal size={15} strokeWidth={1.9} aria-hidden />
                Build my own package
              </button>
            )}
            {chips.map(({ text, Icon }) => (
              <button
                key={text}
                type="button"
                onClick={() => onPick(text)}
                className={`press inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-[13px] backdrop-blur sm:px-3.5 sm:py-2 sm:text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white ${ON_GLOW}`}
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
