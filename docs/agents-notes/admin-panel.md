# AGENTS.md notes: admin panel (`feat/admin-panel`)

Facts to fold into AGENTS.md when `feat/global-admin-otel` merges. AGENTS.md itself was
not edited on this branch (parallel branches touch it).

## §3 Repository layout

- `api/` gains `admin.py` (router `/admin-api`, `require_admin`), `admin_repository.py`
  (admin SQL + `add_search_rows`), `search_log.py` (pure: tool calls -> search_log rows).
- `migrations/` now runs 001..009.

## §7 Chat API

Endpoints (all GET, read-only, `require_admin`: signed-in AND email in `ADMIN_EMAILS`,
else **404** `{"detail":"Not Found"}`, also when signed out; bad UUIDs 404 too):

| Path | Purpose |
|---|---|
| `/admin-api/overview?days=30` (1-365) | KPIs (total/new/active users, chats, turns, searches, failed, thumbs up/down), `daily` series (active_users, signups, turns, searches, failed_searches; days in Asia/Kolkata), `searches_by_tool`, `top_destinations` (top 10, case-insensitive), `recent_failed` (10) |
| `/admin-api/users?q=&page=&page_size=` (≤100) | accounts with last active (last customer message), chats, messages, searches; sorted by last active |
| `/admin-api/users/{id}` | profile, `facts` (user_memory), saved cards count, searches, `last_active`, sessions (title, pinned, created, updated, messages, searches) |
| `/admin-api/sessions/{id}/messages` | full transcript: messages with `options` (cards), `feedback`, `tool_calls` (input + `output_preview` cut to 2,000 chars, `output_chars`); writes one `admin_audit` row (`view_transcript`, session id) per read |
| `/admin-api/searches?tool=&user_id=&q=&errors_only=&from=&to=&page=&page_size=` (≤200) | search_log rows newest first with the customer email; `q` matches destination, origin, email, error, args text; `from`/`to` are IST calendar days; also returns `tools` (distinct tool names) |

- `/auth/me` returns `user.is_admin` (bool). Login/register responses do not.
- `search_log` rows are written in `_turn_events` right after the assistant reply is saved,
  from the same `tool_calls` list (`search_rows()` → `admin_repository.add_search_rows`),
  inside try/except: a failure logs `search_log_write_failed` and never fails the turn.
- Admin endpoints are the one deliberate exception to "every query has user_id in WHERE";
  they read across accounts and are reachable only through `require_admin`.

Migration `009_search_log` (idempotent):

- `search_log`: id, user_id (CASCADE), session_id (CASCADE: deleting a chat deletes its
  searches), message_id (SET NULL: a regenerated reply keeps its searches), call_index,
  tool, args JSONB (args > 8,000 chars stored as `{"_truncated": true}`), origin,
  destination, start_date, party, result_count, error, latency_ms (always NULL for now),
  created_at. Indexes: (created_at), (user_id, created_at), (tool), UNIQUE
  (message_id, call_index) so a reply is never logged twice.
- `admin_audit`: admin_email, action, target, created_at.
- Backfill on every boot for assistant replies that have no search_log rows yet
  (`NOT EXISTS` + `ON CONFLICT DO NOTHING`). Helper SQL functions `search_log_try_jsonb`,
  `search_log_int`, `search_log_party` mirror `api/search_log.py`: keep both in step.
- Derivation rules: destination = destination_city | destination_country | destination |
  city (NOT hotel_name: transfers name a hotel); origin = origin_city | origin; start date =
  check_in | travel_date | departure_date | arrival_date | search_date | start_date | date;
  party = "2 adults, 1 child, 1 infant, 1 room" from adults (or sum of `rooms[].adults`),
  children (or `len(child_ages)`), infants, rooms (int or list length); result_count =
  len(options) | total_results | len(results) | len(items); error = output `message` when
  `error` is truthy (else the error string, else "failed"), or plain-text output starting
  with "Error".
- Every tool call is logged, including helpers (plan, lookup, exchange rate); the UI groups
  tools into services (`lib/admin.ts serviceOfTool`).

Data table addition: "What each customer searched | `search_log`"; "Admin transcript reads |
`admin_audit`".

## §9/§10 Web

- `next.config.ts` rewrites also proxy `/admin-api/:path*`.
- Routes: `src/app/admin/layout.tsx` (guard: `api.me()`, not `is_admin` → `/chat`,
  401 → `/login`; desktop 240 px sidebar, phones a sticky top bar with tabs; the page
  itself scrolls), `admin/page.tsx` (Overview: 8 KPI tiles with sparklines, columns per
  day for active users, messages, searches with failed inside, bars per service and top
  destinations, recent failures), `admin/users/page.tsx` (search + table, cards below
  `md`), `admin/users/[id]/page.tsx`, `admin/chats/[id]/page.tsx` (read-only transcript),
  `admin/searches/page.tsx` (filters in the URL: text, tool via custom `Select`, from/to,
  "Failed only"; table below `md` becomes cards; 50 per page).
- `components/admin/AdminUi.tsx`: `PageHeader`, `Panel`, `KpiTile`, `Sparkline` (SVG),
  `ColumnChart` and `BarList` (CSS), `Pager`, `StatusPill`, `Loading` (TravelLoader),
  `ErrorNote`, `Empty`. No chart library. `.admin-bar` / `.admin-bar-x` grow-in animations
  in globals.css (cut by the reduced-motion rule).
- `MessageList` takes `readOnly` (no edit, retry, regenerate, rating buttons or
  suggestion chips; shows "Customer rated this reply good/bad") and `extraFor(m)`
  (the admin transcript puts each reply's time and tool calls there).
- `CardActions.readOnly`: `HeartButton` and `BookButton` render nothing.
- `Sidebar` shows an "Admin" item (ShieldCheck) above Settings only when `user.is_admin`.
- `User.is_admin?: boolean` in `lib/types.ts`. `lib/admin.ts` holds the admin response
  types and pure helpers (tested in `admin.test.ts`). `api.ts`: `adminOverview`,
  `adminUsers`, `adminUser`, `adminTranscript`, `adminSearches`; mock mode stubs in
  `mock.ts` build them from the browser's mock chats, and the mock user is an admin.

## §11 Testing

- `tests/test_search_log.py` (pure extraction).
- `tests/test_admin_api.py`: access rule without a DB; with a LOCAL Postgres in
  `DATABASE_URL` (127.0.0.1/localhost) it also runs migrations twice, checks the backfill
  equals `search_rows()` and adds no duplicates, and calls every endpoint as admin,
  customer and signed out (skipped when no local DB). It creates and deletes its own users.
- Web: `src/lib/admin.test.ts`.
- QA screenshots: `docs/admin-qa/` (1440 px and 390 px).

## §12 Environment

- Chat API: `ADMIN_EMAILS` (comma separated, case-insensitive; read per request; empty
  or unset = nobody is an admin). Set it on the chat-api Railway service before deploying.

## Gaps

- `latency_ms` is not filled (the agent's tool_calls carry no timing); the OpenTelemetry
  branch can supply it.
- The live write path was not exercised by a real agent turn locally (the local
  OpenRouter key returned 401); it is covered by `add_search_rows` tests and the same
  extraction the backfill test checks.
- The backfill re-checks replies without rows on every boot; cheap at today's volume.
