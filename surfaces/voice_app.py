"""Voice surface — FastAPI bridge between the voice shell and the planner agent.

The voice layer (VoiceCare / Vapi / ElevenLabs) handles telephony, speech-to-text
and the Indian-voice text-to-speech. It calls THIS service mid-call with the
caller's transcribed words; we run the planner agent and return a SHORT, speakable
reply for the voice layer to read aloud.

This mirrors `whatsapp_app.py`: one stateless endpoint, text in -> agent -> text
out, with per-session conversation memory via the SQLite checkpointer.

Run (dev):
    uvicorn surfaces.voice_app:app --reload --port 8100

Run (prod):
    uvicorn surfaces.voice_app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import FastAPI
from pydantic import BaseModel, Field

from agent import (
    build_react_agent,
    build_sqlite_checkpoint,
    configure_logging,
    extract_assistant_text,
    get_logger,
    invoke_and_log,
)

log = get_logger("voice")


# =============================================================================
# Per-session agent + thread_id
# =============================================================================
@lru_cache(maxsize=1)
def get_voice_agent() -> object:
    """Singleton voice-tuned agent. SqliteSaver keyed by thread_id holds per-call
    conversation memory, exactly like the WhatsApp agent."""
    return build_react_agent(
        surface="voice",
        checkpoint_store=build_sqlite_checkpoint(),
    )


def thread_id_for_session(session_id: str) -> str:
    """Stable agent thread key for one ongoing call/conversation.

    The voice shell passes the provider's per-call id (Vapi `call.id` /
    ElevenLabs `conversation_id`) or, failing that, the caller's phone number.
    We namespace it so voice threads never collide with WhatsApp threads
    (`wa_...`) sharing the same SQLite store.
    """
    cleaned = session_id.replace("whatsapp:", "").replace("+", "").strip()
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", cleaned) or "anon"
    return f"voice_{cleaned}"


# =============================================================================
# Format for speech (TTS reads this aloud — strip ALL markup + URLs)
# =============================================================================
# Even with the voice prompt addendum, a model can still emit a stray asterisk,
# heading, table row, or URL. TTS reads those literally ("star", "h t t p"), so
# this is a hard safety net, not a nicety.
TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
MD_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
MD_EMPHASIS_RE = re.compile(r"[*_`#~]+")
MD_BULLET_RE = re.compile(r"^\s*[-*•]\s+", re.MULTILINE)


def format_for_voice(text: str) -> str:
    """Make agent text safe and natural for text-to-speech.

    Removes markdown tables, headings, emphasis characters, bullets and URLs,
    and collapses whitespace into spoken-friendly sentences.
    """
    if not text:
        return ""
    text = TABLE_LINE_RE.sub("", text)  # drop table rows entirely
    text = URL_RE.sub("", text)  # never read a URL aloud
    text = MD_HEADING_RE.sub("", text)  # "## Hotels" -> "Hotels"
    text = MD_BULLET_RE.sub("", text)  # "- item" -> "item"
    text = MD_EMPHASIS_RE.sub("", text)  # strip * _ ` # ~
    text = text.replace("&", " and ")
    # Collapse blank lines/extra spaces; periods make TTS pause naturally.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", ". ", text)
    text = re.sub(r"\n", " ", text)
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    text = re.sub(r"\.{2,}", ".", text)
    return text.strip()


# =============================================================================
# Request / response models
# =============================================================================
class VoiceTurn(BaseModel):
    """One transcribed caller turn from the voice shell.

    `session_id` is the conversation key. We also accept the provider-native
    aliases (`call_id`, `conversation_id`) and `phone`, picking the first that
    is present — so the voice shell can forward whatever it has without us
    guessing the field name.
    """

    transcript: str = Field(..., description="What the caller said (STT output).")
    session_id: str | None = None
    call_id: str | None = None
    conversation_id: str | None = None
    phone: str | None = None

    def resolved_session(self) -> str:
        return (
            self.session_id
            or self.call_id
            or self.conversation_id
            or self.phone
            or "anon"
        )


class VoiceReply(BaseModel):
    reply: str = Field(..., description="Speakable text for the voice layer to read aloud.")
    session_id: str


# =============================================================================
# FastAPI app
# =============================================================================
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Keep startup cheap so health checks pass immediately; the LLM agent is
    # lazy-loaded (lru_cached) on the first /voice request — same pattern as the
    # WhatsApp service.
    configure_logging(prod=True)
    log.info("voice_service_ready")
    yield
    log.info("voice_service_stopping")


app = FastAPI(title="Dubai Trip Planner — Voice Bridge", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/voice", response_model=VoiceReply)
async def voice_turn(turn: VoiceTurn) -> VoiceReply:
    """Run one caller turn through the planner agent and return speakable text.

    Stateless per request; conversation memory is held by the checkpointer keyed
    on the resolved session id, so multi-turn calls remember context.
    """
    session = turn.resolved_session()
    thread_id = thread_id_for_session(session)
    bound = log.bind(thread_id=thread_id, surface="voice")
    bound.info("incoming_voice_turn", transcript_len=len(turn.transcript))

    transcript = (turn.transcript or "").strip()
    if not transcript:
        # Empty STT (silence / noise) — ask the caller to repeat rather than
        # invoking the agent on nothing.
        return VoiceReply(reply="Sorry, I didn't catch that — could you say it again?", session_id=session)

    try:
        response = invoke_and_log(
            get_voice_agent(),
            surface="voice",
            thread_id=thread_id,
            user_message=transcript,
        )
        reply = format_for_voice(extract_assistant_text(response))
        if not reply:
            reply = "Let me have a team member follow up with the exact details."
        bound.info("voice_reply_sent", reply_len=len(reply))
    except Exception as e:
        etype = type(e).__name__
        bound.error("voice_agent_failed", error=str(e), error_type=etype)
        if "Recursion" in etype:
            # The turn needed too many lookups for a live call. Don't make the
            # caller wait — acknowledge and offer to send details after.
            reply = (
                "That's taking a bit longer to pull together. Let me get the "
                "full options and send them to your WhatsApp right after this call. "
                "Anything else I can help with meanwhile?"
            )
        else:
            reply = (
                "Sorry, I hit a snag on my side. Please try again in a moment, "
                "or I can have our team call you back."
            )

    return VoiceReply(reply=reply, session_id=session)
