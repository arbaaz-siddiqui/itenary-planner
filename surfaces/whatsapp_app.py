"""WhatsApp surface — FastAPI webhook for Twilio.

Run (dev):
    uvicorn surfaces.whatsapp_app:app --reload --port 8000

Run (prod):
    uvicorn surfaces.whatsapp_app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import FastAPI, Form, Response
from twilio.rest import Client

from agent import (
    build_react_agent,
    build_sqlite_checkpoint,
    configure_logging,
    extract_assistant_text,
    get_logger,
    invoke_and_log,
)
from settings import get_twilio_settings

log = get_logger("whatsapp")


# =============================================================================
# Per-phone agent + thread_id
# =============================================================================
@lru_cache(maxsize=1)
def get_whatsapp_agent() -> object:
    """Singleton agent. SqliteSaver keyed by thread_id handles per-user state."""
    return build_react_agent(
        surface="whatsapp",
        checkpoint_store=build_sqlite_checkpoint(),
    )


def thread_id_for_phone(phone_number: str) -> str:
    """'whatsapp:+919876543210' → 'wa_919876543210'."""
    cleaned = phone_number.replace("whatsapp:", "").replace("+", "").strip()
    return f"wa_{cleaned}"


# =============================================================================
# Format for WhatsApp (strip MD tables, convert ** → *, cap length)
# =============================================================================
MAX_MESSAGE_CHARS = 1500
TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
TABLE_SEP_RE = re.compile(r"^\s*\|[\s\-:|]+\|\s*$", re.MULTILINE)
MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
MD_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
MD_BULLET_RE = re.compile(r"^\s*[-*]\s+", re.MULTILINE)


def format_for_whatsapp(text: str) -> str:
    if not text:
        return ""
    text = TABLE_SEP_RE.sub("", text)
    text = TABLE_LINE_RE.sub("", text)
    text = MD_HEADING_RE.sub(r"*\1*", text)
    text = MD_BOLD_RE.sub(r"*\1*", text)
    text = MD_BULLET_RE.sub("• ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[: MAX_MESSAGE_CHARS - 20].rstrip() + "\n\n(continued…)"
    return text


# =============================================================================
# Twilio client
# =============================================================================
@lru_cache(maxsize=1)
def _twilio_client() -> Client:
    s = get_twilio_settings()
    return Client(s.account_sid, s.auth_token)


def send_whatsapp(to_phone: str, body: str) -> None:
    s = get_twilio_settings()
    if not s.account_sid or not s.auth_token:
        log.warning("twilio_not_configured", to=to_phone, body_len=len(body))
        return
    try:
        _twilio_client().messages.create(from_=s.whatsapp_from, to=to_phone, body=body)
        log.info("whatsapp_sent", to=to_phone, body_len=len(body))
    except Exception as e:
        log.error("whatsapp_send_failed", to=to_phone, error=str(e))
        raise


# =============================================================================
# FastAPI app
# =============================================================================
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging(prod=True)
    log.info("whatsapp_service_starting")
    get_whatsapp_agent()  # warm-load
    get_twilio_settings()  # surface config errors early
    log.info("whatsapp_service_ready")
    yield
    log.info("whatsapp_service_stopping")


app = FastAPI(title="Dubai Trip Planner — WhatsApp", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/whatsapp")
async def receive(
    From: str = Form(...),  # noqa: N803 -- Twilio's exact field name
    Body: str = Form(...),  # noqa: N803
) -> Response:
    """Twilio posts incoming messages here as application/x-www-form-urlencoded."""
    thread_id = thread_id_for_phone(From)
    bound = log.bind(thread_id=thread_id, surface="whatsapp")
    bound.info("incoming_whatsapp", from_phone=From, body_len=len(Body))

    try:
        response = invoke_and_log(
            get_whatsapp_agent(),
            surface="whatsapp",
            thread_id=thread_id,
            user_message=Body,
        )
        formatted = format_for_whatsapp(extract_assistant_text(response))
        send_whatsapp(From, formatted)
    except Exception as e:
        bound.error("agent_invoke_failed", error=str(e), error_type=type(e).__name__)
        send_whatsapp(
            From,
            "Sorry — I hit a snag on my side. Please try again in a moment, "
            "or message our team if it keeps happening.",
        )

    # Twilio expects an empty 200 OK
    return Response(content="", media_type="text/xml")
