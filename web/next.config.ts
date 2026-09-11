import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Supplier image hosts are not known ahead of time and some URLs 404, so we
  // render option images with a plain <img> + onError fallback rather than
  // next/image (which needs an allowlist and hard-fails on a bad upstream).
  images: {
    unoptimized: true,
  },

  // Dev-only proxy so the browser talks ONLY to localhost:3000 and the request
  // to the API is never cross-origin. This sidesteps a class of local-only
  // failures that look like "cannot reach the server" but are the browser
  // refusing the request before it leaves: Brave/uBlock treating
  // localhost:3000 -> localhost:8000 as cross-site tracking, and cookies being
  // dropped because a cross-origin cookie needs SameSite=None + Secure, which
  // plain http://localhost cannot satisfy.
  //
  // Set NEXT_PUBLIC_API_URL="" (or omit it) to route through this. In
  // production the two services are on different hosts and CORS handles it, so
  // rewrites are not used there.
  async rewrites() {
    const target = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";
    // `beforeFiles` would shadow src/app/chat/stream/route.ts. These run
    // AFTER routes, so the streaming handler wins for /chat/stream and only
    // the ordinary JSON endpoints are rewritten here.
    return {
      beforeFiles: [],
      afterFiles: [
        { source: "/auth/:path*", destination: `${target}/auth/:path*` },
        { source: "/chat/sessions/:path*", destination: `${target}/chat/sessions/:path*` },
        { source: "/chat/messages/:path*", destination: `${target}/chat/messages/:path*` },
        { source: "/health/:path*", destination: `${target}/health/:path*` },
        // "Show details" on a tour card. Without this the request never left
        // Next, so the panel sat on "Loading details…" forever.
        { source: "/tours/:path*", destination: `${target}/tours/:path*` },
      ],
      fallback: [],
    };
  },
};

export default nextConfig;
