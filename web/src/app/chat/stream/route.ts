// Same-origin passthrough for the SSE chat stream.
//
// Why this exists rather than a rewrite in next.config.ts: rewrites BUFFER the
// response. Measured locally, the first SSE event arrived after 4.3s through a
// rewrite versus 0.0s calling the API directly -- and a tour search runs 40s,
// so the customer sees an empty bubble for the whole search and assumes it has
// hung. This handler streams `res.body` straight through, so tokens arrive as
// they are produced.
//
// And why proxy at all instead of calling the API directly from the browser:
// http://localhost:3000 and http://127.0.0.1:8000 are different SITES, so the
// session cookie is cross-site. SameSite=lax is not sent on fetch(), and
// SameSite=none requires Secure, which plain http cannot provide. Keeping the
// request same-origin sidesteps both.

export const runtime = "nodejs";
// Never cache or pre-render: this is a live stream.
export const dynamic = "force-dynamic";

const API = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";

export async function POST(req: Request): Promise<Response> {
  const upstream = await fetch(`${API}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      // Forward the session cookie; without it the API returns 401 and the
      // customer sees a silent failure after a successful login.
      cookie: req.headers.get("cookie") ?? "",
    },
    body: await req.text(),
    // Node needs this to stream a request body; harmless when there is none.
    // @ts-expect-error -- duplex is valid at runtime, missing from the DOM types
    duplex: "half",
  });

  if (!upstream.ok || !upstream.body) {
    // Pass the real status through so the UI can tell 401 from 500 rather than
    // reporting every failure as "cannot reach the server".
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": "application/json" },
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      // Tells nginx and friends not to buffer. Harmless in dev, load-bearing
      // behind a proxy in production.
      "X-Accel-Buffering": "no",
    },
  });
}
