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

import asyncio
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
    StreamResult,
    build_react_agent,
    build_sqlite_checkpoint,
    configure_logging,
    extract_assistant_text,
    extract_tool_calls,
    get_logger,
    invoke_and_log,
    stream_and_log,
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
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        body_text = ""
        try:
            body_text = e.read().decode()
        except Exception:  # noqa: BLE001
            pass
        log.error("voice_call_failed", number=number, http_status=e.code, response=body_text)
        try:
            detail = json.loads(body_text).get("message") or body_text
        except Exception:  # noqa: BLE001
            detail = body_text or str(e)
        return {"error": f"Vapi error {e.code}: {detail}", "number": number}
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
_KNOWN_SESSIONS: set[str] = set()


def _record_turn(session_id: str, turn: dict[str, Any]) -> None:
    with _TRACE_LOCK:
        # New call detected — wipe all previous sessions so only current call is visible
        if session_id not in _KNOWN_SESSIONS:
            _TRACES.clear()
            _KNOWN_SESSIONS.clear()
            _KNOWN_SESSIONS.add(session_id)
        _TRACES[session_id].append(turn)


def get_trace(session_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
    with _TRACE_LOCK:
        if session_id:
            return {session_id: list(_TRACES.get(session_id, []))}
        return {sid: list(turns) for sid, turns in _TRACES.items()}


def clear_trace() -> None:
    with _TRACE_LOCK:
        _TRACES.clear()
        _KNOWN_SESSIONS.clear()


# =============================================================================
# Planner agent (the brain)
# =============================================================================
_voice_agent_instance: object | None = None

def get_voice_agent() -> object:
    global _voice_agent_instance
    if _voice_agent_instance is None:
        _voice_agent_instance = build_react_agent(surface="voice", checkpoint_store=build_sqlite_checkpoint())
    return _voice_agent_instance


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
_RUPEE = re.compile(r"₹\s*([\d,]+)")           # ₹1,00,000 → "1.3 lakh rupees"
_PLAIN_LAKH = re.compile(r"\b(\d+\.\d+)\s*lakh\b", re.IGNORECASE)  # 1.33073 lakh → 1.3 lakh
_BAGGAGE = re.compile(                          # strip baggage/refund details entirely
    r"(baggage|baggaj|check[-\s]?in|hand\s*bag|cabin\s*bag|refundable|non[-\s]?refundable"
    r"|kg\s*check|kg\s*hand|\d+\s*kg)[^.]*",
    re.IGNORECASE,
)


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
            cr = round(n / 10_00_000, 1)
            return f"{cr} crore rupees"
        if n >= 1_00_000:
            lk = round(n / 1_00_000, 1)
            return f"{lk} lakh rupees"
        if n >= 1_000:
            return f"{round(n / 1000, 1)} thousand rupees"
        return f"{n} rupees"
    return _RUPEE.sub(_replace, text)


# Masculine → feminine Hindi verb form replacements.
# Applied after LLM output so gender is correct regardless of what the model says.
_GENDER_FIXES: list[tuple[str, str]] = [
    # first person singular
    (r"\bkarunga\b",       "karungi"),
    (r"\bsakta hoon\b",    "sakti hoon"),
    (r"\bbata sakta\b",    "bata sakti"),
    (r"\bcheck kar sakta\b","check kar sakti"),
    (r"\bde sakta\b",      "de sakti"),
    (r"\bnikal sakta\b",   "nikal sakti"),
    (r"\bkar sakta\b",     "kar sakti"),
    (r"\bsamajh gaya\b",   "samajh gayi"),
    (r"\bsunata hoon\b",   "sunati hoon"),
    (r"\bbatata hoon\b",   "batati hoon"),
    (r"\bkarta hoon\b",    "karti hoon"),
    (r"\bdekh raha hoon\b","dekh rahi hoon"),
    (r"\bbol raha hoon\b", "bol rahi hoon"),
    (r"\bcheck kar raha hoon\b","check kar rahi hoon"),
    (r"\bsearch kar raha hoon\b","search kar rahi hoon"),
    (r"\bjaanta hoon\b",   "jaanti hoon"),
    (r"\bchahta hoon\b",   "chahti hoon"),
    # third person / future
    (r"\bkarega\b",        "karegi"),
    (r"\bhoga\b",          "hogi"),
    (r"\bpadega\b",        "padegi"),
    (r"\bmilega\b",        "milegi"),
    (r"\baayega\b",        "aayegi"),
    (r"\bbatayega\b",      "batayegi"),
    (r"\bbolega\b",        "bolegi"),
    # Devanagari → Roman for common slips
    ("बिल्कुल",            "Bilkul"),
    ("हां",                "Haan"),
    ("नहीं",               "Nahi"),
    ("ठीक है",             "Theek hai"),
    ("अच्छा",              "Acha"),
    ("शानदार",             "shandar"),
    ("बेहतरीन",            "behtareen"),
    ("चाहिए",              "chahiye"),
    ("करूंगा",             "karungi"),
    ("करूंगी",             "karungi"),
    ("निकाल सकता",         "nikal sakti"),
]


def _fix_gender(text: str) -> str:
    """Post-process LLM output to enforce feminine verb forms and Roman script."""
    for pattern, replacement in _GENDER_FIXES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


_EMOJI = re.compile(
    "[\U00010000-\U0010ffff"   # supplementary planes (most emoji)
    "\U0001F300-\U0001F9FF"    # misc symbols & pictographs
    "\U00002600-\U000027BF"    # misc symbols
    "\U0000FE00-\U0000FE0F"    # variation selectors
    "]+",
    flags=re.UNICODE,
)
# Strip CJK characters (Chinese/Japanese/Korean) — LLM occasionally slips these in
# when the conversation mixes Hindi scripts. TTS reads them incorrectly.
_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]+", flags=re.UNICODE)
_NUMBERED_ITEM = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)  # "1. foo" → strip number


def format_for_voice(text: str) -> str:
    """Make agent text safe and natural for text-to-speech.

    Pipeline:
    1. Strip markdown, emoji, numbered lists, URLs
    2. Humanise currency figures
    3. Hard-truncate to 2 sentences so TTS stays short
    """
    if not text:
        return ""
    text = _TABLE.sub("", text)
    text = _URL.sub("", text)
    text = _HEAD.sub("", text)
    text = _BULLET.sub("", text)
    text = _NUMBERED_ITEM.sub("", text)
    text = _EMPH.sub("", text)
    text = _EMOJI.sub("", text)
    text = _CJK.sub("", text)       # strip accidental Chinese/Japanese/Korean chars
    text = _BAGGAGE.sub("", text)   # strip baggage weights, refund status
    # Round ugly decimals: 1.33073 lakh → 1.3 lakh
    text = _PLAIN_LAKH.sub(lambda m: f"{round(float(m.group(1)), 1)} lakh", text)
    text = text.replace("&", " and ")
    text = _humanise_numbers(text)
    text = _fix_gender(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", ". ", text)
    text = re.sub(r"\n", " ", text)
    # Strip inline numbered list markers like " 2. " " 3. " left after joining lines
    text = re.sub(r"\s+\d+\.\s+", " ", text)
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    text = re.sub(r"\.{2,}", ".", text).strip()

    # Hard cap: keep only first 2 sentences so the agent never rambles on voice.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) > 2:
        text = " ".join(sentences[:2])

    return text


# =============================================================================
# Emotion / intent detection
# =============================================================================
# Intent influences: filler phrase, response brevity instruction, search urgency.
# Detected purely from text — no audio model needed.

_URGENCY_SIGNALS = re.compile(
    r"\b(urgent|urgently|asap|jaldi|abhi|turant|immediately|right now|aaj|kal|tomorrow|today)\b",
    re.IGNORECASE,
)
_CONFUSION_SIGNALS = re.compile(
    r"\b(matlab|kya matlab|samjha nahi|samajh nahi|kya|what|huh|pardon|sorry\?|"
    r"again|dobara|repeat|clear nahi|nahi samjha|nahi samjhi)\b",
    re.IGNORECASE,
)
_DETAIL_SIGNALS = re.compile(
    r"\b(detail|details|bata|batao|explain|explain karo|full|poori|puri|complete|sab kuch|"
    r"zyada|aur batao|more info|everything|all options|sab options)\b",
    re.IGNORECASE,
)
_SATISFACTION_SIGNALS = re.compile(
    r"\b(theek hai|theek|accha|acha|sahi|sahi hai|ok|okay|perfect|bilkul|done|haan theek|"
    r"sounds good|book karo|confirm|yes)\b",
    re.IGNORECASE,
)


def _detect_intent(transcript: str) -> dict[str, bool]:
    return {
        "urgent":     bool(_URGENCY_SIGNALS.search(transcript)),
        "confused":   bool(_CONFUSION_SIGNALS.search(transcript)),
        "wants_detail": bool(_DETAIL_SIGNALS.search(transcript)),
        "satisfied":  bool(_SATISFACTION_SIGNALS.search(transcript)),
    }


# =============================================================================
# Smart filler — echo back what we understood + context-aware urgency
# =============================================================================

# Patterns to pull structured facts out of the transcript for echo-back.
_ROUTE_RE = re.compile(
    r"\b(delhi|mumbai|bangalore|bengaluru|hyderabad|chennai|kolkata|pune|ahmedabad|"
    r"jaipur|dubai|london|singapore|bangkok|paris|new york|sydney)\b",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"\b(\d{1,2}(?:st|nd|rd|th)?\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|"
    r"apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)|"
    r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|"
    r"(?:aaj|kal|parso|next week|is week|is mahine))\b",
    re.IGNORECASE,
)


def _build_smart_filler(transcript: str, intent: dict[str, bool]) -> str:
    """Build a context-aware filler that echoes back what we understood.

    Goal: caller immediately hears "yes she got it" + the system starts thinking.
    If we can pull cities/dates from the transcript, we echo them back.
    Otherwise fall back to a keyword-based filler.
    """
    t_lower = transcript.lower()

    cities = _ROUTE_RE.findall(transcript)
    dates = _DATE_RE.findall(transcript)

    # Echo-back filler when we have enough info
    if len(cities) >= 2:
        origin, dest = cities[0].title(), cities[1].title()
        if dates:
            date_str = dates[0]
            return f"{origin} se {dest}, {date_str} — abhi check kar rahi hoon."
        return f"{origin} se {dest} ke liye dekh rahi hoon, ek second."
    if len(cities) == 1:
        dest = cities[0].title()
        if dates:
            return f"{dest} ke liye {dates[0]} — abhi check kar rahi hoon."
        if "hotel" in t_lower or "stay" in t_lower or "room" in t_lower:
            return f"Haan, {dest} mein hotels dekh rahi hoon."
        if "flight" in t_lower or "fly" in t_lower:
            return f"Haan, {dest} ke liye flights dekh rahi hoon."
        return f"Haan, {dest} ke baare mein dekh rahi hoon."

    # No cities — fall back to keyword / intent based fillers
    if intent.get("confused"):
        return "Haan ji, main samjhati hoon."
    if intent.get("urgent"):
        return "Haan, abhi check karti hoon!"
    if "flight" in t_lower or "fly" in t_lower:
        return "Hmm, flights dekh rahi hoon."
    if "hotel" in t_lower or "stay" in t_lower or "room" in t_lower:
        return "Haan ji, hotels dekh rahi hoon."
    if "tour" in t_lower or "safari" in t_lower or "burj" in t_lower:
        return "Haan, tours abhi dekhti hoon."
    if "transfer" in t_lower or "taxi" in t_lower:
        return "Acha, transfers check karti hoon."
    if "visa" in t_lower:
        return "Haan, visa details abhi dekhti hoon."
    if "budget" in t_lower or "cost" in t_lower or "price" in t_lower:
        return "Acha, pricing dekh rahi hoon."
    return "Haan, ek second."


# =============================================================================
# Backchanneling — short natural acknowledgment phrases
# =============================================================================
# Vapi sends a POST as soon as user finishes speaking. We can't insert sounds
# WHILE the user speaks (that's telephony-level, not LLM-level). But we can
# send a very short acknowledgment as the first SSE chunk — it plays in
# <200ms, making it feel like the agent was listening attentively.
_BACKCHANNEL_PHRASES = [
    "Haan.",
    "Hmm.",
    "Acha.",
    "Ji haan.",
    "Haan ji.",
    "Samajh gayi.",
    "Bilkul.",
]
_backchannel_idx: int = 0


def _pick_backchannel(transcript: str, intent: dict[str, bool]) -> str:
    """Pick a natural backchannel phrase that matches the context."""
    global _backchannel_idx  # noqa: PLW0603
    t = transcript.lower()
    if intent.get("confused"):
        return "Acha,"
    if intent.get("urgent"):
        return "Haan ji,"
    if intent.get("satisfied"):
        return "Bilkul,"
    if "thank" in t or "shukriya" in t or "dhanyawad" in t:
        return "Khushi hui."
    # Rotate through the list so it doesn't sound like a broken record
    phrase = _BACKCHANNEL_PHRASES[_backchannel_idx % len(_BACKCHANNEL_PHRASES)]
    _backchannel_idx += 1
    return phrase


# =============================================================================
# Response length instruction injected into user message
# =============================================================================

def _length_instruction(intent: dict[str, bool]) -> str:
    """Return a short instruction appended to the user message to guide reply length."""
    if intent.get("wants_detail"):
        return " [DETAIL MODE: give more info this turn — up to 4 sentences OK]"
    if intent.get("confused"):
        return " [CONFUSED CALLER: simplify — one very short sentence only]"
    return ""  # default: voice_addendum 2-sentence rule applies


# =============================================================================
# Planner helpers
# =============================================================================

def run_planner_turn(transcript: str, session_id: str) -> str:
    """Run ONE planner turn (blocking), return speakable text.

    Used by the non-streaming /voice endpoint and as fallback.
    """
    thread_id = thread_id_for_session(session_id)
    transcript = (transcript or "").strip()
    if not transcript:
        return "Sorry, I didn't catch that — could you say it again?"

    intent = _detect_intent(transcript)
    augmented = transcript + _length_instruction(intent)

    cursor = latest_http_seq()
    start = time.perf_counter()
    tools: list[dict[str, Any]] = []

    def _invoke(tid: str):
        resp = invoke_and_log(
            get_voice_agent(), surface="voice", thread_id=tid, user_message=augmented
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

    reply = ""
    try:
        reply, tools = _invoke(thread_id)
    except Exception as e:  # noqa: BLE001
        emsg = str(e)
        log.error("voice_agent_failed", error=emsg, error_type=type(e).__name__)
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

    _record_turn(
        session_id,
        {
            "user": transcript,
            "agent": reply,
            "latency_s": round(time.perf_counter() - start, 1),
            "api_calls": http_requests_since(cursor),
            "tools": tools,
        },
    )
    return reply


def _stream_planner_turn(
    transcript: str,
    session_id: str,
    on_token: Any,  # callable(token: str) -> None, called for each streamed token
) -> str:
    """Stream a planner turn, calling on_token for each text token as it arrives.

    Returns the final full reply (post-processed) so the caller can record trace.
    Tokens yielded via on_token are RAW (not format_for_voice'd) so the TTS
    starts speaking immediately. The returned full string IS cleaned for trace.
    """
    thread_id = thread_id_for_session(session_id)
    transcript = (transcript or "").strip()
    if not transcript:
        return "Sorry, I didn't catch that — could you say it again?"

    intent = _detect_intent(transcript)
    augmented = transcript + _length_instruction(intent)

    result = StreamResult()
    full_text = ""

    try:
        for token in stream_and_log(
            get_voice_agent(),
            surface="voice",
            thread_id=thread_id,
            user_message=augmented,
            result=result,
        ):
            on_token(token)
            full_text += token
    except Exception as e:  # noqa: BLE001
        emsg = str(e)
        log.error("voice_stream_failed", error=emsg)
        if "INVALID_CHAT_HISTORY" in emsg or "tool_calls" in emsg or "ToolMessage" in emsg:
            try:
                _SESSION_SALT[session_id] = _SESSION_SALT.get(session_id, 0) + 1
                fresh_tid = thread_id_for_session(session_id)
                for token in stream_and_log(
                    get_voice_agent(),
                    surface="voice",
                    thread_id=fresh_tid,
                    user_message=augmented,
                    result=result,
                ):
                    on_token(token)
                    full_text += token
            except Exception as e2:  # noqa: BLE001
                log.error("voice_stream_retry_failed", error=str(e2))
                full_text = "Sorry, let me start that again — could you tell me where you'd like to travel?"
                on_token(full_text)
        else:
            full_text = "Sorry, I hit a snag on my side. Please try again in a moment."
            on_token(full_text)

    return format_for_voice(full_text) or full_text


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

    # Heartbeat phrases spoken every ~4s while the agent is searching tools.
    # Keeps the call alive during long API waits (flights take 10-12s).
    _HEARTBEATS = [
        "Thoda waqt dijiye, results check ho rahe hain...",
        "Haan, almost aa gaye...",
        "Bas ek second aur...",
        "Results aa rahe hain, please hold...",
    ]

    # Detect intent once — used for both filler and length instruction
    intent = _detect_intent(transcript) if transcript.strip() else {}

    async def gen():
        yield _sse_chunk(cid, model, {"role": "assistant"}, None)
        if not transcript.strip():
            reply = "Hello! Main Nikki hoon Gujju Tours se. Kahan jaana hai aapko?"
            yield _sse_chunk(cid, model, {"content": reply}, None)
            yield _sse_chunk(cid, model, {}, "stop")
            yield "data: [DONE]\n\n"
            return

        # HOW VAPI CUSTOM LLM STREAMING ACTUALLY WORKS:
        # Vapi buffers ALL SSE chunks and sends the concatenated text to TTS
        # as ONE utterance. Token-by-token streaming does NOT improve latency.
        # The ONLY way to get immediate speech is to send a short complete
        # sentence FIRST (Vapi speaks it), then send the real answer (Vapi
        # speaks it next). Two sentence chunks = two TTS utterances in sequence.

        # HOW VAPI HEARTBEATS WORK:
        # Vapi DOES stream SSE chunks to TTS in real-time — each chunk triggers
        # a new TTS utterance. BUT only if the chunks arrive BEFORE Vapi's own
        # LLM response timeout (~20s). We send filler+heartbeats as real chunks
        # and Vapi speaks each one as it arrives.
        #
        # The re-search problem (agent re-runs search every turn) is a separate
        # issue — the agent isn't using SQLite checkpoint correctly for voice.

        # 1. Filler — spoken immediately (~200ms after caller stops)
        backchannel = _pick_backchannel(transcript, intent)
        filler = _build_smart_filler(transcript, intent)
        yield _sse_chunk(cid, model, {"content": backchannel + " " + filler}, None)

        # 2. Run planner in background thread
        loop = asyncio.get_event_loop()
        cursor_before = latest_http_seq()
        start_time = time.perf_counter()
        future = loop.run_in_executor(None, run_planner_turn, transcript, session_id)

        # 3. Send a heartbeat every 4s while planner runs — Vapi speaks each one
        heartbeat_idx = 0
        while not future.done():
            try:
                await asyncio.wait_for(asyncio.shield(future), timeout=4.0)
            except asyncio.TimeoutError:
                phrase = _HEARTBEATS[heartbeat_idx % len(_HEARTBEATS)]
                yield _sse_chunk(cid, model, {"content": " " + phrase}, None)
                heartbeat_idx += 1

        try:
            final_reply = future.result()
        except Exception:
            final_reply = "Sorry, kuch issue aa gaya. Dobara try karein?"

        # 4. Real answer — Vapi speaks this after all heartbeats
        if heartbeat_idx > 0:
            final_reply = "Results aa gaye. " + final_reply
        yield _sse_chunk(cid, model, {"content": " " + final_reply}, None)
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


@app.delete("/trace")
async def clear_trace_endpoint() -> JSONResponse:
    clear_trace()
    return JSONResponse({"status": "cleared"})
