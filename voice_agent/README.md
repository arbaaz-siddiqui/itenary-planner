# Nikki — Pipecat Voice Agent

Production-quality WebRTC voice agent for the Dubai Trip Planner.

**Stack:** Daily.co (transport) · Groq Whisper (STT) · Groq Llama 3.3 70B (LLM) · Cartesia Sonic-2 (TTS) · Pipecat (orchestration)

---

## Prerequisites

- Python 3.11+
- A Daily.co account (free tier works) — [daily.co](https://daily.co)
- A Groq API key — [console.groq.com](https://console.groq.com)
- A Cartesia API key — [cartesia.ai](https://cartesia.ai)

---

## Environment variables

Create a `.env` file in the project root (one level above `voice_agent/`):

```env
DAILY_API_KEY=your_daily_api_key_here
GROQ_API_KEY=your_groq_api_key_here
CARTESIA_API_KEY=your_cartesia_api_key_here

# Optional — override the default Indian female voice
CARTESIA_VOICE_ID=a0e99841-438c-4a64-b679-ae501e7d6091
```

---

## Install dependencies

```bash
# From the project root
pip install -r voice_agent/requirements.txt
```

The parent project dependencies (`booking_api`, `parsers`, etc.) must also be installed:

```bash
pip install -r requirements.txt   # root requirements.txt
```

---

## Run locally

### 1. Start the server

```bash
# From the project root
uvicorn voice_agent.server:app --host 0.0.0.0 --port 8080 --reload
```

### 2. Open the browser UI

Navigate to `voice_agent/index.html` — open it directly in a browser **or** serve it:

```bash
# Serve the HTML from the same port (add a static files mount in server.py) or just open the file directly
python -m http.server 3000 --directory voice_agent
```

Then open [http://localhost:3000](http://localhost:3000).

### 3. Click "Start Call with Nikki"

The browser will:
1. Call `POST /start-call` on the FastAPI server
2. Receive a Daily.co room URL + caller token
3. Join the room via the Daily.co JS SDK
4. The server simultaneously spawns `bot.py` as a child process that joins the same room

---

## Architecture

```
Browser (Daily JS SDK)
    │  WebRTC audio
    ▼
Daily.co room
    │  WebRTC audio
    ▼
bot.py (Pipecat pipeline)
    │
    ├── DailyTransport  ← WebRTC in/out
    ├── GroqSTTService  ← whisper-large-v3-turbo
    ├── GroqLLMService  ← llama-3.3-70b-versatile + tool calling
    │       └── on_tool_call_started → mcp_tools/_impl functions
    ├── CartesiaTTSService ← sonic-2
    └── DailyTransport  ← audio back to caller
```

### Tool calling

When the LLM decides to search for flights, hotels, tours, transfers, or visa info, it emits a tool call. The `on_tool_call_started` event handler in `bot.py`:

1. Parses the function name and arguments
2. Calls the real `_impl` function from `mcp_tools/` (runs in a thread pool to avoid blocking the async loop)
3. Appends the JSON result to the LLM context
4. The LLM continues the conversation with real data

### Error handling

- If a tool raises an exception the agent says *"Ek moment, system mein thodi dikkat aayi"* and continues
- If `mcp_tools/` can't be imported (e.g. running outside the parent project), stubs are used so the pipeline still boots

---

## Deploying to Railway / Render

The `server.py` reads `HOST` and `PORT` from env. Set:

```
HOST=0.0.0.0
PORT=8080
DAILY_API_KEY=...
GROQ_API_KEY=...
CARTESIA_API_KEY=...
PYTHONPATH=/app
```

The `PYTHONPATH=/app` is critical so `bot.py` child processes can import `mcp_tools`.

Start command:

```
uvicorn voice_agent.server:app --host 0.0.0.0 --port 8080
```

---

## File overview

| File | Purpose |
|------|---------|
| `bot.py` | Pipecat pipeline — STT → LLM → TTS, tool execution |
| `server.py` | FastAPI server — room creation, bot spawning |
| `index.html` | Browser UI — single-page call interface |
| `requirements.txt` | Python dependencies |
