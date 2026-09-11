# Running the new chat stack locally

Three processes: Postgres, the API (port 8000), the web UI (port 3000). The
existing Streamlit app is untouched and can keep running alongside.

---

## Step 0 — Postgres

Every command here is PowerShell, written on **one line with no `\` line
continuations**. PowerShell treats a trailing backslash as the end of the
command, so a multi-line `docker run` fails with `docker: invalid reference
format` followed by `-e : The term '-e' is not recognized`.

Docker Desktop must be running first. If any docker command errors with
`open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file
specified`, the engine is not up:

```powershell
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
```

Wait 30-60 seconds, then confirm `docker ps` prints a table header (no rows is
fine). Then, all on ONE line:

```powershell
docker run -d --name gt_pg -e POSTGRES_PASSWORD=devpass -e POSTGRES_DB=railway -p 55432:5432 postgres:18-alpine
```

Port **55432** deliberately, not 5432 — if you ever install Postgres natively
the two will not fight over the port.

Check it answers before moving on:

```powershell
docker exec gt_pg pg_isready -U postgres
```

**No Docker?** Two alternatives:

- Install Postgres for Windows and use `postgresql://postgres:PASS@localhost:5432/railway`
- Temporarily enable Public Access on the Railway Postgres and use
  `DATABASE_PUBLIC_URL`. Works immediately, but the DB is then internet-facing
  and billed as egress, so turn it off afterwards.

---

## Step 1 — Environment

Add these three to `.env` (the other keys are already there):

```
DATABASE_URL=postgresql://postgres:devpass@localhost:55432/railway
SESSION_SECRET=EvD1-eUD8zGtekd3NH7Aq55tQIszxrRhpnuMYpDjocyMwI_yN7FGsV3tAZVNO5KR
CORS_ORIGINS=http://localhost:3000
```

That secret was generated for local use only. **Generate a different one for
Railway** — a shared key means a cookie forged against one environment is valid
in the other:

```powershell
python -c "import secrets;print(secrets.token_urlsafe(48))"
```

Also confirm this is already correct, because it is what broke transfer prices
in production last week:

```
BOOKING_B2C_BASE_URL=https://www.gujjutours.com
```

`stagingb2c` returns an HTML 404 on every `/api` path.

---

## Step 2 — Start the API

```powershell
venv\Scripts\python.exe -m uvicorn api.main:app --reload --port 8000
```

Migrations run automatically on boot. Expect `migrations_applied` in the log.

Verify the schema actually landed — do not just trust that the process started:

```powershell
curl http://localhost:8000/health
curl http://localhost:8000/health/db
```

`/health/db` lists the tables it can see. You want `chat_messages`,
`chat_sessions`, `user_memory`, `users`, plus LangGraph's own `checkpoints*`
tables.

A migration failure is **logged, not raised** — deliberately, so a bad
migration cannot fail a Railway healthcheck and roll back the deploy with no
readable reason. So if `/health/db` shows no tables, read the API log rather
than assuming boot success meant success.

---

## Step 3 — Start the web UI

In a second terminal:

```powershell
cd web
npm install          # first time only
```

Create `web/.env.local`:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_MOCK_MODE=false
```

Then:

```powershell
npm run dev
```

**`NEXT_PUBLIC_*` values are inlined at build time.** Changing them needs a
restart of `npm run dev` — editing the file alone does nothing, which is easy
to lose twenty minutes to.

Open http://localhost:3000

---

## Step 4 — Test in this order

Each step proves something the next one depends on, so a failure tells you
where the problem is rather than just "it does not work".

**1. Sign up.** Go to `/signup`, create an account. You should land in `/chat`.
Proves: Postgres write, password hashing, cookie set.

**2. Confirm the user reached the DB:**
```powershell
docker exec gt_pg psql -U postgres -d railway -c "SELECT email, created_at FROM users;"
```

**3. Auth actually blocks.** In a private window open `/chat` directly. You
should be bounced to login. Also:
```powershell
curl -i http://localhost:8000/chat/sessions
```
Expect **401**. If this returns 200, stop — the login gate is not working.

**4. Send a real message.** "desert safari tours in dubai on 20 october for 2
adults". Watch for, in order: "Thinking…", then a tool chip, then the reply
streaming in. Roughly 8-40 seconds — a tour search makes ~40 supplier calls,
which is why the progress indicators exist.

**5. Reload the page.** The conversation must still be there. This is the whole
point of the Postgres checkpointer; Streamlit loses it on refresh.

**6. Check persistence:**
```powershell
docker exec gt_pg psql -U postgres -d railway -c "SELECT role, left(content,50) FROM chat_messages ORDER BY created_at;"
```

**7. New chat, then switch back.** Both sessions in the sidebar, each with its
own history and no bleed between them.

**8. Memory across sessions.** Say "flights from Mumbai to Dubai" in one chat,
then start a **new** chat. Check it was learned:
```powershell
docker exec gt_pg psql -U postgres -d railway -c "SELECT facts FROM user_memory;"
```
Expect `{"origin_city": "Mumbai", ...}`. In the new chat the agent should not
re-ask your departure city.

**9. Transfer pricing.** Ask "desert safari for 10 people, what do sharing and
private cost". Expect per-person sharing vs per-vehicle private, with a total
each — not one flat figure for both.

**10. Debug panel.** Expand a tool chip. It should show the API called, the
input sent, and the output. Payloads reach 200KB, so it loads lazily — the JSON
appears only after you open it.

---

## Known-good reference numbers

Measured against the live supplier on 2026-09-11. If yours differ wildly,
suspect the token or the B2C host rather than the code:

| Check | Expected |
|---|---|
| Dhow Cruise Marina, Lower Deck, 12 Sep | ₹2,074 per adult (matches the website) |
| Desert Safari, 10 pax | sharing ~₹38,462 total vs private ~₹32,194 |
| Solo traveller, sharing | billed for 2 — supplier minimum, stated in the reply |
| Private tier, pax 6 → 7 | steps 440 → 880 AED (vehicle holds 6, not the 12 `maxPax` claims) |

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `DATABASE_URL is not set` | Missing from `.env`, or the API was started from the wrong directory |
| `could not translate host name` | You used Railway's `DATABASE_URL`. That host resolves only inside Railway — use the local one or `DATABASE_PUBLIC_URL` |
| Login works, `/chat` 401s | `CORS_ORIGINS` does not exactly match `http://localhost:3000`. Cross-origin cookies need a literal origin, never `*` |
| UI ignores `NEXT_PUBLIC_API_URL` | Build-time inlined; restart `npm run dev` |
| Blank assistant reply | Check `OPENROUTER_PROVIDERS` is pinned. Three upstream hosts return `content: null` while billing tokens |
| "rates not published for this date" | `BOOKING_B2C_BASE_URL` is pointing at `stagingb2c` |
| Turn hangs for minutes | Should be capped at 120s. If not, the LLM client timeout is not reaching the underlying client |

---

## Stopping

```powershell
docker stop gt_pg          # keeps the data
docker rm -f gt_pg         # deletes it
```

Ctrl-C the API and the web dev server.
