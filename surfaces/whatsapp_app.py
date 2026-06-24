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
from fastapi.responses import FileResponse
from twilio.rest import Client

from agent import (
    build_react_agent,
    build_sqlite_checkpoint,
    configure_logging,
    extract_assistant_text,
    extract_tool_calls,
    get_logger,
    invoke_and_log,
)
from itinerary_store import get_itinerary_path, public_url_for
from settings import get_twilio_settings

log = get_logger("whatsapp")


def _coerce_output(output: object) -> object:
    """Tool outputs arrive as dicts or JSON strings; normalize to a dict."""
    if isinstance(output, dict):
        return output
    if isinstance(output, str):
        import json

        try:
            return json.loads(output)
        except (ValueError, TypeError):
            return None
    return None


def _itinerary_id_from(response: dict) -> str | None:
    """Find an itinerary_id produced by generate_itinerary_pdf this turn."""
    for tc in extract_tool_calls(response):
        out = _coerce_output(tc.get("output"))
        if isinstance(out, dict) and out.get("itinerary_id"):
            return str(out["itinerary_id"])
    return None


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


def send_whatsapp(to_phone: str, body: str, media_url: str | None = None) -> None:
    s = get_twilio_settings()
    if not s.account_sid or not s.auth_token:
        log.warning("twilio_not_configured", to=to_phone, body_len=len(body))
        return
    kwargs: dict[str, object] = {"from_": s.whatsapp_from, "to": to_phone, "body": body}
    if media_url:
        # Twilio attaches the PDF to the WhatsApp message from a public URL.
        kwargs["media_url"] = [media_url]
    try:
        _twilio_client().messages.create(**kwargs)
        log.info("whatsapp_sent", to=to_phone, body_len=len(body), has_media=bool(media_url))
    except Exception as e:
        log.error("whatsapp_send_failed", to=to_phone, error=str(e))
        raise


# =============================================================================
# FastAPI app
# =============================================================================
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Keep startup cheap so the server binds and /health responds immediately —
    # platform healthchecks (Railway/Render) have tight windows. The LLM agent
    # is lazy-loaded (and lru_cached) on the first /whatsapp request instead of
    # warm-loaded here, so a slow build or a missing API key can't block boot
    # or fail the deploy's healthcheck.
    configure_logging(prod=True)
    log.info("whatsapp_service_ready")
    yield
    log.info("whatsapp_service_stopping")


app = FastAPI(title="Dubai Trip Planner — WhatsApp", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/itinerary/{itinerary_id}.pdf")
async def serve_itinerary(itinerary_id: str) -> Response:
    """Public PDF download — also the URL Twilio fetches to attach the PDF."""
    path = get_itinerary_path(itinerary_id)
    if path is None:
        return Response(content="Not found", status_code=404)
    return FileResponse(
        path,
        media_type="application/pdf",
        filename="Dubai-itinerary.pdf",
    )


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

        # If the agent generated an itinerary PDF this turn, attach it as media.
        media_url = None
        itinerary_id = _itinerary_id_from(response)
        if itinerary_id:
            media_url = public_url_for(itinerary_id)
            if media_url:
                bound.info("itinerary_pdf_attached", itinerary_id=itinerary_id)
            else:
                bound.warning("itinerary_pdf_no_public_url", itinerary_id=itinerary_id)

        send_whatsapp(From, formatted, media_url=media_url)
    except Exception as e:
        bound.error("agent_invoke_failed", error=str(e), error_type=type(e).__name__)
        send_whatsapp(
            From,
            "Sorry — I hit a snag on my side. Please try again in a moment, "
            "or message our team if it keeps happening.",
        )

    # Twilio expects an empty 200 OK
    return Response(content="", media_type="text/xml")
