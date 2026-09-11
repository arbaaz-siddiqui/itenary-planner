# Dubai Trip Planner — Web UI

A ChatGPT-shaped Next.js chat front end for the existing Python AI travel-agent
backend. Self-contained in `web/`; nothing outside this directory is touched, and
the Streamlit app (`surfaces/streamlit_app.py`) keeps working unchanged.

## Run locally

```bash
cd web
npm install
cp .env.example .env.local   # then edit
npm run dev                  # http://localhost:3000
```

With `NEXT_PUBLIC_MOCK_MODE=true` (the default in `.env.example`) you need no
backend at all — sign in with any email and password.

Production build:

```bash
npm run build
npm start        # honours $PORT, defaults to 3000
```

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | *(empty)* | Base URL of the Python API, no trailing slash (e.g. `https://api.example.com`). Empty means same-origin relative paths, which is right when the API is proxied under the same domain. |
| `NEXT_PUBLIC_MOCK_MODE` | `false` | `true` serves canned sessions, messages and a simulated SSE stream in the browser. No network calls leave the page. |

> **These are build-time values.** Next.js inlines every `NEXT_PUBLIC_*` variable
> into the client bundle during `next build`, so changing one requires a rebuild
> (and a redeploy on Railway) — restarting the server is not enough. This is the
> single most common surprise here: if mock mode "doesn't turn off", you are
> running a bundle built with the old value.

### Mock mode

Exercises every UI path before the API exists:

- Any email + password signs in; password `wrong` shows the auth error state.
- One seeded chat, plus full create / rename / delete.
- Replies stream token-by-token after a ~900 ms delay, so the typing indicator
  is visible.
- Each reply emits a `tool` event with two upstream API calls and an `options`
  event with three hotels — one of which has a deliberately broken image URL so
  the fallback tile is verifiable.
- State lives in `localStorage`, so it survives a refresh.

## Backend API contract

The UI is written against exactly this. Auth is a httpOnly cookie; every request
sends `credentials: "include"`.

| Method | Path | Notes |
|---|---|---|
| `POST` | `/auth/login` | `{email, password}` → sets cookie, returns `{user}` |
| `POST` | `/auth/logout` | → 204 |
| `GET` | `/auth/me` | → `{user}` or 401 |
| `GET` | `/chat/sessions` | → `[{id, title, created_at, updated_at}]` |
| `POST` | `/chat/sessions` | `{title?}` → session |
| `PATCH` | `/chat/sessions/{id}` | `{title}` → session |
| `DELETE` | `/chat/sessions/{id}` | → 204 |
| `GET` | `/chat/sessions/{id}/messages` | → `[{id, role, content, tool_calls, created_at}]` |
| `POST` | `/chat/stream` | `{session_id, message}` → `text/event-stream` |

SSE events: `token` `{text}`, `tool` `{name, input, output, api_calls[]}`,
`options` `{kind, items[]}`, `error` `{message}`, `done` `{message_id}`.

### Swapping in the real API

**All network access lives in `src/lib/api.ts`.** That is the only file to touch
when routes, headers or auth change. Set `NEXT_PUBLIC_MOCK_MODE=false`, point
`NEXT_PUBLIC_API_URL` at the backend, rebuild. No component imports `fetch`.

Because auth is a cross-origin cookie, the backend must send
`Access-Control-Allow-Credentials: true` and an explicit
`Access-Control-Allow-Origin` (a literal origin — `*` is invalid with
credentials). The stream endpoint must not be buffered by an intermediate proxy,
or tokens arrive in one lump.

## Layout

```
src/
  lib/
    api.ts          the ONLY network boundary — mock/real switch lives here
    sse.ts          SSE frame parser (POST-based, so not EventSource)
    mock.ts         canned backend for NEXT_PUBLIC_MOCK_MODE
    types.ts        contract types
  components/
    Markdown.tsx    react-markdown + remark-gfm; wraps tables for scrolling
    MessageList.tsx bubbles, typing indicator, streaming caret
    Composer.tsx    auto-growing textarea, Enter sends
    Sidebar.tsx     session list, inline rename, confirm-then-delete
    OptionCards.tsx tour / hotel / flight cards
    SafeImage.tsx   aspect-ratio box + fallback for 404ing supplier URLs
    DebugPanel.tsx  per-message tool + API inspector (lazy)
  app/
    login/page.tsx  redirects to /chat when already authed
    chat/page.tsx   stream folding, session CRUD, mobile drawer
```

### Notes on a few deliberate choices

- **Markdown tables.** The agent leans on wide ₹-price comparison tables. Each
  table renders inside an `overflow-x: auto` wrapper, and the flex ancestors
  carry `min-w-0` — without that a wide table stretches the whole layout instead
  of scrolling. Verified at 375 px: the table scrolls internally and the document
  itself has no horizontal scroll.
- **Debug panel is lazily mounted.** Real tool output runs 200 KB+. The panel,
  each tool, and each individual payload have their own collapsed state, and
  `JSON.stringify` runs only once a leaf is opened. Output past 40 KB is
  truncated with the true size reported.
- **Images use `<img>`, not `next/image`.** Supplier hosts are not known at build
  time and some URLs 404; `onError` swaps in a placeholder tile. Relative media
  paths get the supplier base URL prefixed, matching the Streamlit behaviour.
  `images.unoptimized` is set, so the optimizer (and `sharp`) is never invoked.
- **Streaming is batched.** Tokens accumulate in a 50 ms buffer rather than
  triggering a re-render per character.
- **Theme follows the OS**, with no toggle. The palette is defined as plain CSS
  variables on `:root` — Tailwind v4 hoists an `@theme` block out of a
  `@media` query, which would make one palette unconditional.

## Deploy on Railway

Create a service from this repo and set **Root Directory** to `web/`. Railway's
Nixpacks detects Next.js and runs `npm ci` → `npm run build` → `npm start`.
`next start` reads `$PORT`, which Railway injects.

Set these service variables **before the first build** (they are baked into the
bundle):

```
NEXT_PUBLIC_API_URL=https://<your-api-service>.up.railway.app
NEXT_PUBLIC_MOCK_MODE=false
```

If you change either one later, trigger a redeploy — a restart will not pick it
up.

The existing root-level `railway.*.toml` files belong to the Python services and
are unrelated; with Root Directory set to `web/`, Railway does not read them.

## Verification status

`npm install` and `npm run build` both pass (Next 15.5.25, TypeScript strict,
`npm audit` clean). The mock-mode flow was driven end to end in a real browser:
login → redirect, session list, token streaming, GFM table rendering, image
fallback, lazy debug panel, refresh persistence, and the mobile drawer at 375 px.
