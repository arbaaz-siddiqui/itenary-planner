# release/2026.10 — production plan

Integration branch: `release/2026.10`, cut from `origin/chat/nextjs/development` at
`f2a6683`. Every phase below is a `fix/*` or `feat/*` branch that merges here. Nothing
touches a deployment branch until the release gate in §12 passes.

## Revert points

Created before any new work, so every deployed surface can be restored exactly:

| Tag | Commit | Surface |
|---|---|---|
| `deployed/2026.09/chat-nextjs` | `f2a6683` | Next.js chat (primary) |
| `deployed/2026.09/chat-streamlit` | `9e5d75b` | Streamlit chat |
| `deployed/2026.09/voice` | `8226463` | LiveKit voice |
| `deployed/2026.09/whatsapp` | `8226463` | WhatsApp |

`release/2026.09` also points at `f2a6683` as a branch-shaped snapshot. Both release
branches have their upstream deliberately unset, so a stray `git push` cannot reach a
deployment branch.

**Already merged into `release/2026.10`:** the PDF download 404 fix (API route, Next
proxy, download card, flight-card indexing), the reply guards (invented PDF links,
invented results, repeated refused calls), the flight origin recovery, and the verified
supplier API reference.

---

## 1. What we are building

A chat agent that is also a booking surface. The customer types; the agent answers in
prose *and* the answer arrives as real, clickable inventory cards carrying the actual
supplier payload. Clicking a card is a conversational act, not a detour out of the chat.

Three properties decide whether this feels like a product or a demo:

1. **The agent and the UI read the same data.** One retrieval layer, one payload, two
   renderers. They cannot disagree.
2. **State survives everything.** Closing the tab mid-answer, switching devices,
   switching accounts, a dropped connection.
3. **It stays fast.** Every feature below is costed against a latency budget (§11).
   A correct answer that takes 40 seconds has already lost the customer.

---

## 2. Design direction

The Lovable reference pins the direction: dark chrome, collapsible left rail holding the
chat history, a centred greeting, and the composer as the hero of the empty state.
That structure is adopted as given.

One reconciliation. The reference's colour field is a single atmospheric wash behind an
otherwise empty screen — it is the hero, and it appears once. That is different from
decorative gradients sprinkled across a UI, which is what "no AI slop" rules out. So:

**The atmosphere exists in exactly one place** — behind the empty-state greeting, before
the first message. The moment a conversation starts it is gone and the surface is flat,
quiet, and built for reading dense inventory. Nowhere else in the product has a gradient.

This supersedes the light "limestone" palette I proposed previously; the reference is
dark and the reference wins.

| Token | Value | Role |
|---|---|---|
| `bg` | `#0C0D0F` | app ground |
| `surface` | `#141619` | rail, cards, composer |
| `surface-2` | `#1C1F23` | hover, inset fields |
| `border` | `#272B31` | hairlines, card rules |
| `fg` | `#EDEEF0` | primary text |
| `fg-muted` | `#8B9198` | secondary text |
| `accent` | `#E8B04B` | one accent: primary action, focus, selected state |
| `danger` | `#E5776B` | errors only, never the brand |
| `ok` | `#6FB98F` | confirmed selections, refundable badges |

Accent is a warm amber rather than the default blue or violet. It reads as a boarding-
pass stamp against the dark ground, it is legible at small sizes on `#0C0D0F`, and it is
not the colour every generated dashboard reaches for.

**Type**: one family. Inter Tight for UI and headings, with
`font-variant-numeric: tabular-nums` global — this product is numbers, and prices must
align down a column. No second display face; scale and weight carry hierarchy.

**Density**: the rail and chat are comfortable; cards and detail tables are tight. A
consultant comparing nine hotels needs rows, not air.

**Banned outright**, because they are the current signatures of generated design:
identical rounded cards with the same soft grey shadow; gradient washes as decoration;
all-caps tracked eyebrow labels; middot-joined meta strings; monospace faces used for
small labels; arrows appended to button text; glassmorphism.

---

## 3. Phase 0 — Contract and schema

**Branch:** `feat/contract-and-schema` · **1 day** · blocks everything

The single cause of both the backend sprawl and the unusable UI is that tools return
free-form dicts, so the UI has to reverse-engineer cards out of the model's prose
(`web/src/lib/resultTable.ts` literally parses the rendered markdown AST for a 7-column
table). Fix the contract once, and both sides can build in parallel.

```python
class Row(BaseModel):
    id: str
    kind: Kind
    title: str
    subtitle: str
    price_inr: float | None
    price_basis: Literal["per_adult", "per_group", "per_room_night", "total", "on_request"]
    priced_for_pax: int
    badges: list[Badge]          # {label, tone: neutral|ok|warn}
    image: ImageRef | None       # {src, alt, ratio}
    detail_ref: str
    facets: dict[str, str]       # kind-specific display pairs, already formatted

class ToolResult(BaseModel):
    ref: str                     # artifact id; full payload in Postgres
    kind: Kind
    summary_md: str
    rows: list[Row]              # <= 5 inline; the rest via the artifact
    facts: dict[str, Any]        # priced_for_pax, currency, as_of, total_available
    more: MoreInfo | None
    degraded: Degraded | None
    suggest: list[Suggestion]
```

Mirrored in `web/src/lib/contract.ts`, with one real fixture per kind captured from the
live supplier through `scripts/sample_all_apis.py`. A fixture must load in both a pytest
and a Vitest test.

**Schema migrations** (`003_accounts.sql`, `004_turns.sql`, `005_artifacts.sql`):

```sql
ALTER TABLE users ADD COLUMN first_name TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN last_name  TEXT NOT NULL DEFAULT '';

CREATE TABLE password_reset_tokens (
  token_hash  TEXT PRIMARY KEY,           -- store the hash, never the token
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at  TIMESTAMPTZ NOT NULL,
  used_at     TIMESTAMPTZ
);

CREATE TABLE artifacts (
  ref TEXT PRIMARY KEY, thread_id TEXT NOT NULL, kind TEXT NOT NULL,
  params JSONB NOT NULL, payload JSONB NOT NULL,
  as_of TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE trip_state (
  thread_id TEXT PRIMARY KEY,
  party JSONB NOT NULL DEFAULT '{}', trip_window JSONB NOT NULL DEFAULT '{}',
  budget_inr NUMERIC, selections JSONB NOT NULL DEFAULT '[]',
  last_refs JSONB NOT NULL DEFAULT '{}', open_questions JSONB NOT NULL DEFAULT '[]',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Turn journal: what makes an interrupted answer recoverable (see Phase 5).
CREATE TABLE turns (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('streaming','complete','failed','cancelled')),
  user_text TEXT NOT NULL,
  partial_text TEXT NOT NULL DEFAULT '',
  seq BIGINT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at TIMESTAMPTZ
);
```

**Gate:** contract shapes agreed in both languages; migrations run idempotently; one
fixture per kind loads on both sides.

---

## 4. Phase 1 — Auth

**Branch:** `feat/auth-screens` · **2 days** · depends on Phase 0 schema

Screens: **sign up** (first name, last name, email, password), **sign in**,
**forgot password**, **reset password**. Design tokens from §2 are established here and
every later screen consumes them.

- `POST /auth/register` gains `first_name`, `last_name`. Validation server-side, with
  field-level errors the UI can attach to the right input.
- `POST /auth/forgot` — always returns 204 regardless of whether the email exists.
  Telling an anonymous caller which addresses have accounts is an enumeration leak.
- `POST /auth/reset` — single-use token, 30-minute expiry, hash stored not the token,
  all existing sessions invalidated on success.
- Rate limit both reset endpoints per IP and per account.
- Password rules: minimum 10 characters, checked against a common-password list. No
  composition rules — they push people toward `Password1!` and weaken outcomes.
- Sessions: httpOnly, `SameSite=Lax`, `Secure` in production, 30-day sliding expiry.

Email delivery is a dependency, not a detail. Pick the provider in this phase (Resend or
SES) and put the sender domain's SPF/DKIM in place, or password reset ships broken.

**Gate:** all four screens keyboard-navigable with visible focus, correct at 360px, light
paths and error paths styled; reset token single-use and expiring; register rejects a
duplicate email with a usable message.

---

## 5. Phase 2 — App shell, sessions, and every persistence scenario

**Branch:** `feat/app-shell-and-sessions` · **3 days** · depends on Phase 1

The Lovable structure: collapsible left rail, chat history grouped by recency, account
control pinned at the bottom, and an empty state reading **"Hi {first_name}, where are
we going?"** over the single atmospheric field.

Rail behaviour: collapse state persists per device in `localStorage`; on mobile it is a
sheet, not a squeeze. Sessions grouped Today / Yesterday / Previous 7 days / Older.
Auto-titled from the first user message after the turn completes, renameable, deletable
with undo.

**The scenarios, each specified rather than assumed:**

| Scenario | Behaviour |
|---|---|
| Sign in on a second device | Sessions and transcripts are server-side, keyed by `user_id`. The list and every message are already there. Nothing is in `localStorage` except the rail's collapsed flag. |
| Close the tab mid-answer | The turn keeps running server-side. `turns.partial_text` is written on a 500ms debounce. |
| Reopen after closing mid-answer | The transcript loads with the partial reply and a "still writing" state; the client reattaches to the live stream by `turn_id`. |
| Refresh mid-answer | Same path — reattach, never restart. A restart would re-run the supplier calls. |
| Network drops mid-stream | SSE carries `Last-Event-ID`; the client reconnects and replays from `seq`. Three failed reconnects fall back to polling the turn row. |
| Server restarts mid-answer | The turn is marked `failed` by a startup sweep. The UI shows what was written plus a Retry action. |
| Two tabs, same session | Both attach to the same turn stream and see identical output. The composer is disabled in a tab where a turn is in flight. |
| Switch account | Account switcher in the rail. Signing in as a second account stores a **separate** session cookie under a namespaced name; switching swaps the active one with no re-entry of a password. Sign-out clears only the active account. |
| Sign out everywhere | Bumps a `session_epoch` on the user row, invalidating every issued cookie. |

Today the assistant reply is only persisted *after* the stream finishes
(`api/main.py:_stream_turn`), so an interrupted turn is lost permanently. The `turns`
journal from Phase 0 is what fixes that, and it is the foundation for every row above.

**Gate:** each scenario in the table has a Playwright test that kills the connection or
the tab at a scripted point and asserts recovery.

---

## 6. Phase 3 — Retrieval layer: envelope, artifacts, and the fan-out fix

**Branch:** `fix/retrieval-layer` · **4 days** · depends on Phase 0

`retrieval/` becomes the only path to the supplier. The agent stops importing
`booking_api` and `parsers` directly.

```python
def search(kind, params) -> ToolResult   # ONE supplier round trip. No enrichment.
def detail(kind, item_id, ctx) -> ToolResult  # deep, for one named item
```

**The fan-out today**: one `search_tours` call makes roughly 31–40 supplier calls — the
list, then one timeslot call per tour, then options and optionRate per tour, plus images
— with two stacked deadlines (8s then 14s) giving a 22-second floor. Rows that miss the
deadline come back looking as if the supplier publishes no rates, which is a claim we
never checked. `search_hotels` makes one `GetPropertyDescriptions` call per hotel.

After the split: tour search is 2 calls, hotel search is 1. Enrichment moves into
`detail()` and runs only for an item the customer named or the UI opened.

Also in this phase: replace `ThreadPoolExecutor` with `httpx.AsyncClient` and one
semaphore per endpoint family (start at 8). There is no global cap today — two concurrent
tour searches put 48 requests in flight from one worker. Cache moves onto the raw
supplier call rather than the enriched blob, so partial reuse works.

**Freshness policy — the agent never decides this, the layer does:**

| Class | Examples | Re-fetch when |
|---|---|---|
| Reference | city ids, country ids, entity lookup | never within a session |
| Static | descriptions, inclusions, reviews, amenities | 24h TTL |
| Live | price, availability, timeslots, ROE | age > 90s, or before quoting a total, or before booking, or on an explicit re-check |
| Session | party, dates, budget, selections | read from `trip_state`, never fetched |

Every result carries `facts.as_of`. Delete all ten `agent_instructions` strings; the
rules move to the system prompt once.

**Gate:** tour search ≤2 supplier calls, hotel ≤1; every result validates against
`ToolResult`; zero `agent_instructions` in the repo.

---

## 7. Phase 4 — Cards, detail drawer, Ask AI, images

**Branch:** `feat/interactive-cards` · **5 days** · depends on Phase 3 (startable on fixtures)

`resultTable.ts` and its tests are deleted. Cards render from `ToolResult.rows`.

**Per-kind card shapes**, because a flight and a hotel are not the same object and
forcing them into one rounded rectangle is exactly what makes a UI look generated:

- **Flight** — a boarding-pass horizontal: origin code, a route rule marked with the
  stop count, destination code, then times, duration, cabin, baggage, and the fare on
  the right. Return legs stack as a second band on the same card.
- **Hotel** — image-led, 3:2 photo, name, star row, area, board, cancellation tone
  badge, price with `per_room_night` stated.
- **Tour** — 16:9 photo, duration and category, the transfer tier summary, cancellation,
  and start times when real ones exist.
- **Transfer** — vehicle silhouette, capacity, luggage, direction, and whether it is
  private or shared, stated explicitly.
- **Restaurant / visa / package** — compact list rows; these are chosen, not browsed.

**Every card carries three actions:** Details, Select, and **Ask AI**.

*Ask AI* is the feature that makes the UI agentic rather than a parallel read-only view.
It opens a small prompt anchored to that card and posts
`{thread_id, ref, item_id, question}`. The server resolves the item's full payload from
the artifact, injects it as scoped context, and the agent answers **about that specific
item** in the main thread. The customer never has to describe which row they mean, and
the agent never has to guess — which is the failure the POC has today, where it replies
"the Gujjutours one or the Rayna one?" to a customer looking at a screen.

**Detail drawer** shows the complete supplier record for the item: every room and rate
with its own cancellation ladder, all transfer tiers, all timeslots, inclusions,
exclusions, amenities, address, policies, images. Served by the same `detail()` the agent
calls.

**Endpoints:**
```
GET  /api/artifacts/{ref}                  rows 6..N, no supplier call
GET  /api/artifacts/{ref}/items/{id}       one item, enriched via detail()
POST /api/artifacts/{ref}/items/{id}/ask   Ask AI
POST /api/trip/{thread_id}/select          record a choice
```

**Images — treated as the named requirement it is.** Today `next.config.ts` sets
`images.unoptimized: true` and `SafeImage.tsx` renders a bare `<img>` with an `onError`
fallback, because supplier hosts are unknown ahead of time and some URLs 404. Fix:
`GET /api/img?src=<url>&w=<width>` which validates the host against an allowlist built
from observed supplier domains, fetches server-side, caches to disk or S3, resizes, and
serves a designed placeholder on failure. The browser then only ever talks to our origin.
Cards reserve aspect-ratio space so nothing reflows as images land.

**Gate:** clicking any card opens real API data; Ask AI produces an answer naming that
item; no card derives from model prose; zero broken-image icons across the full fixture
set; no layout shift on image load.

---

## 8. Phase 5 — Trip state and the selection loop

**Branch:** `fix/trip-state` · **2 days** · depends on Phase 3

`apply_selection_tool` currently takes `current_spent` as an **argument** — the model is
asked to carry a running total between turns and hand it back, which is why totals drift.
So does `compute_remaining_budget_tool`.

Tools read and write `trip_state` server-side and take no totals. The model cannot get
the arithmetic wrong because it never does the arithmetic. A 250–350 token rendered state
block is prepended each turn.

**A click is a turn.** Selecting from a card writes `trip_state` and injects a synthetic
user turn (`[ui] selected Rove Downtown, 4 nights`). The agent responds — confirming,
showing the new total, noting that no airport transfer is chosen yet.

The 13 calculator tools are deleted and become server functions.

**Gate:** no tool takes a running total; totals match hand calculation across the journey
evals; a UI selection always produces an agent response.

---

## 9. Phase 6 — Memory and context

**Branch:** `fix/memory-tiers` · **2 days** · depends on Phase 5

| Tier | Store | Lifetime |
|---|---|---|
| Working | LangGraph checkpoint | current turn |
| Session | `trip_state` + `artifacts` | the trip |
| Long-term | `user_memory` | across sessions |

**Working memory must be capped.** After 6 turns, older tool messages are rewritten to
`{ref, digest}`; the payload stays retrievable from `artifacts`. Without this, LangGraph
replays every 200KB payload on every turn and both latency and cost grow without bound.

**`user_memory` is dead code today.** The table exists in `001_init.sql` and
`api/main.py:284` reads it on a session's first message, but nothing writes it —
`api/repository.py` has `get_memory` and no setter, so the table is always empty and the
read path has never fired. Add the writer: after each turn, extract a **fixed** key set
and upsert. Fixed keys only; an open-ended "remember anything" store is an unreviewable
prompt-injection surface.

```
home_city · nationality · default_adults · default_children
budget_band · cabin_pref · hotel_star_pref · dietary
```

Injected every turn, not only the first. Always confirmed before booking.

**No vector database.** The useful facts here are keys, not paragraphs, and structured
lookup answers them exactly where embeddings would answer approximately. Revisit only if
semantic search across the ~270 tour descriptions becomes a real requirement.

---

## 10. Phase 7 — Guardrails, observability, tracing, evals

**Branch:** `feat/guardrails-and-observability` · **4 days**

### 10.1 Guardrails

NeMo Guardrails, with a deliberate split, because the naive configuration makes an LLM
call per rail and would add seconds to every turn:

- **Input rails — embedding-based, not LLM.** Jailbreak and injection detection, PII
  detection, off-topic routing. Local embedding model, single-digit milliseconds.
- **Dialog rails — LLM, only where they earn it.** Booking confirmation and payment
  intent, where a wrong turn costs money.
- **Output rails — deterministic Python, not LLM.** These are assertions over the
  envelope, and they are both faster and stricter than a model judging its own output:
  - every price in the reply appears in a `ToolResult` from this turn
  - no claim of availability without a matching `as_of` inside the freshness window
  - no download link without a matching `itinerary_id` in the thread
  - a degraded result is always disclosed as degraded
  - supplier totals are never multiplied by party size

The reply guards already merged into `release/2026.10` (invented results, invented PDF
links) become the first three output rails rather than ad-hoc functions in `agent.py`.

### 10.2 Observability

- **OpenTelemetry** across the whole path: one trace per turn, spans for model call,
  each tool, each supplier request, each DB query. `booking_api/http_client.py` already
  records method, URL, status and duration for every outbound call — that recorder
  becomes the span emitter rather than a separate in-memory deque.
- **LangSmith** (already a dependency) for prompt, token and tool-call inspection.
- **Prometheus metrics**: turn latency by surface, supplier calls per turn, cache hit
  rate, breaker state, tool error rate, guardrail trigger counts, tokens per turn.
- **structlog** already in use — bind `trace_id`, `thread_id`, `user_id` to every line.
- A per-turn trace view in the UI's debug drawer, so a support question is answerable
  without a log search.

### 10.3 Evals

`scripts/eval_journey.py` becomes the release gate; the 42 one-test-per-past-bug files
are replaced by envelope contract tests plus journeys.

Twelve scripted journeys: family of four on a budget; solo business traveller; group of
twelve hitting the 9-pax cap; a returning customer with stored memory; a supplier outage;
a sold-out date; a mid-conversation date change; a "what else do you have" probe; a
price re-check before booking; a shared-versus-private transfer question; an Ask AI
question against a specific card; a voice call.

Each asserts supplier call count, wall-clock, arithmetic correctness, and that no
unchecked claim appears. Plus a **golden-facts suite** (§10.4) and an LLM judge for tone
and completeness only — never for correctness, which is asserted deterministically.

### 10.4 Golden facts — the non-regression suite

These are supplier behaviours discovered the hard way and currently encoded implicitly.
A refactor can silently drop any of them, so each gets a test that fails loudly:

- `toursearchlistrate` carries one flat rate; the shared/private split exists **only** in
  `optionRate → initialTransferRates` with `transferId=3`.
- A "Without Transfer" tour has no pickup, so it has no split. This is **not** the same
  as a tour whose rates are unpublished for that date. Neither may be reported as the
  other.
- Roughly 25% of tours return a `00:00` placeholder timeslot; it is not a real start
  time and must be stripped.
- "Per group" options are billed per guest by the supplier despite the name. Quote what
  the supplier bills and state the basis.
- **Tour transfers are priced by tier; airport transfers are priced by distance.** They
  are different products from different endpoints and must never be explained with each
  other's logic.
- Visa pricing requires nationality id `245`; `INR = (price - fee) * (1 / buyingROE)`.
- Supplier base is AED: `/ buyingROE` when the currency matches the account currency,
  else `* sellingROE`. The ROE pair is fetched once per turn and cached.
- Website price differences come from `rateCategory`, not ROE. Never hardcode a markup.
- Caps: flights 9 pax; hotels 9 rooms, 30 nights, 2 children per room, ages 1–12;
  booking horizon ~12 months.
- `GetPropertyDescriptions` returns an empty list for more than one hotel id and does not
  echo the id back — it is one hotel per call.
- Transfer `hotel_lat`/`hotel_lng` are optional; the name alone resolves coordinates.

---

## 11. Speed

Latency is a feature with a budget, and every phase above is costed against it.

| Surface | p50 | p95 |
|---|---|---|
| Chat, no tool call | 1.2s to first token | 2.5s |
| Chat, one search | 3.0s to first card | 6.0s |
| Chat, full reply | 5.0s | 9.0s |
| Voice | 1.5s to first audio | 3.0s |
| Card detail drawer | 0.8s | 2.0s |

**Where the time actually goes, measured on this codebase:**

1. **Reply length dominates.** Tools return in about 5 seconds and the model then spends
   30–50 seconds writing. The POC's 2600-token cap is the single biggest cost. Caps
   become 300 for voice, 800 for chat and WhatsApp — the tables carry the detail, the
   prose does not.
2. **Fan-out** — Phase 3 removes roughly 38 supplier calls from a tour search.
3. **Checkpoint replay** — Phase 6's compaction stops 200KB payloads being re-sent each
   turn.
4. **Tool schemas** ship on every request. 34 tools collapse to 12: five `search_*`,
   `get_detail(kind, id)` replacing seven per-kind tools, `lookup_entity`,
   `get_visa_info`, `select`, `plan_itinerary`, `generate_pdf`.
5. **Prompt size** — 73KB across five files, of which v1 (23KB) and v2 (29KB) are dead
   (`agent.py:131` loads v3). Consolidate to `core.md` + `rendering.md` + a small surface
   file, under 2,500 tokens assembled.

**Self-hosted GPU serving**: vLLM with prefix caching (the system prompt and tool schemas
are identical every turn — a large free win), continuous batching, and **guided decoding
with the tool JSON schema as grammar**. That last one makes malformed tool calls
structurally impossible, which lets us delete roughly 300 lines of repair code in
`agent.py` (`_split_concatenated_args`, `_repair_thread`, `_recover_empty_turn`,
`_guard_empty_turn`, malformed-tool-name recovery). Keep one retry guard. A hosted API
model stays configured as a failover route behind a flag, so a GPU node restart degrades
quality rather than taking the product down.

**Perceived latency**: cards stream the instant the tool returns, before the prose is
written; tool chips show live progress; skeletons reserve card space.

**Enforcement**: p95 is asserted in the journey evals. A phase that regresses it does not
merge.

---

## 12. Sequencing and release gate

```
Phase 0  ██                      contract + schema          blocks all
Phase 1    ████                  auth
Phase 2        ██████            shell + sessions + recovery
Phase 3    ████████              retrieval layer            blocks 4,5
Phase 4            ██████████    cards + drawer + Ask AI + images
Phase 5            ████          trip state
Phase 6                ████      memory
Phase 7            ████████      guardrails + observability + evals
```

Roughly four weeks with three developers. **Critical path is 0 → 3 → 4** — everything the
client will judge sits at the end of that chain. Phases 1 and 2 run alongside it on the
frontend track and do not block it.

**Release gate — `release/2026.10` → deployment branches**

*Performance* — every budget in §11 met at p95; tour search ≤2 supplier calls, hotel ≤1;
checkpoint size flat across 20 turns.

*Correctness* — all golden facts in §10.4 green; supplier totals never multiplied;
unchecked facts never stated as supplier facts; totals sourced from `trip_state`.

*Resilience* — every scenario in §5 has a passing test; a supplier 500 produces an honest
degraded reply, never a blank turn.

*Product* — every card clickable with real API data; Ask AI answers about the named item;
zero broken images; all four auth screens complete.

*Code health* — zero `agent_instructions`; zero tools taking a running total; zero
`ThreadPoolExecutor` in `retrieval/` or `mcp_tools/`; every tool result validates.

Merge order at release: `chat/nextjs/development` first, soak 48 hours, then
`voice/development` and `whatsapp/development`. Roll back by resetting to the matching
`deployed/2026.09/*` tag.
