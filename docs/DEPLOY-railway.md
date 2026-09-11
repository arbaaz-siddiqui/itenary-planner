# Railway deployment and rollback

Three services, deployed separately, plus the Postgres database.

| Service | Branch | Root | Start command |
|---|---|---|---|
| Chat (Streamlit) — **live, do not touch** | `chat/development` | `/` | (unchanged) |
| Chat API (new) | `chat/nextjs/development` | `/` | `uvicorn api.main:app` |
| Web UI (new) | `chat/nextjs/development` | `web/` | `npm start` |
| Postgres | — | — | Railway plugin |

`chat/nextjs/development` was branched from `chat/development` at the exact
commit now serving, then the new work was added on top. So the Streamlit branch
is untouched and keeps deploying as it always has, while the two new services
read the new branch.

Both new services watch the SAME branch, so a push rebuilds both. That is
intentional: the API and the UI it serves are versioned together.

The chat service is **already live and working**. Everything below is written so
that service can be put back exactly as it was, in one command, without touching
the new ones.

---

## 0. Before anything: record the rollback point

Do this FIRST, while the running deployment is still the good one. Without it
there is nothing to roll back to.

```bash
# The commit currently deployed on the chat service.
git rev-parse chat/development
# Write it down. Example: 9e5d75b...

# A tag is easier to type than a SHA under pressure.
git tag chat-last-known-good chat/development
git push origin chat-last-known-good
```

In the Railway dashboard, open the chat service → **Deployments**, and note the
ID of the deployment that is currently serving. Railway keeps previous builds,
so the fastest rollback is a redeploy of that entry — no rebuild, no git.

---

## 1. Open question before deploying (needs your answer)

`chat/development`'s `railway.toml` contains:

```toml
startCommand = "PYTHONPATH=/app uvicorn voice_service:app --host 0.0.0.0 --port $PORT"
```

That starts the **voice** service, not Streamlit — while `nixpacks.toml`'s own
comment says the chat branch should run `streamlit run surfaces/streamlit_app.py`.

So one of these is true:

1. Railway's dashboard overrides the start command for that service, and the
   file is stale but harmless; or
2. the chat service is running the wrong app.

Check the chat service's **Settings → Deploy → Start Command** in Railway before
deploying. If it is overridden there, leave the file alone — correcting it would
change what deploys. If it is NOT overridden, the file needs fixing first, and
that is a change to the live service, so it should ship on its own.

---

## 2. Postgres

Already created. It uses **private networking**, so `DATABASE_URL` resolves only
from inside Railway — which is why migrations run on API boot rather than from a
laptop.

Variables the Postgres plugin exposes:

- `DATABASE_URL` — `postgres.railway.internal`, private, use this
- `DATABASE_PUBLIC_URL` — public proxy, only for one-off admin access

`api/db.py` prefers `DATABASE_URL` and falls back to the public one.

Migrations are idempotent (`CREATE ... IF NOT EXISTS`, `ADD COLUMN IF NOT
EXISTS`) and run in filename order on every boot, so a redeploy re-applies them
safely and a rollback does not need them undone.

---

## 3. Chat API service (new)

New Railway service, same repo, branch `chat/nextjs/development`, root `/`.

The branch's own `railway.toml` already sets the start command and health
check, so nothing needs overriding in the dashboard.

**Variables:**

```
DATABASE_URL       = ${{Postgres.DATABASE_URL}}    # reference, do not paste
SESSION_SECRET     = <generate a NEW one, see below>
CORS_ORIGINS       = https://<your-web-domain>
BOOKING_TOKEN      = <same as the chat service>
OPENROUTER_API_KEY = <same as the chat service>
```

Generate the session secret ON THE MACHINE, never reuse the local one:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

A shared secret between local and production means a cookie minted on a laptop
is accepted by production. Use a different value in each environment.

**Start command:**

```
PYTHONPATH=/app uvicorn api.main:app --host 0.0.0.0 --port $PORT
```

**Health check path:** `/health`

`CORS_ORIGINS` must contain the web UI's real origin. `*` is rejected by
browsers when credentials are included, and the session cookie is credentialed.
The `https://` in it is also what flips the session cookie to `Secure` (see
`api/auth.py:_cookie_is_secure`), so it must not be left at the localhost
default in production.

---

## 4. Web UI service (new)

New Railway service, same repo, branch `chat/nextjs/development`, **root
directory `web/`**.

**Variables:**

```
API_PROXY_TARGET    = https://<your-api-domain>
NEXT_PUBLIC_API_URL =            # leave BLANK
```

Leaving `NEXT_PUBLIC_API_URL` blank keeps the browser talking to its own origin,
and `next.config.ts` rewrites `/auth/*`, `/chat/*`, `/tours/*` and `/health/*` to
the API. That keeps the session cookie same-site, which is the whole reason the
proxy exists.

`NEXT_PUBLIC_*` values are inlined at BUILD time, so changing one needs a
redeploy, not a restart.

**Build / start:** Nixpacks detects Next.js. If it needs to be explicit:

```
build: npm ci && npm run build
start: npm start
```

---

## 5. Deploy order

The API must be up before the web UI points at it, and the database before the
API boots (it runs migrations on startup).

```
Postgres (already running)
  └─> Chat API        — check /health returns 200
        └─> Web UI    — check login works end to end
```

The existing chat service is untouched by all three.

---

## 6. Deploying an update

Railway deploys on push to the branch a service watches.

```bash
# You do the commits and pushes.
git checkout chat/nextjs/development
git add -A
git commit -m "feat: <what changed>"
git push origin chat/nextjs/development
```

Both new services watch that branch, so both rebuild. `chat/development` is
never touched by this, so the Streamlit service keeps running whatever it is
running now.

After each deploy:

```bash
curl -s https://<api-domain>/health
curl -s https://<api-domain>/health/db      # proves Postgres is reachable
```

---

## 7. Rollback — chat service

The live chat service is the one that must be recoverable. Three options,
fastest first.

### 7a. Redeploy the previous build (fastest, no git)

Railway dashboard → chat service → **Deployments** → find the last known good
entry → **Redeploy**. No rebuild, no push, seconds to take effect. Use this
first.

### 7b. Revert the commit and push

Use when the bad change is already merged and you want the branch to reflect the
rollback.

```bash
git checkout chat/development
git pull origin chat/development

# Undo the last commit as a NEW commit. History is preserved, which matters
# because other branches share these commits.
git revert --no-edit HEAD
git push origin chat/development
```

To revert more than one commit:

```bash
git revert --no-edit <oldest-bad-sha>^..<newest-bad-sha>
git push origin chat/development
```

### 7c. Reset to the known-good tag (last resort)

This DISCARDS commits on the branch. Only do it when a revert is not workable
and you accept losing what came after the tag.

```bash
git checkout chat/development
git reset --hard chat-last-known-good
git push --force-with-lease origin chat/development
```

`--force-with-lease`, never plain `--force`: it refuses if someone else pushed
in the meantime, instead of silently destroying their work.

### What a rollback does NOT undo

- **Database migrations.** `002_message_options.sql` only ADDS a nullable column
  with a default, so older code ignores it and keeps working. Nothing to undo.
- **Rows already written.** Chats saved by the new UI stay in the database.
- **Environment variables.** Changing one does not redeploy; trigger a redeploy
  after editing.

---

## 8. Rolling back the new services

They are separate services, so removing them does not touch chat:

- Web UI misbehaving → Railway → web service → **Remove** (or pause). Chat and
  the API are unaffected.
- API misbehaving → same. The Streamlit chat service does not call it.

This separation is the point: the new UI cannot take the working chat down.

---

## 9. Verify after deploying

```bash
# API alive, database reachable
curl -s https://<api-domain>/health
curl -s https://<api-domain>/health/db

# Chat service still serving (unchanged by any of the above)
curl -s -o /dev/null -w "%{http_code}\n" https://<chat-domain>/
```

In the browser, on the new UI: sign up, send one message, confirm the reply
streams, then **reload the page** and confirm the option cards and their images
are still there. That last step exercises the `options` column added in
`002_message_options.sql` — before it, cards vanished on reload.
