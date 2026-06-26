# Voice Agent — Local Test Runbook

How to run the trip-planner voice stack locally and test it yourself.

## The stack (3 things must be running)

```
Your test → VoiceCare API (NestJS) → Planner /voice bridge (Python) → LangGraph agent
                     │
                  Postgres (Docker)
```

1. **Postgres** — Docker container (the voice app's DB).
2. **Planner /voice bridge** — Python FastAPI, the conversational "brain".
3. **VoiceCare API** — NestJS, the telephony/voice shell + the `/webhook/trip-agent` hook.

> Real phone calls also need Vapi/ElevenLabs pointed at the webhook (a public URL via
> ngrok). That's the LAST step — for now you can test the whole brain over HTTP without
> any phone, which is what this runbook covers.

---

## Start everything (3 terminals)

### Terminal 1 — Postgres (Docker)
```bash
cd "c:/Users/MohdArbaazSiddiqui/Downloads/AI_itinerary_planner/voice_agent/voice-care"
docker compose up -d
# verify:
docker ps --filter name=voicecare-db
```
(One-time, if DB is empty: `cd voicecare-api && npx prisma migrate deploy && npx prisma db seed`)

### Terminal 2 — Planner /voice bridge (Python, port 8100)
```bash
cd "c:/Users/MohdArbaazSiddiqui/Downloads/AI_itinerary_planner"
./venv/Scripts/python.exe -m uvicorn surfaces.voice_app:app --host 127.0.0.1 --port 8100
# wait for: "Application startup complete"
# verify:  curl http://127.0.0.1:8100/health   -> {"status":"ok"}
```

### Terminal 3 — VoiceCare API (NestJS, port 3000)
```bash
cd "c:/Users/MohdArbaazSiddiqui/Downloads/AI_itinerary_planner/voice_agent/voice-care/voicecare-api"
npm run start
# wait for: "VoiceCare API running on http://localhost:3000"
# verify:  curl http://localhost:3000/health   -> {"status":"ok","db":"connected"}
```
> If port 3000 is taken: `PORT=3010 npm run start` and use 3010 in the tests below.

**The wiring** (already set in `voicecare-api/.env`):
`PLANNER_VOICE_URL="http://127.0.0.1:8100/voice"` — this is what makes VoiceCare call the planner.

---

## How to test (no phone needed)

The `/api/webhook/trip-agent` endpoint is exactly what the voice agent calls mid-call.
You send a transcript + a `call_id`; you get back the spoken reply. Same `call_id` =
same conversation (memory persists).

### Test 1 — greeting turn (fast, ~5–8s)
```bash
curl -s -X POST http://localhost:3000/api/webhook/trip-agent \
  -H 'Content-Type: application/json' \
  -d '{"transcript":"Hi, I want to plan a trip to Dubai","call_id":"my-test-1"}'
```
Expect: a short, single-question reply (e.g. "where will you be flying from?").

### Test 2 — continue the SAME call (memory)
```bash
curl -s -X POST http://localhost:3000/api/webhook/trip-agent \
  -H 'Content-Type: application/json' \
  -d '{"transcript":"From Mumbai, 2 adults, 4 nights in August","call_id":"my-test-1"}'
```
Expect: it remembers Dubai + builds on it. NOTE: turns that trigger live flight/hotel
searches can take 30–40s (the agent is calling the booking APIs). That's expected for now.

### Test 3 — a fresh conversation (different call_id)
```bash
curl -s -X POST http://localhost:3000/api/webhook/trip-agent \
  -H 'Content-Type: application/json' \
  -d '{"transcript":"What desert safari options are there?","call_id":"my-test-2"}'
```

### What a GOOD result looks like
- `reply` is short, spoken-style, ONE question at a time, no markdown/asterisks/links.
- It does NOT invent prices/hotels/flights — if it needs to search it may take longer.
- Same `call_id` continues context; new `call_id` starts fresh.

### Things to try to break it (good test cases)
- Vague: `"I want a holiday"` (should ask where/when).
- Numbers: `"6 people, 2 are kids"` (should resolve to 4 adults + 2 children).
- Specific: `"4 nights Dubai, Emirates, around 1.5 lakh budget, 2 adults"`.
- Off-topic: `"what's the weather"` (should stay a travel concierge).
- Silence: `{"transcript":"  ","call_id":"x"}` → "didn't catch that, say it again".

---

## Bonus: see the DB / API directly
```bash
# the seeded traveler context the agent can pull at call start:
curl http://localhost:3000/api/webhook/traveler-data/+918770311925
# -> {"traveler_name":"Jay Purohit", ...}

# list travelers / trips (need an auth token — skip unless testing the dashboard)
```

---

## Stop everything
- Terminals 2 & 3: Ctrl+C.
- Postgres: `docker compose down` (data persists in the volume).

---

## When you're ready for a REAL phone call (later)
1. Expose the VoiceCare API publicly: `ngrok http 3000`.
2. In Vapi (or ElevenLabs) assistant config, point the model/tool webhook at
   `https://<ngrok>/api/webhook/trip-agent`.
3. Set the assistant's **voice** to an Indian voice (ElevenLabs Indian voice id, or Sarvam).
4. Call your Twilio number.
Not needed for the HTTP testing above.
