# Plan — Next.js chat UI, Postgres persistence, transfer pricing

**Branch:** `feat/nextjs-ui` · **No commits or pushes by Claude** — user only.
**Constraint:** the existing Streamlit app stays live and untouched throughout.

---

## Verified starting state

Checked against the repo and the live API, not assumed:

| Thing | Reality |
|---|---|
| Next.js app | Does not exist |
| HTTP chat API | **Does not exist** — Streamlit calls the agent in-process |
| Postgres | **Entirely unwired**: no driver, no `DATABASE_URL`, no code |
| Chat persistence today | SQLite (WhatsApp only) / in-memory (Streamlit — lost on refresh) |
| Auth | None |
| Available | FastAPI 0.136.3, uvicorn 0.48.0, LangGraph 1.2.2 |
| Missing packages | `langgraph-checkpoint-postgres`, `psycopg[binary,pool]` |
| Existing FastAPI pattern | `surfaces/whatsapp_app.py` — reuse its shape |

**The load-bearing consequence:** a Next.js UI cannot talk to the agent today,
because no endpoint exists. So the order must be **DB → API → UI**, not DB → UI.

Two open bugs found while verifying, both folded into Phase 5:

1. `get_tour_options` transfer pricing — the double-count and per-adult fix are
   in the working tree, unverified end to end.
2. `_tour_price_lookup("Dubai Citytour", ...)` returns `0.0`. **Confirmed
   pre-existing** — reproduced on a stashed clean tree, so it is not from the
   pricing change. The itinerary total silently loses that tour's price.

---

## Target structure

Additive only. Nothing under `surfaces/` changes.

```
api/                      NEW — FastAPI chat service
  main.py                 /chat/stream (SSE), /chat/sessions, /auth/*
  db.py                   psycopg pool, migration runner
  auth.py                 httpOnly cookie sessions
  repository.py           session + message persistence
  schemas.py              pydantic request/response
web/                      NEW — Next.js app
  app/                    chat page, login, layout
  components/             MessageList, Composer, SessionSidebar, DebugPanel
  lib/                    api client, SSE hook, auth context
migrations/
  001_init.sql            users, chat_sessions, chat_messages
agent.py                  MODIFIED — add build_postgres_checkpoint()
rules.py                  MODIFIED — transfer cost calculator
surfaces/streamlit_app.py UNCHANGED — stays live
```

**Railway:** two new services from the same repo — `chat-api` and `chat-web` —
with different root directories and start commands. The current `chat-service`
keeps running so the client is never interrupted.

---

## Phase 1 — Database (blocks everything else)

**Needs from user:** `DATABASE_URL` (Railway → Postgres → Variables → the public
`DATABASE_PUBLIC_URL`, so migrations can run from a laptop).

Add `psycopg[binary,pool]` and `langgraph-checkpoint-postgres` to
`requirements.txt`.

Two separate concerns, deliberately not merged:

- **LangGraph checkpoints** — the agent's working state, via `PostgresSaver`.
  It owns its own tables and creates them with `.setup()`. This replaces the
  in-memory store, which is why a browser refresh currently wipes context.
- **Our tables** — the human-readable transcript, session list, and auth.

```sql
users          (id, email, password_hash, created_at)
chat_sessions  (id, user_id, title, created_at, updated_at)
chat_messages  (id, session_id, role, content, tool_calls JSONB,
                created_at)
```

`tool_calls` is JSONB so the debug view survives a reload — today it is lost.

**Memory:** start with last-N messages plus a per-user facts row (home city,
usual party size, past trips). Vector/semantic recall is deliberately **not** in
this plan — it is a later step once the basics are proven, and adding it now
would be building on an untested foundation.

**Done when:** `psql` connects, migrations apply idempotently, a LangGraph turn
persists and resumes across a process restart.

---

## Phase 2 — API (blocked by Phase 1)

Reuse the `whatsapp_app.py` shape. `stream_and_log` already yields tokens, so
SSE is a thin wrapper.

| Endpoint | Purpose |
|---|---|
| `POST /auth/login`, `/auth/logout`, `GET /auth/me` | cookie session |
| `GET/POST /chat/sessions`, `PATCH`, `DELETE /{id}` | session CRUD |
| `GET /chat/sessions/{id}/messages` | transcript |
| `POST /chat/stream` | SSE: tokens, tool events, options, done |
| `GET /health` | Railway probe |

Persist the user message before streaming and the assistant message after, so an
interrupted stream cannot lose the turn.

**Done when:** a `curl` SSE call returns a streamed reply, the transcript
survives a restart, and Streamlit still works unchanged.

---

## Phase 3 — UI (blocked by Phase 2)

Next.js App Router + Tailwind. ChatGPT-shaped: session sidebar, new chat,
streaming tokens, markdown tables, tour/hotel images, PDF download, collapsible
debug panel (API called / input / output — the format already agreed).

**Done when:** login works, a conversation streams, sessions persist across
refresh and across devices, tables and images render, and it is usable on
mobile.

---

## Phase 4 — Railway deployment (blocked by 2 and 3)

`chat-api` (root `/`, uvicorn) and `chat-web` (root `web/`, next start), both
with `DATABASE_URL` and the supplier/LLM variables. Existing `chat-service`
untouched.

**Note for the user:** `BOOKING_B2C_BASE_URL` must be `https://www.gujjutours.com`
on any new service. `stagingb2c` returns an HTML 404 on every `/api` path, which
is what broke transfer prices earlier this week.

---

## Phase 5 — Transfer pricing (independent of 1–4)

Deterministic maths in `rules.py`, never left to the model. Rules confirmed
against live API data:

```
ticket   = per_adult × actual_pax        (child discounts via price_group)

sharing  = sharing_price × max(actual_pax, minPax)
private  = private_price × ceil(actual_pax / seats_per_vehicle)

total    = ticket + transfer
```

Verified across 25 tours / 91 options:

| Sharing (minPax, maxPax) | Options |
|---|---|
| (2, 100) | 21 |
| (2, 12) | 15 |
| (2, 10) | 5 |

Private was `(1, 12)` on all 49 options sampled. `vehicleName` is populated on
some (`Toyota Hiace`, `vehicleId: 4`) and null on others.

**Open question that must be resolved before shipping the private calculation:**
is `maxPax` on the private row the *vehicle seat count* or a *booking ceiling*?
Sharing rows carry `maxPax: 100`, which cannot be a vehicle — so the field means
"max bookable pax for this transfer type" and merely *coincides* with capacity
for private. If any tour sets private `maxPax: 12` as a ceiling while the car
seats 6, `ceil(pax / maxPax)` returns 1 car instead of 2 and **undercharges the
customer**. Until Technoheaven confirms, prefer `vehicleName`/`vehicleId` where
present and treat `maxPax` as a fallback, and state the assumption in the reply.

**Display rule (user's instruction):** show sharing and private as indicative
per-unit prices until the customer gives a passenger count — without pax the two
are indistinguishable. Once pax is known, show the computed comparison.

Also in this phase: the two bugs above — verify the `get_tour_options` fix end
to end, and fix `_tour_price_lookup` returning `0.0`.

**Also worth doing:** wire `Getoptiondescription`
(`/api/v1/tourservices/TourSearch/Getoptiondescription`). Tested live, returns 8
sections — Inclusions, Exclusions, Useful Information, Option Wise Cancellation
Policy (+ description), Option Wise Child Policy (+ description), Cancellation
policy — as plain text and HTML. **We do not call it anywhere today**, so
inclusions and cancellation terms are missing from variant replies.

---

## Dependency order

```
Phase 1 (DB) ──> Phase 2 (API) ──> Phase 3 (UI) ──> Phase 4 (Railway)
Phase 5 (pricing) ── independent, can run in parallel from the start
```

Only Phase 5 is genuinely parallelisable. Phases 1→2→3 are a hard chain: the API
cannot be written before the schema exists, and the UI cannot be written before
the endpoints exist.

---

## Decisions needed from the user

1. **`DATABASE_URL`** — required to start Phase 1.
2. **Auth scope** — shared access code (hours) or real email/password accounts
   (about a day)? POC suggests the former.
3. **Order confirmation** — DB → API → UI → pricing, given the API must exist
   before the UI. Or pull Phase 5 forward, since wrong transfer prices are a
   live customer-facing bug the client already raised.

---

## On running these as parallel agents

Worth being straight about this rather than fanning out for its own sake.

**Phase 5 is a good parallel candidate** — it touches `rules.py`,
`mcp_tools/get_tour_options.py` and `agent_tools.py`, none of which Phases 1–4
touch. It can start immediately and run independently.

**Phases 1–3 are not.** Each needs the previous one's output as its input: the
API's repository layer is written against the schema, and the UI client is
written against the endpoints. Running them concurrently means agent 2 inventing
a schema and agent 3 inventing endpoints, then reconciling three mismatched
guesses — slower than doing them in order, and a common way to produce code that
compiles but does not work together.

A better use of parallelism within the chain: once Phase 1 lands, Phase 2's
endpoints and Phase 3's components can be built against a written API contract
in parallel. That is worth doing, and the contract has to exist first.
