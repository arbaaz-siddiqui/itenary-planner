"""voice_service — single-file voice agent backend (replaces the NestJS app).

Everything the voice agent needs, in Python:
  * VAPI config (read from env)
  * place_call() / schedule_call() — trigger an outbound Vapi call
  * the FastAPI app with the Vapi Custom-LLM webhook (`/api/webhook/chat/completions`)
    that streams the planner's reply back to Vapi (SSE)
  * per-call TRACE capture: every user/agent turn + every booking API call (with
    request data + output summary), exposed via `get_trace()` for the Streamlit
    Voice tab.

No database. No Node. Run it with:  uvicorn voice_service:app --port 8100
and point Vapi's assistant model.url at  https://<ngrok>/api/webhook
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

# Quiet dotenv's per-line parse warnings — the .env holds long JWT/comment lines
# it can't fully parse but the real KEY=VALUE pairs load fine; the noise just
# clutters the Streamlit/console output.
import logging as _logging

_logging.getLogger("dotenv.main").setLevel(_logging.ERROR)
load_dotenv()  # pull VAPI_* + LLM + booking creds from the planner .env

from agent import (
    build_react_agent,
    build_sqlite_checkpoint,
    configure_logging,
    extract_assistant_text,
    extract_tool_calls,
    get_logger,
    invoke_and_log,
)
from booking_api.http_client import http_requests_since, latest_http_seq

log = get_logger("voice")


# =============================================================================
# Config (env)
# =============================================================================
VAPI_API_KEY = os.getenv("VAPI_API_KEY", "")
VAPI_ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID", "")
VAPI_PHONE_NUMBER_ID = os.getenv("VAPI_PHONE_NUMBER_ID", "")


def _normalize_phone(num: str) -> str:
    num = (num or "").strip().replace(" ", "").replace("-", "")
    if not num:
        return ""
    if num.startswith("+"):
        return num
    digits = re.sub(r"\D", "", num)
    if len(digits) == 10:  # bare Indian mobile
        return f"+91{digits}"
    return f"+{digits}"


def place_call(number: str, *, schedule_unix: int | None = None) -> dict[str, Any]:
    """Trigger an outbound Vapi call. Returns the Vapi response (or an error dict)."""
    number = _normalize_phone(number)
    if not (VAPI_API_KEY and VAPI_ASSISTANT_ID and VAPI_PHONE_NUMBER_ID):
        return {"error": "VAPI env not configured (VAPI_API_KEY / ASSISTANT_ID / PHONE_NUMBER_ID)"}
    if not number:
        return {"error": "no phone number"}
    body: dict[str, Any] = {
        "assistantId": VAPI_ASSISTANT_ID,
        "phoneNumberId": VAPI_PHONE_NUMBER_ID,
        "customer": {"number": number},
    }
    if schedule_unix:
        from datetime import datetime, timezone

        body["schedulePlan"] = {
            "earliestAt": datetime.fromtimestamp(schedule_unix, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        }
    req = urllib.request.Request(
        "https://api.vapi.ai/call/phone",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {VAPI_API_KEY}",
            "Content-Type": "application/json",
            # Vapi sits behind Cloudflare, which 403s urllib's default
            # "Python-urllib/x" UA. A normal UA gets through (curl works for the
            # same reason — its UA isn't blocked).
            "User-Agent": "trip-planner-voice/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode())
            log.info("voice_call_placed", number=number, call_id=d.get("id"), status=d.get("status"))
            return {"call_id": d.get("id"), "status": d.get("status"), "number": number}
    except Exception as e:  # noqa: BLE001
        log.error("voice_call_failed", number=number, error=str(e))
        return {"error": str(e), "number": number}


# =============================================================================
# Per-call TRACE (for the Streamlit Voice tab)
# =============================================================================
# session_id -> list of turn dicts:
#   {"user": str, "agent": str, "latency_s": float, "api_calls": [...], "tools": [...]}
_TRACE_LOCK = threading.Lock()
_TRACES: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=50))


def _record_turn(session_id: str, turn: dict[str, Any]) -> None:
    with _TRACE_LOCK:
        _TRACES[session_id].append(turn)


def get_trace(session_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """All recorded voice turns. Without session_id, returns every session."""
    with _TRACE_LOCK:
        if session_id:
            return {session_id: list(_TRACES.get(session_id, []))}
        return {sid: list(turns) for sid, turns in _TRACES.items()}


def clear_trace() -> None:
    with _TRACE_LOCK:
        _TRACES.clear()


# =============================================================================
# Planner agent (the brain)
# =============================================================================
@lru_cache(maxsize=1)
def get_voice_agent() -> object:
    return build_react_agent(surface="voice", checkpoint_store=build_sqlite_checkpoint())


# If a thread gets corrupted (interrupted tool call), we bump this salt so the
# caller gets a clean thread on retry instead of being stuck failing forever.
_SESSION_SALT: dict[str, int] = {}


def thread_id_for_session(session_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", (session_id or "").replace("+", "")) or "anon"
    salt = _SESSION_SALT.get(session_id, 0)
    return f"voice_{cleaned}" + (f"_{salt}" if salt else "")


# Strip markup/URLs so nothing un-speakable reaches TTS.
_TABLE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_HEAD = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_EMPH = re.compile(r"[*_`#~]+")
_BULLET = re.compile(r"^\s*[-*•]\s+", re.MULTILINE)
_RUPEE = re.compile(r"₹\s*([\d,]+)")          # ₹1,00,000 → "1 lakh rupees" etc.


def _humanise_numbers(text: str) -> str:
    """Convert ₹1,24,500 → 'one lakh twenty four thousand five hundred rupees'.
    Keeps the conversation natural — TTS reads raw numbers awkwardly."""
    def _replace(m: re.Match) -> str:
        raw = m.group(1).replace(",", "")
        try:
            n = int(raw)
        except ValueError:
            return m.group(0)
        if n >= 10_00_000:
            cr = n / 10_00_000
            return f"{cr:g} crore rupees"
        if n >= 1_00_000:
            lk = n / 1_00_000
            return f"{lk:g} lakh rupees"
        if n >= 1_000:
            return f"{n:,} rupees"
        return f"{n} rupees"
    return _RUPEE.sub(_replace, text)


def format_for_voice(text: str) -> str:
    """Make agent text safe and natural for text-to-speech.

    Pipeline:
    1. Strip all markdown (tables, headings, bullets, emphasis, URLs)
    2. Humanise currency figures (₹ → spoken rupees)
    3. Collapse whitespace into spoken-friendly sentences
    """
    if not text:
        return ""
    text = _TABLE.sub("", text)
    text = _URL.sub("", text)
    text = _HEAD.sub("", text)
    text = _BULLET.sub("", text)
    text = _EMPH.sub("", text)
    text = text.replace("&", " and ")
    text = _humanise_numbers(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", ". ", text)
    text = re.sub(r"\n", " ", text)
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    return re.sub(r"\.{2,}", ".", text).strip()


# Filler phrases spoken IMMEDIATELY while the agent thinks — kills dead air.
_FILLERS: dict[str, str] = {
    "flight":   "Sure, let me check those flights...",
    "fly":      "Sure, let me check those flights...",
    "hotel":    "Looking up hotels for you...",
    "stay":     "Looking up hotels for you...",
    "room":     "Looking up hotels for you...",
    "tour":     "Checking tour options...",
    "safari":   "Checking tour options...",
    "burj":     "Checking tour options...",
    "transfer": "Looking up transfers...",
    "taxi":     "Looking up transfers...",
    "visa":     "Pulling visa info...",
    "budget":   "Let me run those numbers...",
    "cost":     "Let me run those numbers...",
    "price":    "Let me run those numbers...",
    "plan":     "On it, give me just a moment...",
    "trip":     "On it, give me just a moment...",
    "itinerary":"On it, give me just a moment...",
}
_DEFAULT_FILLER = "Got it, one moment..."


def _filler_for(transcript: str) -> str:
    t = transcript.lower()
    for keyword, filler in _FILLERS.items():
        if keyword in t:
            return filler
    return _DEFAULT_FILLER


def run_planner_turn(transcript: str, session_id: str) -> str:
    """Run ONE planner turn, capture the trace (turns + API calls), return speakable text."""
    thread_id = thread_id_for_session(session_id)
    transcript = (transcript or "").strip()
    if not transcript:
        return "Sorry, I didn't catch that — could you say it again?"

    cursor = latest_http_seq()  # mark, so we capture only this turn's API calls
    start = time.perf_counter()
    reply = ""
    api_calls: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []

    def _invoke(tid: str):
        resp = invoke_and_log(
            get_voice_agent(), surface="voice", thread_id=tid, user_message=transcript
        )
        text = format_for_voice(extract_assistant_text(resp)) or (
            "Let me have a team member follow up with the exact details."
        )
        tcs = []
        for tc in extract_tool_calls(resp):
            out = tc.get("output")
            out_full = json.dumps(out, default=str, indent=2) if isinstance(out, (dict, list)) else str(out)
            tcs.append({"tool": tc.get("tool_name"), "input": tc.get("input"), "output": out_full})
        return text, tcs

    try:
        reply, tools = _invoke(thread_id)
    except Exception as e:  # noqa: BLE001
        etype = type(e).__name__
        emsg = str(e)
        log.error("voice_agent_failed", error=emsg, error_type=etype)
        # A corrupted thread (tool_calls with no ToolMessage — e.g. an earlier
        # turn was interrupted) poisons EVERY later turn. Recover by starting a
        # fresh thread for this caller and retrying once, so the call continues.
        if "INVALID_CHAT_HISTORY" in emsg or "tool_calls" in emsg or "ToolMessage" in emsg:
            try:
                _SESSION_SALT[session_id] = _SESSION_SALT.get(session_id, 0) + 1
                fresh_tid = thread_id_for_session(session_id)
                log.info("voice_thread_reset", session_id=session_id, new_thread=fresh_tid)
                reply, tools = _invoke(fresh_tid)
            except Exception as e2:  # noqa: BLE001
                log.error("voice_agent_retry_failed", error=str(e2))
                reply = "Sorry, let me start that again — could you tell me where you'd like to travel?"
        else:
            reply = "Sorry, I hit a snag on my side. Please try again in a moment."

    api_calls = http_requests_since(cursor)  # exact booking-API calls this turn
    _record_turn(
        session_id,
        {
            "user": transcript,
            "agent": reply,
            "latency_s": round(time.perf_counter() - start, 1),
            "api_calls": api_calls,
            "tools": tools,
        },
    )
    return reply


# =============================================================================
# FastAPI app — Vapi Custom-LLM webhook (the brain), in Python (no NestJS)
# =============================================================================
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging(prod=False)
    log.info("voice_service_ready", vapi_configured=bool(VAPI_API_KEY))
    yield


app = FastAPI(title="Trip Planner — Voice Service", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _sse_chunk(cid: str, model: str, delta: dict[str, Any], finish: str | None) -> str:
    return "data: " + json.dumps(
        {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": 1_700_000_000,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
    ) + "\n\n"


@app.post("/api/webhook/chat/completions")
@app.post("/voice/chat/completions")
async def vapi_chat_completions(request: Request) -> Any:
    """Vapi Custom-LLM endpoint. Point Vapi model.url at .../api/webhook.

    Vapi POSTs an OpenAI chat-completions request (stream:true). We extract the
    latest user turn + call id, run the planner, and stream the reply back as SSE.
    """
    body = await request.json()
    messages = body.get("messages") if isinstance(body, dict) else None
    messages = messages if isinstance(messages, list) else []
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    transcript = (last_user or {}).get("content", "") if last_user else ""
    session_id = (
        (body.get("call") or {}).get("id")
        or (body.get("metadata") or {}).get("callId")
        or body.get("callId")
        or "vapi-call"
    )
    model = body.get("model") or "trip-planner"
    cid = f"chatcmpl-{session_id}"
    streaming = body.get("stream", True)

    def gen():
        yield _sse_chunk(cid, model, {"role": "assistant"}, None)
        if not transcript.strip():
            reply = "Hello! I'm your Dubai trip planner. Where are you flying from?"
        else:
            # Stream a filler immediately — Vapi speaks this while agent searches.
            # Keeps the call alive; no dead air while booking APIs respond.
            yield _sse_chunk(cid, model, {"content": _filler_for(transcript)}, None)
            reply = run_planner_turn(transcript, session_id)
        yield _sse_chunk(cid, model, {"content": reply}, None)
        yield _sse_chunk(cid, model, {}, "stop")
        yield "data: [DONE]\n\n"

    if streaming:
        return StreamingResponse(gen(), media_type="text/event-stream")

    reply = run_planner_turn(transcript, session_id) if transcript.strip() else "Hello!"
    return JSONResponse(
        {
            "id": cid,
            "object": "chat.completion",
            "created": 1_700_000_000,
            "model": model,
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}
            ],
        }
    )


# Plain JSON endpoint for the Streamlit "test without phone" path + local testing.
@app.post("/voice")
async def voice_turn(request: Request) -> JSONResponse:
    body = await request.json()
    transcript = body.get("transcript", "")
    session_id = body.get("session_id") or body.get("call_id") or "anon"
    reply = run_planner_turn(transcript, session_id)
    return JSONResponse({"reply": reply, "session_id": session_id})


# Place a call straight from the service (Streamlit calls this).
@app.post("/call")
async def call_endpoint(request: Request) -> JSONResponse:
    body = await request.json()
    res = place_call(body.get("number", ""), schedule_unix=body.get("schedule_unix"))
    return JSONResponse(res)


@app.get("/trace")
async def trace_endpoint(session_id: str | None = None) -> JSONResponse:
    return JSONResponse(get_trace(session_id))
