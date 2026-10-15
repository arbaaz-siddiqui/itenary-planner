# CLAUDE.md

The full project guide for coding agents is in AGENTS.md: product, layout, agent loop, chat API, pricing rules, web UI and design system, testing, environment, branches and deployment. Read it before changing code.

@AGENTS.md

## Working rules (short form)

- Branches: work on `fix/*` or `feat/*`; merge tested work into `release/2026.10`. Never merge or push to a deployment branch (`chat/nextjs/development`, `chat/development`, `voice/development`, `whatsapp/development`) unless the user says so in the conversation: a push there goes live on Railway.
- Never print or commit secrets (`.env`, `railway-env*.txt`); refer to variables by name.
- Fix root causes; no hardcoded prices, rates or ids; keep changes backward compatible.
- Prices: quote supplier figures, never compute them in prompts or UI; cards show per-person or per-unit prices, replies give the party total from the result.
- UI copy: sentence case, no em dashes, lucide icons only, `formatInr` for money.
- Account data (saved cards, pins, settings, chats) lives in Postgres behind owner-scoped queries; never in localStorage. Tool state kept between calls is keyed by conversation (`rules.current_thread()`).
- Before calling work done: relevant pytest files; `cd web && npm run typecheck && npm test`; for UI, look at it at a phone width and a desktop width.
- Client verification reports are DOCX only.
