"""FastAPI chat service for the Next.js surface.

Customers must sign in before they can reach the agent: every /chat route
depends on `current_user`, which raises 401 without a valid session cookie.

Runs as its own Railway service alongside the existing Streamlit app, which is
untouched and stays live. Postgres is reached over Railway's private network,
so the database is never exposed to the internet.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field

from . import auth, db, repository

log = logging.getLogger("chat_api")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Migrations run here because the database is on Railway's PRIVATE network:
    # `postgres.railway.internal` resolves only inside Railway, so this is the
    # only place they can run without exposing the DB publicly. They are
    # idempotent, so running on every boot is safe.
    #
    # Failure is logged, not raised: a migration error must not stop the
    # service binding, or Railway's healthcheck fails and the deploy rolls back
    # with no way to read the reason.
    from agent import configure_logging

    configure_logging(prod=True)

    # Fail fast and loudly if SESSION_SECRET is missing. Without this the
    # service starts happily and every login returns 500 at the moment a
    # customer tries it, which is far harder to diagnose than a refusal here.
    try:
        auth._secret()
    except RuntimeError as e:
        log.error("auth_misconfigured: %s", e)

    try:
        applied = db.run_migrations()
        log.info("migrations_applied", extra={"files": applied})
    except Exception as e:  # noqa: BLE001
        log.error("migrations_failed: %s", e)
    yield
    db.close_pool()
    log.info("chat_api_stopping")


app = FastAPI(title="Dubai Trip Planner — Chat API", lifespan=lifespan)

# The browser sends the session cookie cross-origin (web service -> api
# service), so credentials must be allowed and the origin echoed explicitly:
# "*" is rejected by browsers when credentials are included.
import os as _os

_origins = [
    o.strip()
    for o in _os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- schemas
class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class Registration(Credentials):
    # Optional so an older client that sends only email + password still
    # registers; the UI asks for both and greets by first name.
    first_name: str = Field(default="", max_length=80)
    last_name: str = Field(default="", max_length=80)


class Preferences(BaseModel):
    """Travel defaults the customer sets in Settings.

    Stored with the facts the agent already remembers between chats, under
    the same keys, so a preference set here and one the agent learned from
    the conversation are the same thing and the newest wins.
    """

    origin_city: str = Field(default="", max_length=60)
    usual_party_size: int | None = Field(default=None, ge=1, le=20)
    diet: Literal["", "Veg", "Jain", "Vegan"] = ""
    nationality: str = Field(default="", max_length=60)


_PREF_KEYS = ("origin_city", "usual_party_size", "diet", "nationality")


class ProfileUpdate(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(default="", max_length=80)


def _user_out(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(user["id"]),
        "email": user["email"],
        "first_name": user.get("first_name") or "",
        "last_name": user.get("last_name") or "",
    }


class SessionCreate(BaseModel):
    title: str | None = None


class SessionUpdate(BaseModel):
    """Rename and/or pin a chat. Either field may be left out."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    pinned: bool | None = None


class SavedCard(BaseModel):
    """One hearted result card, stored as the customer saw it."""

    kind: str = Field(min_length=1, max_length=40)
    item: dict[str, Any]


class UiSettings(BaseModel):
    """Screen preferences that follow the account across devices."""

    theme: Literal["dark", "light", "system"] | None = None
    sidebar_collapsed: bool | None = None


_UI_KEYS = ("theme", "sidebar_collapsed")
# One saved card is one option payload; this bounds a single save.
_SAVED_ITEM_MAX_BYTES = 64_000


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1)
    # "ask": answer the question in hand. "plan": build a whole trip.
    mode: Literal["ask", "plan"] = "ask"
    # Answer the last question again, replacing the reply it got.
    regenerate: bool = False


class Feedback(BaseModel):
    value: Literal["up", "down"] | None = None


# ----------------------------------------------------------------- health
@app.get("/health")
async def health() -> dict[str, str]:
    """Kept trivial and DB-free so Railway's healthcheck passes even when
    Postgres is briefly unavailable — a failing probe would roll the deploy
    back and take the whole service down with it."""
    return {"status": "ok"}


@app.get("/itinerary/{itinerary_id}.pdf")
async def serve_itinerary(itinerary_id: str) -> Response:
    """The generated itinerary PDF.

    Public on purpose, like the WhatsApp surface's copy of this route: the
    customer opens the link from a chat message, often on a device that is not
    signed in, and the id is an unguessable hash. Without this route the PDF
    was written and linked but every download 404'd.
    """
    from fastapi.responses import FileResponse

    from itinerary_store import get_itinerary_path

    path = get_itinerary_path(itinerary_id)
    if path is None:
        return Response(content="Not found", status_code=404)
    return FileResponse(
        path, media_type="application/pdf", filename="Dubai-itinerary.pdf"
    )


@app.get("/health/db")
async def health_db() -> dict[str, Any]:
    """Separate, so a DB problem is diagnosable without failing the deploy."""
    try:
        return {"status": "ok", **db.healthcheck()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(e)) from e


# ------------------------------------------------------------------- auth
@app.post("/auth/register", status_code=status.HTTP_201_CREATED)
async def register(body: Registration, response: Response) -> dict[str, Any]:
    if repository.find_user_by_email(body.email):
        raise HTTPException(status_code=409, detail="That email is already registered")
    user = repository.create_user(
        body.email, auth.hash_password(body.password), body.first_name, body.last_name
    )
    auth.set_session_cookie(response, str(user["id"]))
    return {"user": _user_out(user)}


@app.post("/auth/login")
async def login(body: Credentials, response: Response) -> dict[str, Any]:
    user = repository.find_user_by_email(body.email)
    # One message for both "no such account" and "wrong password", so the
    # response cannot be used to discover which emails are registered.
    if not user or not auth.verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    auth.set_session_cookie(response, str(user["id"]))
    return {"user": _user_out(user)}


@app.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    auth.clear_session_cookie(response)


@app.get("/auth/me")
async def me(user: dict = Depends(auth.current_user)) -> dict[str, Any]:
    return {"user": _user_out(user)}


@app.get("/me/preferences")
async def get_preferences(user: dict = Depends(auth.current_user)) -> dict[str, Any]:
    facts = repository.get_memory(str(user["id"]))
    return {"preferences": {k: facts.get(k) for k in _PREF_KEYS if facts.get(k) not in (None, "")}}


@app.patch("/me/preferences")
async def set_preferences(
    body: Preferences, user: dict = Depends(auth.current_user)
) -> dict[str, Any]:
    # An empty field clears that preference rather than being ignored, so the
    # customer can take one back.
    facts = {k: (v if v not in ("", None) else None) for k, v in body.model_dump().items()}
    merged = repository.merge_memory(str(user["id"]), facts)
    return {"preferences": {k: merged.get(k) for k in _PREF_KEYS if merged.get(k) not in (None, "")}}


@app.patch("/auth/me")
async def update_me(
    body: ProfileUpdate, user: dict = Depends(auth.current_user)
) -> dict[str, Any]:
    updated = repository.update_user_name(str(user["id"]), body.first_name, body.last_name)
    if not updated:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"user": _user_out(updated)}


# ----------------------------------------------------- saved cards, settings
@app.get("/me/saved-cards")
async def get_saved_cards(user: dict = Depends(auth.current_user)) -> dict[str, Any]:
    return {
        "cards": [
            {"id": r["card_id"], "kind": r["kind"], "item": r["item"],
             "saved_at": r["created_at"].isoformat()}
            for r in repository.list_saved_cards(str(user["id"]))
        ]
    }


@app.put("/me/saved-cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def put_saved_card(
    card_id: str, body: SavedCard, user: dict = Depends(auth.current_user)
) -> Response:
    if len(card_id) > 300:
        raise HTTPException(status_code=422, detail="Card id too long")
    if len(json.dumps(body.item)) > _SAVED_ITEM_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Card too large to save")
    repository.save_card(str(user["id"]), card_id, body.kind, body.item)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.delete("/me/saved-cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_card(card_id: str, user: dict = Depends(auth.current_user)) -> Response:
    # Idempotent: unsaving a card that is already gone is not an error.
    repository.unsave_card(str(user["id"]), card_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/me/settings")
async def get_ui_settings(user: dict = Depends(auth.current_user)) -> dict[str, Any]:
    s = repository.get_ui_settings(str(user["id"]))
    return {"settings": {k: s[k] for k in _UI_KEYS if k in s}}


@app.patch("/me/settings")
async def patch_ui_settings(
    body: UiSettings, user: dict = Depends(auth.current_user)
) -> dict[str, Any]:
    changes = body.model_dump(exclude_none=True)
    uid = str(user["id"])
    s = repository.merge_ui_settings(uid, changes) if changes else repository.get_ui_settings(uid)
    return {"settings": {k: s[k] for k in _UI_KEYS if k in s}}


# --------------------------------------------------------------- sessions
@app.get("/chat/sessions")
async def get_sessions(user: dict = Depends(auth.current_user)) -> list[dict[str, Any]]:
    return [_session_out(s) for s in repository.list_sessions(str(user["id"]))]


@app.post("/chat/sessions", status_code=status.HTTP_201_CREATED)
async def post_session(
    body: SessionCreate, user: dict = Depends(auth.current_user)
) -> dict[str, Any]:
    s = repository.create_session(str(user["id"]), body.title or "New chat")
    return _session_out(s)


@app.patch("/chat/sessions/{session_id}")
async def patch_session(
    session_id: str, body: SessionUpdate, user: dict = Depends(auth.current_user)
) -> dict[str, Any]:
    s = repository.update_session(
        session_id, str(user["id"]), title=body.title, pinned=body.pinned
    )
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_out(s)


@app.delete("/chat/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_session(
    session_id: str, user: dict = Depends(auth.current_user)
) -> None:
    if not repository.delete_session(session_id, str(user["id"])):
        raise HTTPException(status_code=404, detail="Session not found")


@app.get("/chat/sessions/{session_id}/messages")
async def get_messages(
    session_id: str, user: dict = Depends(auth.current_user)
) -> list[dict[str, Any]]:
    if not repository.get_session(session_id, str(user["id"])):
        raise HTTPException(status_code=404, detail="Session not found")
    return [
        {
            "id": str(m["id"]),
            "role": m["role"],
            "content": m["content"],
            "created_at": m["created_at"].isoformat(),
            # Without this the option cards -- and every tour image on them --
            # were gone the moment the page reloaded.
            "options": m.get("options") or [],
            "feedback": {1: "up", -1: "down"}.get(m.get("feedback")),
        }
        for m in repository.list_messages(session_id)
    ]


@app.get("/chat/sessions/{session_id}/status")
async def session_status(session_id: str, user: dict = Depends(auth.current_user)) -> dict[str, Any]:
    """Whether a reply is still being worked on in this chat."""
    if not repository.get_session(session_id, str(user["id"])):
        raise HTTPException(status_code=404, detail="Session not found")
    task = _RUNNING.get(session_id)
    return {"busy": bool(task and not task.done())}


@app.post("/chat/sessions/{session_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_turn(session_id: str, user: dict = Depends(auth.current_user)) -> Response:
    """Stop button: drop the reply in progress; nothing is saved for it."""
    if not repository.get_session(session_id, str(user["id"])):
        raise HTTPException(status_code=404, detail="Session not found")
    task = _RUNNING.pop(session_id, None)
    if task and not task.done():
        task.cancel()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/chat/messages/{message_id}/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def rate_message(
    message_id: str, body: Feedback, user: dict = Depends(auth.current_user)
) -> Response:
    value = {"up": 1, "down": -1}.get(body.value or "")
    if not repository.set_feedback(message_id, str(user["id"]), value):
        raise HTTPException(status_code=404, detail="Message not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/chat/messages/{message_id}/tool-calls")
async def get_tool_calls(
    message_id: str, user: dict = Depends(auth.current_user)
) -> list[dict[str, Any]]:
    """Debug payload for one message, fetched only when the panel opens —
    these reach 200KB+ for a tour search. Only for the owner of the chat."""
    calls = repository.get_message_tool_calls(message_id, str(user["id"]))
    if calls is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return calls


@app.get("/tours/{tour_id}/images")
async def tour_images(tour_id: int, _user: dict = Depends(auth.current_user)) -> dict[str, Any]:
    """Every photo of a tour, for the detail panel's gallery.

    Search gives one photo per tour; the rest are read when the card is
    opened. An empty list (not an error) when none can be read, so the panel
    keeps the photo it already has.
    """
    from mcp_tools.tour_gallery import tour_image_urls

    return {"images": await asyncio.to_thread(tour_image_urls, tour_id)}


@app.get("/tours/{tour_id}/options/{option_id}/details")
async def tour_option_details(
    tour_id: int,
    option_id: str,
    supplier_id: int,
    _user: dict = Depends(auth.current_user),
) -> dict[str, Any]:
    """Inclusions, exclusions and policies for one tour variant.

    Fetched on demand when the customer opens "Show details" rather than with
    every search: it is one supplier call per variant, and a tour search
    returning 17 rows would otherwise cost 17 extra calls for panels nobody
    opened.

    Behind auth like every other route -- this is supplier content, not public.
    """
    from booking_api.endpoints import call_tour_option_description
    from parsers import parse_tour_option_description

    try:
        raw = call_tour_option_description(
            tour_id=tour_id, tour_option_id=option_id, supplier_id=supplier_id
        )
    except Exception as e:  # noqa: BLE001 -- a supplier failure is a 502, not a 500
        log.warning("tour_option_description_failed tour=%s option=%s: %s",
                    tour_id, option_id, e)
        raise HTTPException(
            status_code=502, detail="Could not load the details for this option."
        ) from e

    return {"sections": parse_tour_option_description(raw)}


# ----------------------------------------------------------------- stream
@app.post("/chat/stream")
async def chat_stream(
    body: ChatRequest, user: dict = Depends(auth.current_user)
) -> StreamingResponse:
    session = repository.get_session(body.session_id, str(user["id"]))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    running = _RUNNING.get(str(session["id"]))
    if running and not running.done():
        raise HTTPException(status_code=409, detail="Still answering your last message in this chat")
    return StreamingResponse(
        _stream_turn(session, body.message, body.mode, body.regenerate),
        media_type="text/event-stream",
        # Nginx and some proxies buffer SSE into one chunk at the end, which
        # defeats streaming entirely.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Plan mode turns a question into a trip. Sent as context ahead of the
# customer's words (like the remembered facts), never stored as their message.
# Ask mode needs no instruction: answering the question in hand is the
# agent's default.
_MODE_CONTEXT = {
    "plan": (
        "[system] Plan mode: the customer wants a complete trip planned, not a "
        "single answer. If the destination, travel dates, departure city or "
        "party (adults, children and their ages) is missing, ask for all the "
        "missing ones in ONE short question. Once known, search live flights, "
        "a hotel and tours, build the day-by-day plan with plan_itinerary_tool, "
        "give the trip total from the tools, and offer to change any part.\n\n"
    ),
}

# Follow-up chips after a reply that showed results, by result kind. Each is
# phrased as the customer would type it, because tapping one sends it as
# their next message. Only things the agent can actually do.
_FOLLOW_UPS: dict[str, list[str]] = {
    "tours": ["Show private transfer prices", "Add a hotel for these dates", "Plan my full trip"],
    "hotels": ["Only free cancellation", "Show cheaper hotels", "Add an airport transfer"],
    "flights": ["Show cheaper flights", "Only non-stop flights", "Find hotels in Dubai"],
    "restaurants": ["Jain-friendly options", "Restaurants near Burj Khalifa", "Show the menu"],
    "transfers": ["Shared transfer instead", "Add the return transfer", "Find a hotel"],
    "visa": ["Which documents do I need?", "How long does processing take?", "Find flights to Dubai"],
    "packages": ["Show package details", "Compare the top two", "Plan a custom trip"],
}

def _step_summary(out: Any) -> dict[str, Any]:
    """What the step line needs from a tool result: a count or a failure.

    The full result stays on the server. Sending it made every step event as
    large as the search itself, and the browser only ever showed a count.
    """
    if not isinstance(out, dict):
        return {}
    if out.get("error"):
        return {"error": str(out.get("message") or "failed")[:200]}
    for key in ("options", "results", "items"):
        if isinstance(out.get(key), list):
            return {"count": len(out[key])}
    return {}


_REGENERATE_CONTEXT = (
    "[system] The customer asked for a fresh answer to the message below, "
    "which they already sent once. Answer it again from live tool results.\n\n"
)

_TURN_FAILED = (
    "Sorry, I could not finish that one. Please send it again, and I will "
    "pick up from where we were."
)


# Turns in progress, by session id. A turn runs as its own task so it is NOT
# tied to the browser's connection: switching to another chat used to close
# the stream, which cancelled the turn before its reply was saved, so the
# answer was simply lost. Now the turn finishes and saves either way, and the
# chat shows "still working" until it has. Single process: Railway runs one
# worker for chat-api.
_RUNNING: dict[str, asyncio.Task[None]] = {}


async def _stream_turn(
    session: dict, message: str, mode: str = "ask", regenerate: bool = False
) -> AsyncIterator[str]:
    """Start a turn and relay its events for as long as the browser listens."""
    sid = str(session["id"])
    out: asyncio.Queue[str | None] = asyncio.Queue()

    async def run() -> None:
        try:
            async for event in _turn_events(session, message, mode, regenerate):
                out.put_nowait(event)
        except asyncio.CancelledError:
            log.info("turn_cancelled session=%s", sid)
            raise
        except Exception as e:  # noqa: BLE001 -- reported to the customer in words
            log.exception("turn_failed: %s", e)
            out.put_nowait(_sse("error", {"message": _TURN_FAILED}))
        finally:
            out.put_nowait(None)
            if _RUNNING.get(sid) is task:
                _RUNNING.pop(sid, None)

    task = asyncio.create_task(run())
    _RUNNING[sid] = task
    while True:
        event = await out.get()
        if event is None:
            break
        yield event


async def _turn_events(
    session: dict, message: str, mode: str = "ask", regenerate: bool = False
) -> AsyncIterator[str]:
    """Run one agent turn, emitting SSE events as tokens arrive."""
    from agent import StreamResult, extract_tool_calls, stream_and_log

    agent = _get_agent()
    thread_id = session["thread_id"]

    # Persist the customer's message BEFORE streaming: an interrupted stream
    # must not lose what they asked.
    if regenerate:
        # The question is already stored; only its old answer goes.
        repository.delete_last_reply(str(session["id"]))
    else:
        repository.add_message(str(session["id"]), "user", message)

    # Carry known facts across sessions so a returning customer is not re-asked
    # their departure city every time. Prepended as context rather than written
    # into the prompt file, because it is per-user and changes per turn.
    #
    # Injected only on the FIRST message of a session: LangGraph replays the
    # whole thread each turn, so repeating it would add the same block on every
    # request for no benefit.
    memory_prefix = ""
    try:
        existing = repository.list_messages(str(session["id"]))
        if len([m for m in existing if m["role"] == "user"]) <= 1:
            facts = repository.get_memory(str(session["user_id"]))
            pairs = ", ".join(f"{k}: {v}" for k, v in (facts or {}).items() if v not in (None, ""))
            if pairs:
                memory_prefix = (
                    f"[system] Known about this customer from earlier "
                    f"conversations — use it instead of asking again, but "
                    f"confirm before booking: {pairs}\n\n"
                )
    except Exception as e:  # noqa: BLE001 — memory is an aid, never the reply
        log.warning("memory_read_failed: %s", e)

    holder = StreamResult()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()

    def _produce() -> None:
        """stream_and_log is a sync generator, so it runs in a worker thread and
        hands events back through the queue rather than blocking the event loop
        (which would stall every other request on this worker).

        Tool activity is emitted AS IT HAPPENS, not at the end. A tour search
        makes ~40 supplier calls over 8-40 seconds, and the model sends no text
        until they finish — so without this the customer stares at an empty
        bubble for the whole search and assumes it has hung.
        """
        seen_calls: set[str] = set()
        seen_results: set[str] = set()

        def _drain_tool_events() -> None:
            """Turn newly-arrived tool messages into `tool` events.

            Keyed on the tool_call id so a call is announced once when it
            starts and patched once when it returns — the UI matches them by
            id and updates the chip in place.
            """
            for m in list(holder.messages):
                for tc in getattr(m, "tool_calls", None) or []:
                    if not isinstance(tc, dict):
                        continue
                    cid = str(tc.get("id") or "")
                    if cid and cid not in seen_calls:
                        seen_calls.add(cid)
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            ("tool", {"id": cid, "name": tc.get("name") or "tool",
                                      "input": tc.get("args") or {},
                                      "status": "running"}),
                        )
                if m.__class__.__name__ == "ToolMessage":
                    cid = str(getattr(m, "tool_call_id", "") or "")
                    if cid and cid not in seen_results:
                        seen_results.add(cid)
                        raw = getattr(m, "content", "")
                        try:
                            parsed = json.loads(raw) if isinstance(raw, str) else raw
                        except (ValueError, TypeError):
                            parsed = None
                        out = _step_summary(parsed)
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            ("tool", {"id": cid,
                                      "name": getattr(m, "name", "") or "tool",
                                      "output": out, "status": "done"}),
                        )

        # Steps reach the browser as they happen, not when text next arrives.
        holder.on_update = _drain_tool_events
        try:
            for token in stream_and_log(
                agent=agent,
                surface="streamlit",
                thread_id=thread_id,
                user_message=(
                    memory_prefix
                    + (_REGENERATE_CONTEXT if regenerate else "")
                    + _MODE_CONTEXT.get(mode, "")
                    + message
                ),
                result=holder,
            ):
                # Flush tool progress before the token, so the chips appear in
                # the order things actually happened.
                _drain_tool_events()
                loop.call_soon_threadsafe(queue.put_nowait, ("token", token))
            _drain_tool_events()
        except Exception as e:  # noqa: BLE001
            # Logged in full, shown to the customer in words: a framework
            # message ("Recursion limit of 12 reached ... visit docs.langchain")
            # is not something a traveller can act on.
            log.exception("stream_turn_failed: %s", e)
            loop.call_soon_threadsafe(queue.put_nowait, ("error", _TURN_FAILED))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    task = loop.run_in_executor(None, _produce)

    # A tool_call segment only appears once the model has DECIDED on a tool,
    # which can take a few seconds. Announce the turn immediately so the client
    # can show its "Thinking..." state from the first frame rather than waiting
    # for silence to elapse.
    yield _sse("start", {"session_id": str(session["id"])})

    while True:
        item = await queue.get()
        if item is None:
            break
        kind, payload = item
        if kind == "token":
            yield _sse("token", {"text": payload})
        elif kind == "tool":
            yield _sse("tool", payload)
        else:
            yield _sse("error", {"message": payload})
    await task

    from rules import normalize_reply

    reply = normalize_reply(holder.text or "")
    tool_calls = extract_tool_calls(holder.response) or []

    # Option cards (tours / hotels / flights) are built BEFORE the message is
    # saved so they can be stored with it. They used to be emitted after the
    # insert and never persisted, so every card -- and every tour image --
    # disappeared on reload while the reply text stayed.
    option_groups: list[dict[str, Any]] = []
    try:
        from agent import extract_all_search_options

        # One group per kind searched this turn (hotels AND their transfer),
        # latest search of each kind; the UI's `items` key maps to `options`.
        follow: list[str] | None = None
        for found in extract_all_search_options(holder.response):
            items = found.get("options") or []
            if not items:
                continue
            # The extractor returns a singular kind ("tour", "hotel") but the
            # UI selects its card component on the plural. Left unmapped, a
            # hotel search silently rendered with the tour card layout.
            kind = str(found.get("kind") or "tour")
            plural = {
                "tour": "tours", "hotel": "hotels", "flight": "flights",
                "restaurant": "restaurants", "transfer": "transfers",
                "package": "packages", "visa": "visa",
            }
            option_groups.append({"kind": plural.get(kind, kind), "items": items})
            follow = _FOLLOW_UPS.get(plural.get(kind, kind)) or follow
        if follow:
            # Next steps for the last thing shown, as tappable chips. Same
            # persisted channel as the cards, so they survive a reload.
            option_groups.append(
                {"kind": "suggestions", "items": [{"text": t} for t in follow]}
            )
    except Exception as e:  # noqa: BLE001 — cards are a bonus, never the reply
        log.warning("option_extraction_failed: %s", e)

    # The generated PDF rides the same persisted `options` channel as the
    # cards, so it survives a reload and needs no schema change. Without this
    # the only trace of a PDF was the URL the model happened to type into its
    # reply, which rendered as a bare link with nothing to click.
    try:
        from agent import _coerce_to_dict

        for call in tool_calls:
            # A ToolMessage's content is a JSON STRING, not a dict.
            out = _coerce_to_dict(call.get("output"))
            if not out or not out.get("itinerary_id"):
                continue
            # No URL here on purpose: the browser fetches the same-origin
            # path /itinerary/{id}.pdf, so this works on localhost and in
            # production without either knowing the other's hostname.
            option_groups.append({
                "kind": "itinerary_pdf",
                "items": [{
                    "itinerary_id": out.get("itinerary_id"),
                    "filename": out.get("filename") or "itinerary.pdf",
                }],
            })
            break
    except Exception as e:  # noqa: BLE001 — never fail a turn over the PDF
        log.warning("pdf_extraction_failed: %s", e)

    saved = repository.add_message(
        str(session["id"]), "assistant", reply, tool_calls, option_groups
    )

    # Learn durable facts from what the customer actually said, so the next
    # session starts warm. Only fields the agent genuinely reuses -- a bigger
    # net would store noise and waste the prefix budget on every first turn.
    try:
        learned = _extract_facts(message)
        if learned:
            repository.merge_memory(str(session["user_id"]), learned)
    except Exception as e:  # noqa: BLE001 -- never fail a turn over memory
        log.warning("memory_write_failed: %s", e)

    # Give the model's own title to a session still called "New chat", so the
    # sidebar is readable without the customer renaming anything.
    if session.get("title") in (None, "", "New chat"):
        repository.rename_session(
            str(session["id"]), str(session["user_id"]), message[:60]
        )

    # Emitted after the reply because the UI renders the cards below the
    # message. Same payload that was just persisted, so a reload shows exactly
    # what the live stream showed.
    for group in option_groups:
        yield _sse("options", group)

    # `content` is the reply as stored. It differs from what streamed when a
    # guard corrected it (invented fares, a dead PDF link), and the browser
    # adopts it so the live bubble matches what a reload would show.
    yield _sse("done", {"message_id": str(saved["id"]), "content": reply})


_ORIGIN_CITIES = (
    "mumbai", "delhi", "bangalore", "bengaluru", "hyderabad", "chennai",
    "kolkata", "pune", "ahmedabad", "kochi", "jaipur", "lucknow", "indore",
)


def _extract_facts(message: str) -> dict[str, Any]:
    """Durable facts worth carrying to the next session.

    Deliberately narrow and keyword-based rather than a second LLM call: an
    extra model round-trip per turn would add latency and cost to every
    message, and the fields that actually save the customer a question are few.

    Only records an origin city when the customer's own words name it, so we
    never persist a value the model inferred -- storing a guessed departure city
    would make the SAME mistake permanent across every future session.
    """
    text = (message or "").lower()
    facts: dict[str, Any] = {}

    for city in _ORIGIN_CITIES:
        if f"from {city}" in text:
            facts["origin_city"] = city.title()
            break

    import re as _re

    m = _re.search(r"(\d+)\s*(?:adults?|pax|people|persons?)", text)
    if m:
        facts["usual_party_size"] = int(m.group(1))

    for nat in ("indian", "india"):
        if nat in text:
            facts["nationality"] = "India"
            break

    return facts


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _session_out(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(s["id"]),
        "title": s["title"],
        "pinned": bool(s.get("pinned")),
        "created_at": s["created_at"].isoformat(),
        "updated_at": s["updated_at"].isoformat(),
    }


_agent_cache: dict[str, Any] = {}


def _get_agent() -> Any:
    """Build the agent once, with the Postgres checkpointer.

    Lazy rather than at startup: building it loads the LLM client and every
    tool schema, and doing that during lifespan risks Railway's healthcheck
    window on a cold boot.
    """
    if "agent" not in _agent_cache:
        from agent import build_postgres_checkpoint, build_react_agent

        _agent_cache["agent"] = build_react_agent(
            surface="streamlit", checkpoint_store=build_postgres_checkpoint()
        )
    return _agent_cache["agent"]
