"use client";

import { useEffect, useRef, useState } from "react";
import { BedDouble, Plane, Ticket } from "lucide-react";
import { BrandLogo } from "@/components/BrandLogo";

/** The client's hero reel (also on gujjutours.com). Overridable per deploy. */
const HERO_VIDEO =
  process.env.NEXT_PUBLIC_AUTH_VIDEO_URL ||
  "https://video.gumlet.io/6a200c2c40fe2ab6282883ef/6a20209f40fe2ab6282aefb1/download.mp4";
const HERO_POSTER = "/auth/poster.webp";
// The reel is a 3:1 banner with its tagline burned into the left half; every
// crop here shows only the right side, which is clean in every scene.
const VIDEO_FOCUS = "88% 50%";

/**
 * Sign-in and sign-up frame. The form sits on the left; on wide screens the
 * right is the brand gradient with the client's travel reel playing inside a
 * tall arch, a doorway onto the trip, with a glimpse of the agent's answer
 * resting against it. On narrow screens the reel rises faintly behind the
 * heading instead. Only one video element is ever mounted, so the reel
 * downloads once.
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
  const wide = useWide();
  return (
    <main className="relative flex min-h-dvh overflow-hidden bg-bg">
      {wide === false && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-[46%] opacity-55 [mask-image:linear-gradient(to_bottom,black_20%,transparent)]"
        >
          <Reel className="h-full w-full" />
        </div>
      )}
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
        <div className="hero-glow hero-glow-fixed hero-bloom relative h-full overflow-hidden rounded-[28px]">
          {wide && (
            <div className="arch-rise absolute top-12 right-12 bottom-12 w-[min(46%,460px)]">
              {/* A hairline echo of the arch, set just outside it. */}
              <div className="absolute -inset-3.5 rounded-t-full rounded-b-[38px] border border-white/20" />
              <div className="relative h-full overflow-hidden rounded-t-full rounded-b-[26px] shadow-[0_40px_80px_-30px_rgba(0,0,0,0.65)] ring-1 ring-white/25">
                <Reel className="h-full w-full scale-[1.04]" />
                {/* Grade the reel toward the brand sunset so it sits in the
                    gradient instead of on top of it. */}
                <div className="absolute inset-0 bg-[linear-gradient(to_bottom,rgba(43,127,200,0.35),transparent_45%,rgba(232,80,58,0.45))] mix-blend-soft-light" />
                <div className="absolute inset-0 rounded-[inherit] shadow-[inset_0_0_60px_rgba(0,0,0,0.35)]" />
              </div>
            </div>
          )}
          <div className="absolute bottom-12 left-12 z-10 max-w-[440px]">
            <TripPreview />
          </div>
        </div>
      </aside>
    </main>
  );
}

/** True at the `lg` breakpoint and up; null until the first client render. */
function useWide(): boolean | null {
  const [wide, setWide] = useState<boolean | null>(null);
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    const sync = () => setWide(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  return wide;
}

/** The looping reel. Holds on the poster when the visitor asks for less motion. */
function Reel({ className }: { className?: string }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => {
      if (mq.matches) v.pause();
      else v.play().catch(() => {});
    };
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  return (
    <video
      ref={ref}
      className={`object-cover ${className ?? ""}`}
      style={{ objectPosition: VIDEO_FOCUS }}
      src={HERO_VIDEO}
      poster={HERO_POSTER}
      muted
      loop
      playsInline
      preload="auto"
      disablePictureInPicture
      tabIndex={-1}
    />
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
      <div className="mt-8 hidden space-y-3 xl:block">
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
