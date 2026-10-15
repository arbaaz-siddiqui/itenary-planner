"use client";

import { BedDouble, Plane, Ticket } from "lucide-react";
import { BrandLogo } from "@/components/BrandLogo";

/**
 * Sign-in and sign-up frame: the form on the left, and on wide screens the
 * home screen's gradient on the right with a glimpse of what the agent does.
 * The gradient is the one bold element; everything around the form stays
 * quiet so the fields read first.
 */
export function AuthShell({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <main className="relative flex min-h-dvh overflow-hidden bg-bg">
      {/* Narrow screens have no gradient panel, so the brand colour rises
          faintly from the bottom edge instead. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 bottom-0 h-[45%] opacity-40 [mask-image:linear-gradient(to_top,black,transparent)] lg:hidden"
      >
        <div className="hero-glow h-full w-full" />
      </div>
      <section className="relative flex w-full flex-col px-5 py-6 sm:px-10 lg:w-[46%] lg:max-w-[640px] lg:px-14">
        <BrandLogo />
        <div className="flex flex-1 items-center py-10">
          <div className="hero-rise mx-auto w-full max-w-[380px]">
            <h1 className="text-[28px] leading-tight font-semibold tracking-tight text-fg">{title}</h1>
            <p className="mt-2 text-sm leading-relaxed text-fg-muted">{subtitle}</p>
            <div className="mt-8">{children}</div>
            {footer && <div className="mt-8 text-sm text-fg-muted">{footer}</div>}
          </div>
        </div>
        <p className="text-xs text-fg-muted">Flights, hotels, tours, transfers and visas for Dubai, in one chat.</p>
      </section>

      {/* The panel keeps the dark gradient in both themes: the light one is
          too pale to carry white type. */}
      <aside aria-hidden className="hidden flex-1 p-3 lg:block">
        <div className="hero-glow hero-glow-fixed hero-bloom relative flex h-full flex-col justify-end overflow-hidden rounded-[28px] p-12">
          <TripPreview />
        </div>
      </aside>
    </main>
  );
}

/** A still of a conversation, so the first screen already shows the product. */
function TripPreview() {
  const lines = [
    { icon: Plane, title: "Return flights from Ahmedabad", meta: "Direct, 3 h 20 m" },
    { icon: BedDouble, title: "3 nights near Dubai Marina", meta: "Breakfast included" },
    { icon: Ticket, title: "Desert safari with BBQ dinner", meta: "Hotel pickup" },
  ];
  return (
    <div className="max-w-[440px] text-white">
      <p className="text-[34px] leading-[1.15] font-semibold tracking-tight">
        Tell it the trip.
        <br />
        Get a plan you can book.
      </p>
      <div className="mt-8 space-y-3">
        <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-md bg-white/15 px-4 py-2.5 text-sm backdrop-blur-md">
          3 nights in Dubai for 2 of us, mid November
        </p>
        <div className="rounded-2xl rounded-bl-md border border-white/15 bg-black/35 p-4 backdrop-blur-md">
          <p className="text-sm text-white/85">Here is a plan for 2 adults:</p>
          <ul className="mt-3 space-y-2">
            {lines.map(({ icon: Icon, title, meta }) => (
              <li key={title} className="flex items-center gap-3 rounded-xl bg-white/[0.07] px-3 py-2.5">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-white/12">
                  <Icon className="h-4 w-4" strokeWidth={1.75} />
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{title}</span>
                  <span className="block text-xs text-white/65">{meta}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
