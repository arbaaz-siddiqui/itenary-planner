"use client";

import { useState } from "react";
import { ImageOff, type LucideIcon } from "lucide-react";

// Media is served by the CDN, NOT the API host: the supplier's own
// stagingapi.gujjutours.com URLs 404, and tenant-scoped paths live under
// /uploads there. This mirrors _resolve_image_url in parsers.py -- the server
// already sends CDN URLs, so this only repairs a relative path, or an API-host
// URL stored before the server learned every media folder.
const MEDIA_CDN_BASE = "https://d3bfv5x1dw8ekm.cloudfront.net";
const CDN_PREFIXES = ["tour-images/", "hotel-images/", "restaurant-images/"];
const TENANT_MEDIA = /\/[0-9a-f]{8}-[0-9a-f-]{27}\/[a-z]+media\//i;
const API_HOST = /^https?:\/\/stagingapi\.gujjutours\.com/i;

/** Supplier payloads sometimes carry a relative media path instead of a URL. */
export function normalizeImageUrl(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const s = raw.trim();
  if (!s) return null;
  if (s.startsWith("data:")) return s;
  if (/^https?:\/\//i.test(s)) {
    if (!API_HOST.test(s)) return s;
    const path = s.replace(API_HOST, "");
    return TENANT_MEDIA.test(path) ? `${MEDIA_CDN_BASE}/uploads${path}` : s;
  }
  const path = s.replace(/^\/+/, "");
  if (CDN_PREFIXES.some((p) => path.startsWith(p))) return `${MEDIA_CDN_BASE}/${path}`;
  if (TENANT_MEDIA.test(`/${path}`)) return `${MEDIA_CDN_BASE}/uploads/${path}`;
  return `${MEDIA_CDN_BASE}/${path}`;
}

/**
 * Card photo. Plain <img> rather than next/image on purpose: supplier hosts
 * are not known at build time and a dead URL must degrade to a placeholder
 * instead of throwing. Fixed aspect ratio so the card never reflows.
 */
export function SafeImage({
  src,
  alt,
  className = "",
  ratio = "aspect-[3/2]",
  icon: Icon = ImageOff,
  eager = false,
}: {
  src: unknown;
  alt: string;
  className?: string;
  ratio?: string;
  /** The section's icon, shown on the branded placeholder. */
  icon?: LucideIcon;
  eager?: boolean;
}) {
  const url = normalizeImageUrl(src);
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);

  if (!url || failed) {
    return (
      <div
        className={`${ratio} ${className} flex items-center justify-center bg-gradient-to-br from-brand-blue/25 via-surface-2 to-brand-red/20 text-fg-muted`}
        aria-label={`No photo for ${alt}`}
        role="img"
      >
        <Icon size={28} strokeWidth={1.5} aria-hidden />
      </div>
    );
  }

  return (
    <div className={`${ratio} ${className} relative overflow-hidden bg-surface-2`}>
      {!loaded && <div className="skeleton absolute inset-0" aria-hidden />}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={url}
        alt={alt}
        loading={eager ? "eager" : "lazy"}
        decoding="async"
        onError={() => setFailed(true)}
        onLoad={() => setLoaded(true)}
        className={`relative h-full w-full object-cover transition-opacity duration-300 ${loaded ? "opacity-100" : "opacity-0"}`}
      />
    </div>
  );
}
