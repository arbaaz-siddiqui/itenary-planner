"use client";

import { useEffect, useRef, useState } from "react";
import { BrandLogo } from "@/components/BrandLogo";

/** The client's hero reel (also on gujjutours.com). Overridable per deploy. */
const HERO_VIDEO =
  process.env.NEXT_PUBLIC_AUTH_VIDEO_URL ||
  "https://video.gumlet.io/6a200c2c40fe2ab6282883ef/6a20209f40fe2ab6282aefb1/download.mp4";
const HERO_POSTER = "/auth/poster.webp";
// The reel is a 3:1 banner with its tagline burned into the left half; the
// crop keeps to the right side, which is clean in every scene.
const VIDEO_FOCUS = "100% 50%";

/**
 * Sign-in and sign-up frame. The form sits on the black page; on wide
 * screens the brand gradient card on the right carries one line, set big.
 * On phones the client's travel reel fades in behind the heading.
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
  const narrow = useNarrow();
  return (
    <main className="relative flex min-h-dvh overflow-hidden bg-bg">
      {/* Phones only: the reel fades in behind the heading. Desktop keeps
          the plain black page, and never mounts (or downloads) the video. */}
      {narrow && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-[46%] opacity-55 [mask-image:linear-gradient(to_bottom,black_20%,transparent)]"
        >
          <Reel className="h-full w-full" />
        </div>
      )}
      <section className="relative flex w-full flex-col px-5 pt-10 pb-6 sm:px-10 sm:pt-12 lg:w-[46%] lg:max-w-[640px] lg:px-14">
        <div className="relative flex">
          <BrandLogo />
        </div>
        <div className="relative flex flex-1 items-center py-10">
          <div className="hero-rise mx-auto w-full max-w-[380px]">
            <h1 className="text-[30px] leading-tight font-semibold text-fg">{title}</h1>
            <p className="mt-2 text-sm leading-relaxed text-fg-muted">{subtitle}</p>
            <div className="mt-8">{children}</div>
            {footer && <div className="mt-8 text-sm text-fg-muted">{footer}</div>}
          </div>
        </div>
        </section>

      {/* The panel keeps the dark gradient in both themes: the light one is
          too pale to carry white type. */}
      <aside aria-hidden className="hidden flex-1 p-3 lg:block">
        <div className="hero-glow hero-glow-fixed hero-bloom relative flex h-full flex-col justify-end overflow-hidden rounded-[28px] p-12">
          <div className="word-rise text-white">
            <p className="font-display text-[clamp(28px,3vw,44px)] leading-[1.05] font-semibold tracking-tight">
              Plan a trip
              <br />
              that lasts
            </p>
            {/* FOREVER is stretched to the panel's full width, so it always
                fills the line exactly, whatever the screen size. */}
            <svg viewBox="0 0 1000 170" className="mt-3 block w-full" role="presentation">
              <text
                x="0"
                y="160"
                textLength="1000"
                lengthAdjust="spacingAndGlyphs"
                fill="white"
                style={{ fontFamily: "var(--font-display-face), sans-serif", fontWeight: 800, fontSize: 220 }}
              >
                FOREVER
              </text>
            </svg>
            <p className="mt-6 max-w-[420px] text-sm leading-relaxed text-white/80">
              Flights, hotels, tours and visas, planned in one conversation and priced live.
            </p>
          </div>
        </div>
      </aside>
    </main>
  );
}

/** True below the `lg` breakpoint, where the gradient panel is hidden. */
function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1023px)");
    const sync = () => setNarrow(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  return narrow;
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
