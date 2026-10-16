# AGENTS.md

Guide for AI coding agents working in this repository. Read it before changing code. It covers what the product is, how the code is laid out, how a request flows end to end, the UI system, pricing rules, testing, and how branches deploy.

Facts here were checked against the code on `fix/enhance-ui` / `release/2026.10` in October 2026. When something here and the code disagree, the code wins; fix this file in the same change.

---

## 1. What this is

An AI travel agent for **GujjuTours** (supplier platform: ActivityLinker), focused on Dubai. A customer chats ("3 nights in Dubai for 2 in November") and the agent searches live supplier inventory (flights, hotels, tours, transfers, restaurants, visas, packages), quotes prices in INR, builds day-by-day itineraries and PDF quotes.

Pieces:

| Piece | Where | Runs as |
|---|---|---|
| Booking engine: supplier HTTP calls, parsers, pricing, business rules | root `*.py`, `booking_api/`, `mcp_tools/` | library |
| LangGraph ReAct agent | `agent.py`, `agent_tools.py`, `prompts/` | library |
| Chat API (Next.js backend, Postgres) | `api/`, `migrations/` | `uvicorn api.main:app` |
| Web chat UI | `web/` (Next.js 15) | `npm run dev` / `start` |
| Streamlit surface (older chat UI, still live) | `surfaces/streamlit_app.py` | `streamlit run` |
| WhatsApp surface (Twilio) | `surfaces/whatsapp_app.py` | `uvicorn` |
| Voice (Vapi / LiveKit) | `voice_service.py`, `voice_livekit.py`, `surfaces/voice_app.py` | `uvicorn` / worker |
| MCP server | `surfaces/mcp_server.py` | `python -m surfaces.mcp_server` |

The current product focus is the **web UI + chat API** pair.

---

## 2. Commands

### Python (run from the repo root)

- Setup: `python3.12 -m venv venv`, activate (`venv\Scripts\activate` on Windows, `source venv/bin/activate` elsewhere), then `pip install -r requirements.txt`. `requirements.txt` has exact pins plus `psycopg[binary,pool]` and `langgraph-checkpoint-postgres`, which the chat API needs and which `pip install -e ".[dev]"` does NOT install. LiveKit worker: also `pip install -r requirements.livekit.txt`.
- The local venv folder in this checkout is `venv/` (not `.venv`). Always run Python through it (`venv/Scripts/python.exe` on Windows); the system Python lacks the dependencies.
- Tests: `pytest`. Config is in `pyproject.toml` `[tool.pytest.ini_options]` (testpaths `tests`, `-ra --strict-markers -v`). There is no `pytest.ini`.
  - One file: `pytest tests/test_core.py`. One test: `pytest tests/test_core.py -k "name"`.
  - The full suite (~880 tests, ~50 files) takes over an hour on this machine because some tests still hit FX code paths (see §11). For a change, run the files that cover it.
- Lint / format: `ruff check .`, `ruff format --check .`, `ruff format .`.
- Types: `mypy .` (pre-commit excludes `tests/` and `scripts/`).
- Pre-commit: `pre-commit install`, `pre-commit run --all-files` (large files > 500 KB, ruff format, ruff --fix, mypy, detect-secrets with `.secrets.baseline`).

### Services

- Chat API: `uvicorn api.main:app --reload --port 8000`. This machine usually runs it on **8010** (`--host 127.0.0.1 --port 8010`) because Docker holds 8000.
- Web UI: `cd web && npm install && npm run dev` (http://localhost:3000). Point it at the API with `API_PROXY_TARGET=http://127.0.0.1:8010` when the API is not on 8000.
- Streamlit: `streamlit run surfaces/streamlit_app.py`.
- WhatsApp: `uvicorn surfaces.whatsapp_app:app --reload --port 8000`.
- MCP server: `python -m surfaces.mcp_server`.
- Voice (Vapi webhook, production): `uvicorn voice_service:app --port 8100`. Voice bridge for the separate voice layer: `uvicorn surfaces.voice_app:app --port 8100` (see `docs/voice_test_runbook.md`). LiveKit worker: `python voice_livekit.py start`.
- Local Postgres: Docker container `gt_pg`, host port 55433. Use `127.0.0.1`, **not** `localhost`, in `DATABASE_URL`: on this machine `localhost` resolves to IPv6 first and the Docker IPv6 forward stalls ~30 s per connection, which makes boot-time migrations time out.
- Health: `curl http://127.0.0.1:8010/health` (DB-free by design) and `/health/db` (Postgres + table list).

### Web (`cd web`)

- `npm run dev`, `npm run build`, `npm start` (`next start -p ${PORT:-3000}`).
- `npm test` (Vitest, node env, pure TS), `npm run typecheck` (`tsc --noEmit`), `npm run lint` (not configured yet; it opens the Next ESLint setup prompt).
- `NEXT_PUBLIC_*` values are inlined at build time: changing one needs a rebuild/restart, not just an API restart.

### Scripts

- `python -m scripts.diagnose_apis` (`--quick`, `--only flight,hotel`): live supplier endpoint health.
- `python -m scripts.refresh_test_fixtures`, `python -m scripts.build_contract_fixtures` (writes `fixtures/contract/*.json`).
- `python -m scripts.grade_benchmarks`, `scripts.eval_agent`, `scripts.eval_journey`, `scripts.audit_apis`, `scripts.sample_all_apis`, `scripts.validate_against_samples`, `scripts/compare_flight_prices.ps1`.

---

## 3. Repository layout

```
core.py                  domain models, TripPlannerError hierarchy, to_inr / format_inr; imports nothing internal
settings.py              lazy @lru_cache pydantic-settings groups (Booking, Llm, Twilio, State, Currency,
                         Pricing, Http, Geo, Restaurant) + clear_all_caches()
llm.py                   build_llm(): anthropic | openrouter | selfhosted
parsers.py               every supplier response parser, data-quality filters, hotel_image_urls,
                         tenant-media URL rewrite (_TENANT_MEDIA)
rules.py                 business rules, normalize_reply, injection guard, set_conversation_text (ContextVar)
reference_data_loader.py reads reference_data/cities.json and reference_data/hotels/dubai.json
fx.py                    rates of exchange: live_rate_map, convert_supplier_price, _roe_pair
geo.py, text_match.py, scheduling.py, flight_cache.py, lead_scoring.py
itinerary_pdf.py, itinerary_store.py   PDF quotes (written to ITINERARY_DIR, default itineraries/)
contracts.py             typed tool-result envelope (see §6); mirrored in web/src/lib/contract.ts
agent.py                 LangGraph agent, streaming, guards, option extraction (§5)
agent_tools.py           plain tools + registry: _build_all_tools(), ALL_TOOLS, tools_for_surface()
voice_service.py, voice_livekit.py, livekit_config.py   voice stack
booking_api/             endpoints.py (call_* per supplier endpoint), headers.py, http_client.py
mcp_tools/               one module per tool, registered on the FastMCP instance in server.py (§4)
surfaces/                streamlit_app.py, whatsapp_app.py, voice_app.py, mcp_server.py, ui_theme.py
api/                     chat API: main.py, db.py, auth.py, repository.py
migrations/              001..006 *.sql, run on chat-API boot (root folder, not api/migrations)
prompts/                 system_prompt_v1/v2/v3.md, system_prompt_voice.md, whatsapp_addendum.md
scripts/                 diagnostics, fixtures, evals (above)
tests/                   pytest suite; tests/fixtures/ holds recorded supplier responses
fixtures/contract/       contract fixtures per kind (generated)
data/samples/, data_samples/   sample supplier / lead JSON
reference_data/          cities and Dubai hotels reference data
assets/                  PDF letterhead; template/Template.docx is the report template
docs/                    see §15
web/                     Next.js app (§9, §10)
railway*.toml, nixpacks.toml, deploy.sh   deployment (§14)
```

Generated and local-only (gitignored, never commit): `.env`, `railway-env*.txt` (contain live secrets: never print or paste them), `whatsapp_state.db*`, `benchmark_results.xlsx` (written by the turn logger), `itineraries/`, `*.log`, `venv/`.

`surfaces/README.md` ("12 tools") and `prompts/README.md` (v1 only) are stale.

### Architecture boundaries

- Strict top-down imports: `surfaces/` → `agent.py` + `agent_tools.py` → `mcp_tools/` + `booking_api/` → `parsers.py` + `rules.py` + `reference_data_loader.py` + `llm.py` → `settings.py` → `core.py`. Lower layers never import higher ones; `core.py` imports nothing project-internal.
- `api/` and `web/` are the chat stack. `surfaces/streamlit_app.py` stays working and separate; do not break it when changing shared code (it relies on the "Here are the top X ..." reply phrase via `CARD_TRIGGER_RE`).
- All errors inherit from `TripPlannerError` (`core.py`).

---

## 4. Tools (`mcp_tools/` and `agent_tools.py`)

Each `mcp_tools/<name>.py` defines an `_impl` and registers it with `mcp.tool(name=...)`. `agent_tools._build_all_tools()` (≈ line 1233) collects them plus agent-only tools; `tools_for_surface(surface)` filters per surface (voice drops `display_options_tool`, `build_trip_schedule_tool`, `generate_itinerary_pdf_tool`; WhatsApp drops the two UI tools). Every tool is wrapped by `_split_for_ui` → `ui_payload.wrap_tool` (§6).

| Kind | Tool name(s) | Notes |
|---|---|---|
| Flights | `search_flights`, `get_flight_details` | max 9 passengers (supplier rejects more); details need `conversationId` |
| Hotels | `search_hotels` (prices EVERY property in the city: `booking_api.city_hotel_catalogue` (GetStaticDataByCity, 4,510 Dubai IDs with `Category` + stars) filtered by stars (strict, unrated left out) and `property_type` (hotel, apartment, aparthotel, hostel, villa, resort), priced 200 IDs per call in parallel by `call_hotel_availability_many` (~6.5 s for the whole city), cheapest first; `refundable_only` keeps hotels whose shown (cheapest) rate has free cancellation, as the website's filter does; `area=` filter; results carry `property_type`, `stars` from each hotel's static record and `area`/`district` resolved from its address via `reference_data/dubai_areas.json`; the airport-transfer `next_step` only when the customer asked for a transfer); static content: `lookup_hotel_city`, `list_city_hotels`, `get_hotel_info`, `get_hotel_description`, `get_hotel_reviews` | static content uses the hotels-only token; 9 rooms / 30 nights / 2 children (age 1-12) max |
| Tours | `search_tours` (`theme=` adventure / romantic / family / culture / water / food, matched on the supplier's tour category via `reference_data/tour_themes.json`; `enrich=False` skips the slot and transfer fan-out), `get_tour_details`, `get_tour_timeslots`, `get_tour_options` (agent-only) | party totals per transfer type; `tour_pricing.py` |
| Transfers | `search_airport_transfer_dubai` (module `search_transfers.py`), `get_transfer_details` | hotel name alone resolves coordinates |
| Restaurants | `search_restaurants`, `get_restaurant_details` | only ~9 Dubai restaurants, all veg; `restaurant_catalog.py` |
| Visa | `get_visa_info`, `list_visa_countries` | Indian nationality uses supplier id 245. Every quoted price and its visa name go to `quote_registry` (`remember`, `remember_label`). Returns `refund_policy`, `track_times` (website wording), `party_totals`, and a `timeline` (working days to travel, which track fits) |
| Packages | `list_packages`, `get_package_details` | live price built like the website (`package_pricing.py`, see §8); `list_packages` prices the first 8 matching the nights asked |
| Lookup / FX | `lookup_entity` (public autocomplete), `get_exchange_rate` | |
| Package maths | `plan_itinerary_tool` (needs `adults`; tours carry `date`; `flight_fare_ref`; `fill_days` fills each day with priced tours; adds airport pickup/drop-off, visa and meals; returns `package_lines`; pricing helpers in `mcp_tools/plan_pricing.py`), `cancellation_breakdown_tool` (refund full/none/partial) | |
| Helpers | `result_cache.py`, `ui_payload.py`, `tour_pricing.py`, `quote_registry.py` (prices quoted per conversation; a transfer total must have been quoted) | |

`surfaces/mcp_server.py` does not import `lookup_entity`, `get_tour_timeslots` or `list_visa_countries`.

**Adding a tool:** `call_<thing>()` in `booking_api/endpoints.py` → model in `core.py` → `parse_<thing>_response()` in `parsers.py` → `mcp_tools/<thing>.py` → register in `agent_tools._build_all_tools()` → tests + fixtures → mention it in the active prompt if the model must know when to use it (`tests/test_prompt_tool_names.py` checks names). If it returns options for cards, add the kind to the web card system (§10).

**Adding a surface:** one file in `surfaces/`, call `build_react_agent(surface="<name>")`.

---

## 5. The agent (`agent.py`)

- **Prompt:** `SYSTEM_PROMPT_VERSION` (read at import, default `v3` → `prompts/system_prompt_v3.md`). `load_system_prompt` appends the IST date, a current-year rule and the surface name; voice adds `system_prompt_voice.md`, WhatsApp adds `whatsapp_addendum.md`. The v3 prompt has a ~3,000-token cap enforced by tests; trim before adding.
- **`build_react_agent(surface)`:** `max_tokens` 300 for voice, 2600 otherwise. `create_react_agent` with:
  - `ParallelToolNode` (runs tool calls concurrently, recovers malformed tool names),
  - `pre_model_hook=_supersede_earlier_searches` (stubs old restaurant results),
  - `post_model_hook=_split_concatenated_args` → `repair_inline_tool_call` (tool calls written as text), `{..}{..}` arg splitting, `_drop_repeated_failures`,
  - checkpointer: memory by default, `build_postgres_checkpoint` (chat API) or `build_sqlite_checkpoint` (WhatsApp).
- **Recursion limit:** 33 for chat (`_STEPS_PER_TOOL_ROUND * PLAN_TOOL_ROUNDS + 1`, 8 tool rounds; a built package needs 6+), 25 for voice.
- **Streaming:** `stream_and_log` returns a `StreamResult` (`text`, `messages`, `tool_event_log`, `latency_seconds`, `on_update` callback fired after every update chunk, `.response`). Stream modes `messages` + `updates`. `on_update` is how the chat API emits live tool steps.
- **Guards** (both paths): injection guard, malformed-args repair, empty-turn and unfulfilled-promise nudges (a lookup offered on the customer's answer is not a broken promise), `_apply_reply_guards` (invented results, fake PDF links, claiming an unbuilt PDF, visa guarantees removed and the tight-timeline note added, a missing visa party total added).
- **`_customer_part`** strips stacked leading `[system]` blocks the API prepends (mode, regenerate, memory) so rules see only the customer's words.
- **`extract_all_search_options`** returns the latest result **per kind** (one card group per kind), resolved through `ui_payload.full_result`.

---

## 6. Tool results: contract and the slim/full split

- **`contracts.py`** defines the target envelope (`ToolResult`: `ref`, `kind`, `summary_md`, `rows` ≤ 5, `facts`, `more`, `degraded`, `suggest` ≤ 3, `error`; `Row`, `ItemDetail`, `PriceBasis`), all `extra="forbid"`. TS mirror: `web/src/lib/contract.ts`, kept in step by `tests/test_contracts.py::test_typescript_mirror_is_in_step` (run it after changing either). Design rule: new behaviour means new values in existing fields, never new fields.
- **Reality today:** the contract is not enforced at runtime. Live tools return plain dicts with an `options` list; tours also use `price_basis` values `per_unit` and `party_total`, which are not in `PriceBasis`. Do not assume a live result validates against `ToolResult`.
- **`mcp_tools/ui_payload.py`:** the model gets a slim copy, the UI gets everything.
  - `split()` stores a deep copy of any result with a non-empty `options` list (in memory, TTL 3600 s, max 500) and returns a slim copy with `ui_ref` + `ui_note`.
  - Slim drops `image_url`, `image_urls`, `dish_photos`, `airline_logo`, `unique_key`, and `documents` / `opening_hours` / `meal_timings` when their short `*_display` form exists. Names, ids, prices, policies stay, so the model can answer about any card.
  - `full_result()` resolves `ui_ref` (falls back to the slim copy). The chat API stores the full items with the message, so cards survive reloads although the store is in memory.
  - `UI_NOTE` tells the model cards show per-person prices and that its reply must quote the party total from the result, never compute it.

---

## 7. Chat API (`api/`)

`api/main.py` (FastAPI), `api/db.py` (psycopg pool 1-10, `run_migrations`, loads `.env` with `override=False`), `api/auth.py` (PBKDF2 390k rounds, HMAC-signed `gt_session` cookie, 30-day TTL), `api/repository.py` (all SQL).

**Endpoints**

| Method | Path | Purpose |
|---|---|---|
| GET | `/health`, `/health/db` | liveness (no DB) / Postgres + tables |
| GET | `/itinerary/{id}.pdf` | generated PDF (public) |
| POST | `/auth/register` (201), `/auth/login`, `/auth/logout` (204) | `register` takes `first_name`, `last_name` and signs in |
| GET/PATCH | `/auth/me` | current user; PATCH updates names |
| GET/PATCH | `/me/preferences` | `origin_city`, `usual_party_size`, `diet`, `nationality` ↔ `user_memory.facts` (empty value clears) |
| GET/POST | `/chat/sessions`; PATCH/DELETE `/chat/sessions/{id}` | ordered by `updated_at DESC`; PATCH takes `title` and/or `pinned` |
| GET | `/me/saved-cards`; PUT/DELETE `/me/saved-cards/{card_id}` | hearted cards per account (`saved_cards`, ≤ 50, PUT body `{kind, item}`) |
| GET/PATCH | `/me/settings` | `{theme, sidebar_collapsed}` in `users.ui_settings` |
| GET | `/chat/sessions/{id}/messages` | includes `options` and `feedback` |
| GET | `/chat/sessions/{id}/status` → `{busy}`; POST `.../cancel` (204) | detached turns |
| POST | `/chat/messages/{id}/feedback` | `up`/`down`/null → 1/-1/NULL |
| GET | `/chat/messages/{id}/tool-calls` | the tool calls behind a reply (args, results); owner only |
| GET | `/tours/{tour_id}/options/{option_id}/details?supplier_id=` | tour option detail for the panel |
| GET | `/tours/{tour_id}/images` | every photo of a tour for the panel gallery (`mcp_tools/tour_gallery.py`, cached 6 h) |
| POST | `/chat/stream` | SSE turn: `{session_id, message, mode: "ask"|"plan", regenerate}` |

**SSE events** (`_turn_events`): `start`; `token {text}`; `tool {id,name,input,status:"running"}` then `{id,name,output:{count|error},status:"done"}`; `error {message}`; `options {kind,items}` (kind is plural, or `suggestions` / `itinerary_pdf` / `itinerary`: the planned trip, sent first); `done {message_id,content}`.

**Turn mechanics**

- Turns run detached from the HTTP connection: `_RUNNING` maps session id → asyncio Task; `_stream_turn` relays events from it. Closing the tab or switching chats does not stop the turn; the UI polls `/status`. A second stream for a busy session returns 409. Cancel saves nothing. Single-process only.
- `mode=plan` prepends `_MODE_CONTEXT["plan"]`. `regenerate` deletes the last reply and prepends `_REGENERATE_CONTEXT`. Remembered facts are prepended on the first user message only; `_extract_facts` learns facts from customer messages.
- Follow-up chips: `_FOLLOW_UPS` per kind, saved with the message as an options group `kind="suggestions"`.
- Agent is built lazily with `surface="streamlit"` and the Postgres checkpointer.
- Cookies: `samesite=none; secure` when `COOKIE_SECURE` is set, or unset and `CORS_ORIGINS` contains https; otherwise `lax`. `CORS_ORIGINS` must list exact origins (e.g. `http://localhost:3000`), never `*`.
- Boot logs (does not raise) a missing `SESSION_SECRET` or a failed migration, so `/health` stays green: check logs or `/health/db`.

**Migrations** (`migrations/`, idempotent SQL, no version table): `001_init` (users, chat_sessions, chat_messages, user_memory), `002_message_options`, `003_accounts` (names, session_epoch), `004_turns`, `005_artifacts_and_trip_state` (column `trip_window`; `window` is reserved), `006_message_feedback`, `007_user_saved_pins_settings` (`chat_sessions.pinned`, `saved_cards`, `users.ui_settings`), `008_itinerary_pdfs` (PDF bytes per chat). Add new ones as `009_*.sql`, written to be re-runnable.

**Data ownership (no leaks).** Every query that touches account data puts `user_id` in its WHERE clause (or joins through `chat_sessions.user_id`), so a missing check returns nothing rather than someone else's row; by-id endpoints return 404 for another account's ids. Where data lives:

| Data | Store |
|---|---|
| Account, names | `users` |
| Chats, pinned flag | `chat_sessions` |
| Messages, the cards (full option items) behind each reply, feedback, tool calls | `chat_messages` |
| Travel preferences and facts the agent remembers | `user_memory.facts` |
| Saved (hearted) cards | `saved_cards` |
| Theme, sidebar collapsed | `users.ui_settings` |
| Agent working state | LangGraph checkpoint tables, keyed by the session's unique `thread_id` |
| Itinerary PDFs | files under `ITINERARY_DIR` plus a copy in `itinerary_pdfs` (saved when the turn that built them is saved; the API disk is wiped on every Railway deploy), served at `/itinerary/{id}.pdf` by unguessable id (public by design), disk first then the database |

The browser keeps no account data. `localStorage` holds only the theme as a first-paint cache (`theme-pref`) and the last open chat id (used only if it is in the signed-in account's own list). Never add account data to `localStorage`; add a column or table and an owner-scoped endpoint. In-process state that tools keep between calls must be keyed by conversation (`rules.current_thread()`), never module-global: `agent_tools._LAST_PLANS` is the example.

`artifacts`, `trip_state` and `turns` (migrations 004, 005) are created but not yet used by any code.

---

## 8. Pricing and currency (read before touching any price)

- Supplier prices are AED-based. `fx.convert_supplier_price`: same currency as the account → `price / buyingROE`, otherwise `price * sellingROE`. Cache the ROE pair (`fx._roe_pair`); never hardcode rates. INR rounds to 0 places unless `places` is passed (tours use 2).
- Website and chatbot prices are identical since 2026-10-15 (the client removed the 5% flight markup). Price differences versus the website come from the supplier `rateCategory`, not ROE.
- Visa: `convert_supplier_price(price)` = price / buyingROE, service fee included, kept to 2 places (the website shows ₹7,753.36); Indian nationality is supplier id 245.
- **Field names by kind** (the UI reads these):
  - Flights: `price_inr` (whole party), `price_per_adult_inr`, `base_fare_inr`, `total_tax_inr`.
  - Hotels: `price_inr` (all rooms, whole stay), `per_night_inr`, `property_type`; rooms carry `price_inr`, `per_night_inr`, `price_display`.
  - Tours: `price_per_adult_inr`; `party_total_display` per option and `party_totals_markdown` for the reply; variants have `prices` / `transfer_prices` with `total_inr`, `total_display`, `per_person_display`, `priced_for_pax`.
  - Transfers: `price_inr` (vehicle total), `per_person_inr` for shared.
  - Visa: `price_per_person_inr`, `child_price_inr`.
  - Restaurants: `price_per_adult_inr`; dishes `adult_price_inr`, `child_price_inr`.
  - Packages: `price_inr` (party total), `price_per_person_inr`, `pricing_available`, `package_lines` (hotel + each tour/transfer with `price_inr`), `unpriced_parts`.
- **Package price = the website's:** the supplier's packagerate answers empty lists and packagestaticdata has only day titles, so `mcp_tools/package_pricing.py` reads the parts from the package's gujjutours.com page (`itineraryServices`, `packageHotels`; URL `/en/holidays/{country}-{id}/{city}-{id}/{name}-{id}`) and prices them as the website's browser does: first available hotel in priority order via B2C `packagehotelv1` (whole stay, AED) + each service's optionRate `rate` asked for ONE adult (or one unit for transfer type 4; adult + unit when the option's `restrictedRateType` lists "Unit"; private with a vehicle id when the band has one; supplier id from the option list), optional parts included, an unpriceable part left out and named; total converted once (AED → INR × sellingROE), per person from the unrounded total. Verified to the paisa on 10 Dubai packages on 2026-10-16. The website prices tours for one adult even for a bigger party; that is reproduced and reported to the client.
- **optionRate needs `isCache: false`:** without it the supplier answers from a rate cache (Desert Safari 30647 private: AED 474.10 vs the live 914.10 the website shows).
- **Per-vehicle tours:** a list row with `tourRateTypeId` "11" is sold per vehicle (airport pickup / drop-off): `sold_per_vehicle`, party total is one vehicle, options with no transfer types price one unit on type 4.
- **Display rule:** cards show the per-person (or per-night / per-vehicle) price; the reply text states the total for the customer's party, quoted from the result.
- **Data-quality filters live in parsers:** ~25% of flights are wrong-destination and Air India INR fares below 12,000 are test data (both dropped); hotels have no `StarRating` (defaults to 0); 26 of 197 tours have no matching rate (dropped); packages with `totalPrice=0` are on request and sort after priced ones.
- Prices are live: re-running a search minutes later can move a fare or change the vehicle list. Compare like for like when verifying.

---

## 9. Web app (`web/`): stack and structure

Next.js 15.5, React 19.1, Tailwind v4 (`@tailwindcss/postcss`, no `tailwind.config`), lucide-react 0.460, react-markdown 10 + remark-gfm, Vitest 4, TypeScript 5.9. No state library, no DOM test environment.

- `next.config.ts`: `reactStrictMode`; `images.unoptimized` (plain `<img>`, unknown supplier hosts). Rewrites (afterFiles) proxy `/auth/*`, `/chat/sessions/*`, `/chat/messages/*`, `/health/*`, `/me/*`, `/tours/*`, `/itinerary/*` to `API_PROXY_TARGET` (default `http://127.0.0.1:8000`) when `NEXT_PUBLIC_API_URL` is empty (same-origin mode).
- `src/app/chat/stream/route.ts`: POST passthrough for SSE (rewrites buffer SSE; this route streams immediately). Node runtime, `force-dynamic`, forwards cookies, `duplex: "half"`, `X-Accel-Buffering: no`.
- Env: `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_MOCK_MODE` (`"true"` → in-browser fake backend `src/lib/mock.ts`; password `wrong` shows the auth error), `NEXT_PUBLIC_AUTH_VIDEO_URL` (phone auth video), `API_PROXY_TARGET` (server side).
- `public/brand/gujju-logo.svg` (client logo, self-hosted), `public/auth/poster.webp` (video poster), `src/app/icon.svg` (favicon).

### Routes (`src/app`)

- `layout.tsx`: fonts via `next/font/google` (Plus Jakarta Sans 400-700 `--font-sans-face`; Bricolage Grotesque 500-800 `--font-display-face`; both have the ₹ glyph), viewport (`maximumScale 1`, `viewportFit: cover`), `<html data-theme="dark">` plus the inline `THEME_BOOT_SCRIPT`.
- `page.tsx` → redirect to `/chat`.
- `login/page.tsx`, `signup/page.tsx` (first + last name, email, password ≥ 8, confirm; validates on blur/submit; 409 = email taken). Both skip to `/chat` when already signed in.
- `chat/page.tsx`: the whole app shell (sidebar, drawer, home hero, messages, composer, dialogs, detail panel).

### Components (`src/components`)

- `Sidebar.tsx`: 280 px, collapses to a 64 px rail; same component in the mobile drawer. Logo centred, services list, Search (Ctrl/Cmd+K), Saved cards, chats grouped Pinned/Today/Yesterday/Previous 7 days/Older with pin/rename/delete, Settings, Help, user + logout. Theme is not here; it lives in Settings.
- `Composer.tsx`: `hero` and `dock` variants; auto-grow; Enter sends, Shift+Enter newline; Ask/Plan radio (Ask = quick answers, Plan = full priced trip); service chip; send/stop button.
- `HomeHero.tsx`: empty-chat screen on the gradient with greeting, composer and quick-start chips.
- `MessageList.tsx`: bubbles; card groups; suggestion chips; `AssistantActions` (copy, regenerate, thumbs up/down saved to the server); edit last message; retry. Builds `optionFor` (see §10).
- `SegmentList.tsx`: streaming text plus ONE status line: the running step (`toolLabel`) or "Thinking", never both, no counts, gone once text arrives.
- `Markdown.tsx`: react-markdown over `plainSymbols(text)`; result tables → cards; prose tables → `.md-table-wrap` with `LabelledRow` adding `data-label` / `data-kind` per cell.
- `ResultTable.tsx`: `ResultComparison` and `OptionLookup = (name, hint?: {price?, nth?})`.
- `cards/Cards.tsx`: `RestaurantCard`, `TourCard`, `HotelCard`, `PackageCard`, `TransferCard`, `VisaCard`, `FlightRow`; `CARD_FOR`, `KIND_ICON`. `cards/parts.tsx`: `CardShell`, `HeartButton`, `RatingPill`, `PriceTag` (value, label, unit e.g. "/night", note e.g. stay total), `TagChips`, `BookButton`, `MetaLine`. `cards/CardCarousel.tsx`: carousel / list layouts, preloads first images, `CardsLoading` skeletons.
- `Carousel.tsx`: snap strip with arrows (from `sm`), hidden scrollbar.
- `detail/DetailPanel.tsx`: right slide-over (full width on phones, 480 px from `sm`), gallery with arrows, facts, bill-style price breakdown, "Ask the agent" + Book.
- `SettingsDialog.tsx`: tabs Profile, Travel (preferences), Appearance (dark/light/system), Chats (export .txt, clear all); bottom sheet on phones.
- `NameDialog.tsx` (one-time name prompt for older accounts), `SearchDialog.tsx`, `Select.tsx` (custom listbox; never use a native `<select>`), `TravelLoader.tsx`, `SafeImage.tsx` (fallback placeholder; rewrites tenant media to the CloudFront CDN), `BrandLogo.tsx`, `ItineraryPdf.tsx` (branded download card), `ItineraryView.tsx` (the Plan-mode day-by-day card with prices per step and the cost lines; logic in `lib/itinerary.ts`; the other cards fold under it).
- `auth/AuthShell.tsx` (sign-in/up frame), `auth/Field.tsx` (`Field`, `SubmitButton`, `FormError`, `AuthChecking`).

### Library (`src/lib`)

- `api.ts`: the only network boundary (`credentials: "include"`, `ApiError`, `isUnauthorized`, mock branch on every call). `sse.ts`: POST SSE parser.
- `types.ts`, `contract.ts` (TS mirror, used only by its test so far).
- `cards.ts` (`kindOf`, safe readers `str`/`num`/`strList`/`objList`/`obj`, `formatInr`, `imagesOf`, `titleOf`, `idOf`, `KIND_LABEL`), `cardRefs.ts` (`toCardRefs`, `KIND_TAG="__kind"`, drops identical flights), `cardActions.tsx` (context: open, isSaved, toggleSave, book), `optionMatch.ts` (name matching), `resultTable.ts` (hast table parser).
- `usePersisted.ts`: now only `takeLocalAccountData(userId)`, a one-time migration that uploads this account's saved cards and pins left in localStorage by older builds and deletes every account-data key. Account data lives on the server (§7).
- `segments.ts` + `useStreamSegments.ts` (streaming reducer, 1.2 s idle "Thinking"), `services.ts` (7 services, quick starts, `displayFirstName`), `sessionGroups.ts`, `plainSymbols.ts`, `theme.ts` + `themeBoot.ts`, `mock.ts`.

---

## 10. UI behaviour and design system

### A chat turn in the UI

1. Boot: `api.me()` (401 → `/login`), `listSessions`, restore the active session from localStorage. A travel loader shows meanwhile.
2. Open a chat: `listMessages`, then `/status`; if the server is still working, poll every 2.5 s with a "Still working" bubble.
3. Send: optimistic user bubble + pending draft; `api.stream` events feed `useStreamSegments`; `options` events attach card groups to the draft; `done` settles and adopts the server `message_id` and content.
4. `stop()` cancels on the server; `detach()` (switching chats, new chat) only stops listening and the server finishes the turn.
5. Cards: groups render as carousels (flights as a list). A markdown table whose rows name results becomes those cards instead of a table. `optionFor` matches row names to option items: exact normalised name, then a unique prefix, then a unique shortened name; when several items share a name the row's own price (±1 against `price_inr`, `price_per_adult_inr`, `per_night_inr`, `price_per_person_inr`, `total_inr`) and then its position pick the item. Groups already shown by a table are not drawn again below it. A table without a price column (packages) still becomes cards when at least half its rows match results.
6. Actions: regenerate (re-sends with `regenerate: true`), thumbs (optimistic, server-saved), edit (prefills composer), heart (saved cards stored on the account via `/me/saved-cards`, ≤ 50, optimistic with rollback), Book (sends a booking message), "Ask the agent" (prefills "About the {kind} {title}: "). Opening a card sets `?view={kind}&id={cardId}`; Back closes it.

### Look and feel

- **Tokens** (`src/app/globals.css`): `@theme` maps `--color-*` utilities to `--c-*` variables defined for `[data-theme=dark]` (default) and `[data-theme=light]`. Never nest `@theme` in a selector. Use tokens (`bg-bg`, `bg-surface`, `text-fg`, `text-fg-muted`, `border-border`) rather than raw colours.
- **Brand:** blue `#2b7fc8` (dark `#1f5f9e`), red `#e8503a`, black `#0b0b0f`, rating `#ffc107`, success `#1e9e5a`. The hero gradient runs black → blue → violet → magenta → red → sunset orange (`.hero-glow`; `.hero-glow-fixed` keeps it dark in the light theme).
- **Type:** Plus Jakarta Sans for text, Bricolage Grotesque for h1-h4 (`font-display`), tracking −0.02em on headings.
- **Notable classes:** `.chat-canvas`, `.md` (prose capped ~70ch), `.md-table-wrap` (prose tables; stacked labelled cards below 640 px), `.md-result-table`, `.travel-loader-icon`, `.select-pop`, `.press` (tactile scale), `.no-scrollbar`, `.skeleton`, `.card-in`, `.panel-in`, `.panel-pop`, `.status-in`, `.hero-rise`, `.hero-bloom`, `.word-rise`, `.glow-out`/`.chat-enter`/`.dock-enter` (home → chat handover).
- **Loader:** `TravelLoader` (plane, hotel, ticket, car glide in from left, bottom, right, top; blue, violet, magenta, orange; no frame; pure CSS; static plane under reduced motion). Use it for any page-level or status wait; keep the small spinner only inside buttons.
- **Auth screens:** desktop = black form side + gradient card "Plan a trip that lasts FOREVER" (no video on desktop; the client asked for that). Phones = client reel (gumlet mp4, cropped to its right side because its tagline is burned into the left half) fading behind the heading; the video element is only mounted on phones. The page never scrolls; only the form column does, with a hidden bar.
- **Responsive rules:** `lg` (1024 px) switches desktop sidebar ↔ drawer + slim mobile top bar (menu, logo, new chat). `sm` (640 px) switches result table ↔ cards, detail panel width, settings sheet ↔ dialog. `[@media(hover:none)]` reveals hover-only controls on touch. Safe-area insets pad the main column, mobile bar, composer, detail footer and settings. The main column keeps `min-w-0` so wide content cannot widen the layout. Verified at 360, 390, 768, 1024, 1280, 1440, 1920 px and landscape phones.
- **Motion:** one orchestrated entrance per screen; everything honours `prefers-reduced-motion` (global rule in `globals.css`).
- **Copy rules:** sentence case, plain verbs, no em dashes in UI copy, no emoji icons (lucide only), prices in en-IN format via `formatInr`.
- **Removed on purpose (do not re-add):** top bar on desktop, + and mic buttons in the composer, "Enter to send" hint, jump-to-latest button, theme toggle in the sidebar, result counts in the status line.

---

## 11. Testing

- `tests/conftest.py` puts the root on `sys.path` (run from root). Autouse fixtures: `_hermetic_fx` (sets `FX_DISABLE_LIVE_ROE=1`, clears the FX cache), `_clear_settings_caches`, `_clear_conversation_state`. Also `sample_currency_rates`, `fake_today` (2026-05-25); builders in `tests/factories.py`.
- `FX_DISABLE_LIVE_ROE` gates `fx.live_rate_to_inr` / `live_rate_map` only. `fx._roe_pair` / `convert_supplier_price` (called from `parsers.py` and `tour_pricing.py`) still try the network unless a test mocks them; this is why the full suite is slow.
- HTTP mocking: `test_integration.py` uses `responses`; other tests monkeypatch.
- Key files: `test_contracts.py`, `test_ui_payload.py`, `test_chat_modes.py`, `test_tool_name_repair.py`, `test_malformed_tool_call_recovery.py`, `test_thread_repair.py`, `test_tour_fixes.py`, `test_restaurant_fixes.py`, `test_flight_fixes.py`, `test_fx.py`, `test_currency_and_detail.py`, `test_option_price_basis.py`, `test_injection_guard.py`, `test_prompt_tool_names.py`, `test_prompt_tool_args.py`, `test_voice*.py`.
- Web: Vitest under `web/src/lib/*.test.ts` (segments, resultTable, cards, cardRefs, optionMatch, plainSymbols, sessionGroups, contract). Pure functions only; put testable logic in `src/lib`.
- **Before calling a UI change done:** `npm run typecheck`, `npm test`, then check it in a browser at a phone width and a desktop width (Playwright screenshots are fine). For backend changes run the relevant pytest files.

---

## 12. Environment variables (names only; values live in `.env` / Railway)

- **Required to boot:** an LLM key for `LLM_PROVIDER` (`OPENROUTER_API_KEY` for the default `openrouter`, or `ANTHROPIC_API_KEY`). Real searches need `BOOKING_TOKEN`. Chat API also needs `DATABASE_URL` (or `DATABASE_PUBLIC_URL`; `sslmode=require` is added for non-local hosts) and `SESSION_SECRET` (generate per environment).
- **LLM:** `LLM_PROVIDER`, `ANTHROPIC_MODEL`, `OPENROUTER_MODEL`, `OPENROUTER_BASE_URL`, `OPENROUTER_PROVIDERS` (default `Google,DeepInfra,Cloudflare,Novita`, sent as provider order with fallbacks; dropping it has caused blank replies), `SELFHOSTED_*`, `SYSTEM_PROMPT_VERSION`.
- **Booking:** `BOOKING_BASE_URL`, `BOOKING_B2C_BASE_URL` (must be `https://www.gujjutours.com`; `stagingb2c` 404s), `BOOKING_TOKEN` (must carry all service types), `BOOKING_TRANSFER_TOKEN` (transfer inventory; the main token returns empty there), `BOOKING_HOTEL_STATIC_TOKEN` (optional hotels-only account), `BOOKING_FLIGHT_TARGET`, `BOOKING_TENANT_ID`, `FLIGHT_LIST_TENANT_ID`, `FLIGHT_SEARCH_TENANT_ID`, `FLIGHT_LIST_CUSTOM_HOST`, `BOOKING_ROE_VERIFICATION_TOKEN`. The supplier JWT expires 2027-05-25.
- **FX / pricing:** `AED_TO_INR`, `USD_TO_INR`, `EUR_TO_INR`, `GBP_TO_INR`, `SGD_TO_INR` (fallbacks only), `ROE_BASE_CURRENCY`, `FX_DISABLE_LIVE_ROE`, `PAYMENT_BUFFER_DAYS`, `DEPOSIT_PCT_*`, `TCS_*`, `EMI_TENURES`.
- **HTTP / misc:** `HTTP_TIMEOUT_SECS`, `HTTP_MAX_RETRIES`, `PLACE_RADIUS_KM`, `RESTAURANT_DETAIL_WORKERS`, `RESTAURANT_DETAIL_FANOUT`, `WHATSAPP_DB_PATH`, `ITINERARY_DIR`, `PUBLIC_BASE_URL`, `NO_COLOR`.
- **Chat API:** `CORS_ORIGINS`, `COOKIE_SECURE`.
- **WhatsApp:** `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`.
- **Voice:** `VAPI_*`, `VOICE_CALL_PROVIDER`, `VOICE_SERVICE_URL`, `LIVEKIT_*`, `TWILIO_TERMINATION_*`, `SARVAM_API_KEY` (or `SARWAM_AI_API_KEY`), `SARVAM_STT_MODEL`, `SARVAM_SPEAKER`, `SARVAM_TTS_SAMPLE_RATE`.
- **Web:** `API_PROXY_TARGET`, `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_MOCK_MODE`, `NEXT_PUBLIC_AUTH_VIDEO_URL`.

Never print, log, commit or paste secret values (`.env`, `railway-env*.txt`). Refer to them by name.

---

## 13. Branches and workflow

| Branch | Role |
|---|---|
| `main` | v2 baseline. Not the deploy source for the chat stack. |
| `fix/*`, `feat/*` | One change per branch, cut from the current release line, tested locally. |
| `release/2026.10` | Current release line. Finished fix/feat branches merge here with `--no-ff` (`merge: <branch> into release/2026.10`) after tests pass. Plan: `docs/RELEASE-2026.10-PLAN.md`. `release/2026.09` is the previous line. |
| `chat/nextjs/development` | **Deployment branch** for the Next.js web service AND the chat API service on Railway. A push deploys both immediately. |
| `chat/development` | Deployment branch for the Streamlit service. |
| `voice/development`, `whatsapp/development` | Deployment branches for voice and WhatsApp. |
| `*/saftey/development` | Safety copies of the deployment branches. |

Rules for agents:

1. Work on a `fix/*` or `feat/*` branch. Commit and push to it freely.
2. Merge into `release/2026.10` when the change is tested.
3. **Never merge or push to a deployment branch (`chat/nextjs/development`, `chat/development`, `voice/development`, `whatsapp/development`) unless the user explicitly says so in the current conversation.** A push there goes live. When the user does ask, remind them to check Railway env vars first (e.g. the current `BOOKING_TOKEN` on chat-api).
4. Never force-push shared branches; never rewrite release history.
5. Commit messages: conventional prefixes (`fix(web):`, `feat(tours):`, `merge:`), a short body explaining why.
6. **Docs land with the code, never after.** Before any commit, update every doc the change touches: this file (facts, tool params, gotchas), the client release notes (`docs/Release-Notes-2026.10.docx`) and the module's client verification report (`docs/*-Verification-Report.docx`) when client-visible behaviour changed. A commit whose docs are stale is not ready.

---

## 14. Deployment (Railway, Nixpacks; no Dockerfile or Procfile)

- Root `nixpacks.toml`: python312 + gcc, `/opt/venv`, `pip install -r requirements.txt`.
- `railway.toml` (chat API): `PYTHONPATH=/app uvicorn api.main:app --host 0.0.0.0 --port $PORT`, healthcheck `/health`, timeout 120 s, restart on failure ×3. Service watches `chat/nextjs/development`, root `/`.
- `web/railway.toml` + `web/nixpacks.toml` (Node 22, `npm ci`, build, `npm run start`): web service, same branch, **root `/web`**. Both files are needed so Railway does not pick up the root Python config.
- `railway.streamlit.toml` (`streamlit run surfaces/streamlit_app.py`, health `/_stcore/health`), `railway.voice.toml` (`uvicorn voice_service:app`), `railway.whatsapp.toml` (`uvicorn surfaces.whatsapp_app:app`), `railway.livekit.toml` (`pip install -r requirements.livekit.txt && python voice_livekit.py start`, 1 replica).
- Postgres is a Railway database; migrations run on chat-API boot (logged, not raised).
- Web service env: `API_PROXY_TARGET` (internal URL of chat-api) or `NEXT_PUBLIC_API_URL` (cross-origin; then set `CORS_ORIGINS` and `COOKIE_SECURE`).
- Full procedure, rollback and service map: `docs/DEPLOY-railway.md`.

---

## 15. Deeper docs

- `docs/architecture.md`: layering, adding tools and surfaces.
- `docs/agent_flow.md`: agent loop, tool inventory, API state.
- `docs/API-AND-TOOLS-REFERENCE.md`: the 27 verified supplier endpoints.
- `docs/LOCAL-TESTING.md`: local chat stack setup and checks.
- `docs/DEPLOY-railway.md`: Railway services, branch mapping, rollback.
- `docs/RELEASE-2026.10-PLAN.md`: degradation ladder and behaviour rules.
- `docs/PLAN-nextjs-ui-and-db.md`, `docs/adr-001-time-aware-itinerary.md`, `docs/FIXES-v3.md`, `docs/ISSUES-and-CLIENT-RESPONSE.md`, `docs/voice_*`, `docs/call_steps.md`.
- Client verification reports (`docs/*-Verification-Report.docx`, `docs/*-qa/`), including `docs/Website-Comparison-Fix-Verification-Report.docx` (cheaper hotels, apartments, refundable hotels, adventure tours). New client reports are DOCX only.
- Client release notes: `docs/Release-Notes-2026.10.docx` (every module in the release; update it with each client-visible change).

---

## 16. Known gaps and gotchas

- Only hotel IDs 206 and 509 are confirmed real; search `CLIENT_PLACEHOLDER` for values awaiting the client.
- Transfers in reference data use placeholder Place IDs; live search works by hotel name.
- Tour timeslots exist in the supplier API (Burj Khalifa has 33) but no agent tool exposes them to the customer yet.
- Hotel descriptions need one call per hotel; only 8 of 21 Dubai hotels have CDN photos (`parsers.hotel_image_urls`).
- Three B2C tour endpoints are dead upstream; see `docs/API-AND-TOOLS-REFERENCE.md`.
- `/chat/stream` detached turns live in one process; scaling the API to several replicas needs a shared store first.
- Restaurant data has no availability; only ~9 Dubai restaurants, all vegetarian.
- Flight results can contain the same flight from two suppliers; the UI shows one card per airline + flight numbers + times + fare.
- Tour photos: `GET /tours/{id}/images` → B2C `/api/tours/medias` (search gives one photo). Global Village came back from autocomplete as an unsellable Klook listing; `get_tour_options` swaps an all-unpriced tour for the website's priced listing of the same name.
- `plan_itinerary_tool`: no visa when the customer said so (`rules.customer_excluded`); flight times are dropped unless a flight is in the package or the customer stated them; the plan is cached for the PDF AFTER pricing, and the PDF takes its prices and total from that plan. The last day of a trip is a travel day (never an "empty day"); activities without slots start at their time of day (dinner/night 19:30, Global Village 16:00, safari/sunset 15:00).
- Plan mode full days (`scheduling.py`, `mcp_tools/plan_pricing.py`): arrival = airport pickup, hotel, 120 min rest, then up to 2 activities; full days aim at 540 min of activities with breakfast at the hotel, lunch and dinner placed in the gaps; departure = check-out, at most one activity, drop-off leaving 4 h before the flight. Dinner is "included" when a tour serves it (`serves_dinner`), else a restaurant within 6 km of the last place that is open then (`meal_venue`, places from `reference_data/dubai_attractions.json`), else the hotel. Airport legs are priced from the per-vehicle airport tours by the hotel's zone (smallest vehicle that seats the party). Tours are priced once per option chosen (`tour_price`, cached); the catalogue search runs with `search_tours(enrich=False)` to skip the timeslot fan-out. `usable_slots` reads both `start` and `time` keys.
- `agent._customer_words` reads only the customer's part of each message: the Plan mode [system] block says "no flights, visa or meals they did not ask for", and read as customer words it removed the visa from every plan.
- Re-planning (Plan mode changes): `plan_itinerary_tool` takes a tour's start times from the supplier (`_tour_slots`), never from the caller (the model sent each tour's last planned time as its only slot and pinned Dubai Frame into a full day); a tour whose own date is full moves to another day instead of being dropped; the day fill offers the last plan's tours first (stable days) and skips one this turn's message names and the caller left out (`rules.customer_named_now`, from `set_conversation_text(..., latest=)`); fillers need a published duration. A visa price must be one `get_visa_info` quoted in the chat (`quote_registry.was_quoted`), else the live fare is looked up (the model sent Rs 3,500; the fare is Rs 7,752.29). The visa line is named by `quote_registry.label_for`. A hotel's standard check-in later than the arrival does not push the rest. Tours the plan could not place come back in `excluded` and show on the card as notes. `_ensure_visa_party_total` stays out of turns that built a plan.
- Plan cache across restarts: `_LAST_PLANS` is in memory, so the chat API seeds it from the latest saved itinerary card (`api.main._seed_plan_from_history` → `agent_tools.seed_plan`) before each turn. When the PDF is for the same trip as the plan, the plan's days and lines are used, not the model's.
- `agent_tools` has a module `logger` (the `except` branches that logged used to raise NameError).
- Web: `formatInr` shows paisa as two digits (₹5,606.20, never ₹5,606.2); whole amounts stay without decimals. Hotel cards show the property type next to the stars.
- "Build a package" form (`web/src/components/PackageBuilder.tsx`, logic in `lib/packageBuilder.ts`) on the Packages home screen sends one plain request in Plan mode; Plan mode plans only the parts listed.
- Cabin is normalised (`normalize_cabin`): the supplier returns zero fares, silently, for any cabin that is not a bare code.
- City names tolerate typos ("Ahemdabad"): `resolve_city` falls back to a close match for names of 6+ letters, and `rules.customer_said` accepts the corrected spelling. Without both, a misspelt origin could never be searched.
- A reply with no tool call this turn may only quote ₹ amounts that some tool (or the customer) produced in the thread; a repeated request answered from memory ("I have already pulled those") is re-searched (`agent._research_unsourced`).
- Tour search gives one photo per tour and no supplier API we hold returns the gallery; `booking_api.endpoints.call_tour_page` reads `tourImages` from the website's tour page (resolves by id, any slug) when a card is opened.
- Flight options carry `airline_logos` (CDN `/uploads` + the segment's `airlinelogoPath`, UI-only). While streaming, a half-written markdown table is held back and shown as the loader (`web/src/lib/pendingTable.ts`).
- The website adds a hotel markup that varies by hotel (4.0% to 11.4% seen; 8-27%, median ~11.7%, across all of Dubai on 6-10 Nov 2026); the chatbot quotes the supplier rate until the client confirms the rule. The website's own search is public: `POST www.gujjutours.com/api/x-hotels/search-stream` (NDJSON, needs the city `locationId` from `/api/x-hotels/hotel-lookup`), price = `StartPrice` (AED) x sellingROE; its star buttons default to 3-5 and its Refundable filter judges the cheapest rate only. The website's visa cards (3-4 working days, Express 1 working day) disagree with its own visa article (2–4 days, 24–48 hours); the chatbot follows the article.
