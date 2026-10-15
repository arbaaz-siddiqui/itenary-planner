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
 * Sign-in and sign-up frame. The form sits on the left; on wide screens the
 * right is the brand gradient with GUJJU / TOURS set huge, and the client's
 * travel reel plays only inside the letters. On phones the reel fades in
 * behind the heading instead. Only one video element is ever mounted, so the
 * reel downloads once.
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
        <div className="hero-glow hero-glow-fixed hero-bloom relative flex h-full flex-col justify-between overflow-hidden rounded-[28px] p-12">
          <div className="flex flex-1 items-center justify-center">
            {wide && <ReelWordmark className="word-rise w-full" />}
          </div>
          <p className="text-[30px] leading-[1.15] font-semibold tracking-tight text-white">
            Tell it the trip.
            <br />
            Get a plan you can book.
          </p>
        </div>
      </aside>
    </main>
  );
}

// The wordmark lives in a 2400 x 300 box: one line, cap height filling the
// box, stretched to its full width so it reads as one solid band.
const BOX = { w: 2400, h: 300 };
const WORDS = [{ text: "GUJJU TOURS", y: 290 }];
const WORD_STYLE = { fontFamily: "var(--font-poppins), sans-serif", fontWeight: 700, fontSize: 400 } as const;
// The band shows the reel's full width, so crop to the top of the frame: the
// burned-in tagline sits in its lower half.
const WORD_FOCUS = "50% 0%";
const CLIP_ID = "gt-reel-letters";

/** GUJJU TOURS with the reel playing inside the letters and nowhere else. */
function ReelWordmark({ className }: { className?: string }) {
  return (
    <div className={`relative ${className ?? ""}`} style={{ aspectRatio: `${BOX.w} / ${BOX.h}` }}>
      {/* Letters as a clip path in the box's own units (0..1), drawn in the
          box's own space and scaled down to fit. */}
      <svg width="0" height="0" className="absolute" aria-hidden>
        <clipPath id={CLIP_ID} clipPathUnits="objectBoundingBox">
          {/* Each line carries the scale itself: a clip path takes only
              shapes and text, never a group. */}
          {WORDS.map((w) => (
            <text
              key={w.text}
              x="0"
              y={w.y}
              transform={`scale(${1 / BOX.w} ${1 / BOX.h})`}
              textLength={BOX.w}
              lengthAdjust="spacingAndGlyphs"
              style={WORD_STYLE}
            >
              {w.text}
            </text>
          ))}
        </clipPath>
      </svg>
      <div
        className="absolute inset-0 bg-white/15"
        style={{ clipPath: `url(#${CLIP_ID})`, WebkitClipPath: `url(#${CLIP_ID})` }}
      >
        <Reel className="h-full w-full" focus={WORD_FOCUS} />
      </div>
      {/* A hairline on the letter edges so they hold their shape over the
          reel's bright and dark scenes alike. */}
      <svg viewBox={`0 0 ${BOX.w} ${BOX.h}`} className="absolute inset-0 h-full w-full" aria-hidden>
        {WORDS.map((w) => (
          <text
            key={w.text}
            x="0"
            y={w.y}
            textLength={BOX.w}
            lengthAdjust="spacingAndGlyphs"
            style={WORD_STYLE}
            fill="none"
            stroke="rgba(255,255,255,0.22)"
            strokeWidth="1.25"
          >
            {w.text}
          </text>
        ))}
      </svg>
    </div>
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
function Reel({ className, focus = VIDEO_FOCUS }: { className?: string; focus?: string }) {
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
      style={{ objectPosition: focus }}
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
