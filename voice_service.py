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
        headers={"Authorization": f"Bearer {VAPI_API_KEY}", "Content-Type": "application/json"},
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


def thread_id_for_session(session_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", (session_id or "").replace("+", "")) or "anon"
    return f"voice_{cleaned}"


# Strip markup/URLs so nothing un-speakable reaches TTS.
_TABLE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_HEAD = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_EMPH = re.compile(r"[*_`#~]+")
_BULLET = re.compile(r"^\s*[-*•]\s+", re.MULTILINE)


def format_for_voice(text: str) -> str:
    if not text:
        return ""
    text = _TABLE.sub("", text)
    text = _URL.sub("", text)
    text = _HEAD.sub("", text)
    text = _BULLET.sub("", text)
    text = _EMPH.sub("", text)
    text = text.replace("&", " and ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", ". ", text)
    text = re.sub(r"\n", " ", text)
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    return re.sub(r"\.{2,}", ".", text).strip()


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
    try:
        response = invoke_and_log(
            get_voice_agent(), surface="voice", thread_id=thread_id, user_message=transcript
        )
        reply = format_for_voice(extract_assistant_text(response)) or (
            "Let me have a team member follow up with the exact details."
        )
        # Tool calls the agent made (name + input + output summary)
        for tc in extract_tool_calls(response):
            out = tc.get("output")
            summary = out
            if isinstance(out, (dict, list)):
                summary = json.dumps(out, default=str)[:600]
            elif isinstance(out, str):
                summary = out[:600]
            tools.append({"tool": tc.get("tool_name"), "input": tc.get("input"), "output": summary})
    except Exception as e:  # noqa: BLE001
        etype = type(e).__name__
        log.error("voice_agent_failed", error=str(e), error_type=etype)
        reply = (
            "That's taking a little longer to pull together. Let me send the full options "
            "to your WhatsApp right after this call."
            if "Recursion" in etype
            else "Sorry, I hit a snag on my side. Please try again in a moment."
        )

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
        reply = run_planner_turn(transcript, session_id) if transcript.strip() else (
            "Hello! How can I help you plan your trip today?"
        )
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
