"use client";

import { useState } from "react";

// Media is served by the CDN, NOT the API host: the supplier's own
// stagingapi.gujjutours.com URLs 404, and tenant-scoped paths live under
// /uploads there. This mirrors _resolve_image_url in parsers.py -- the server
// already sends absolute CDN URLs, so this is only a fallback for a relative
// path that slipped through.
const MEDIA_CDN_BASE = "https://d3bfv5x1dw8ekm.cloudfront.net";
const CDN_PREFIXES = ["tour-images/", "hotel-images/", "restaurant-images/"];
const CDN_UPLOADS_MARKERS = ["/TourMedia/", "/HotelMedia/", "/RestaurantMedia/"];

/** Supplier payloads sometimes carry a relative media path instead of a URL. */
export function normalizeImageUrl(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const s = raw.trim();
  if (!s) return null;
  if (/^https?:\/\//i.test(s) || s.startsWith("data:")) return s;
  const path = s.replace(/^\/+/, "");
  if (CDN_PREFIXES.some((p) => path.startsWith(p))) return `${MEDIA_CDN_BASE}/${path}`;
  if (CDN_UPLOADS_MARKERS.some((m) => `/${path}`.includes(m)))
    return `${MEDIA_CDN_BASE}/uploads/${path}`;
  return `${MEDIA_CDN_BASE}/${path}`;
}

/**
 * Option-card image. Plain <img> rather than next/image on purpose: supplier
 * hosts are not known at build time and a dead URL must degrade to a
 * placeholder instead of throwing. Fixed aspect ratio so the card does not
 * reflow once the bytes land.
 */
export function SafeImage({
  src,
  alt,
  className = "",
  ratio = "aspect-[16/10]",
}: {
  src: unknown;
  alt: string;
  className?: string;
  ratio?: string;
}) {
  const url = normalizeImageUrl(src);
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);

  if (!url || failed) {
    return (
      <div
        className={`${ratio} ${className} flex items-center justify-center rounded-lg bg-surface-2 text-fg-muted`}
        aria-label={`No image for ${alt}`}
        role="img"
      >
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.5" aria-hidden="true">
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <circle cx="8.5" cy="9.5" r="1.5" />
          <path d="m21 16-5-5-4 4-2-2-7 7" />
        </svg>
      </div>
    );
  }

  return (
    <div className={`${ratio} ${className} relative overflow-hidden rounded-lg bg-surface-2`}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {/* A skeleton while the bytes are in flight. Without it the card showed
          an empty grey box and the image appeared to load "late" even when it
          was already on its way. */}
      {!loaded && <div className="absolute inset-0 animate-pulse bg-surface-2" />}
      <img
        src={url}
        alt={alt}
        /* eager, not lazy: these sit inside a horizontally scrolled carousel,
           where the off-screen cards are one swipe away. Lazy-loading them
           meant the picture only started downloading after the customer
           scrolled to it, which is exactly the delay being reported. */
        loading="eager"
        /* Hints the browser to fetch the first cards sooner without blocking
           the reply text. */
        fetchPriority="high"
        decoding="async"
        onError={() => setFailed(true)}
        onLoad={() => setLoaded(true)}
        className={`relative h-full w-full object-cover transition-opacity duration-200 ${
          loaded ? "opacity-100" : "opacity-0"
        }`}
      />
    </div>
  );
}
