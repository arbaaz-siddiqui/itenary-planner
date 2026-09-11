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
from typing import Any

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


class SessionCreate(BaseModel):
    title: str | None = None


class SessionRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1)


# ----------------------------------------------------------------- health
@app.get("/health")
async def health() -> dict[str, str]:
    """Kept trivial and DB-free so Railway's healthcheck passes even when
    Postgres is briefly unavailable — a failing probe would roll the deploy
    back and take the whole service down with it."""
    return {"status": "ok"}


@app.get("/health/db")
async def health_db() -> dict[str, Any]:
    """Separate, so a DB problem is diagnosable without failing the deploy."""
    try:
        return {"status": "ok", **db.healthcheck()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(e)) from e


# ------------------------------------------------------------------- auth
@app.post("/auth/register", status_code=status.HTTP_201_CREATED)
async def register(body: Credentials, response: Response) -> dict[str, Any]:
    if repository.find_user_by_email(body.email):
        raise HTTPException(status_code=409, detail="That email is already registered")
    user = repository.create_user(body.email, auth.hash_password(body.password))
    auth.set_session_cookie(response, str(user["id"]))
    return {"user": {"id": str(user["id"]), "email": user["email"]}}


@app.post("/auth/login")
async def login(body: Credentials, response: Response) -> dict[str, Any]:
    user = repository.find_user_by_email(body.email)
    # One message for both "no such account" and "wrong password", so the
    # response cannot be used to discover which emails are registered.
    if not user or not auth.verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    auth.set_session_cookie(response, str(user["id"]))
    return {"user": {"id": str(user["id"]), "email": user["email"]}}


@app.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    auth.clear_session_cookie(response)


@app.get("/auth/me")
async def me(user: dict = Depends(auth.current_user)) -> dict[str, Any]:
    return {"user": {"id": str(user["id"]), "email": user["email"]}}


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
    session_id: str, body: SessionRename, user: dict = Depends(auth.current_user)
) -> dict[str, Any]:
    s = repository.rename_session(session_id, str(user["id"]), body.title)
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
        }
        for m in repository.list_messages(session_id)
    ]


@app.get("/chat/messages/{message_id}/tool-calls")
async def get_tool_calls(
    message_id: str, _user: dict = Depends(auth.current_user)
) -> list[dict[str, Any]]:
    """Debug payload for one message, fetched only when the panel opens —
    these reach 200KB+ for a tour search."""
    return repository.get_message_tool_calls(message_id)


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
    return StreamingResponse(
        _stream_turn(session, body.message),
        media_type="text/event-stream",
        # Nginx and some proxies buffer SSE into one chunk at the end, which
        # defeats streaming entirely.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_turn(session: dict, message: str) -> AsyncIterator[str]:
    """Run one agent turn, emitting SSE events as tokens arrive."""
    from agent import StreamResult, extract_tool_calls, stream_and_log

    agent = _get_agent()
    thread_id = session["thread_id"]

    # Persist the customer's message BEFORE streaming: an interrupted stream
    # must not lose what they asked.
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
            if facts:
                pairs = ", ".join(f"{k}: {v}" for k, v in facts.items())
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
                            out = json.loads(raw) if isinstance(raw, str) else raw
                        except (ValueError, TypeError):
                            out = {"raw": str(raw)[:2000]}
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            ("tool", {"id": cid,
                                      "name": getattr(m, "name", "") or "tool",
                                      "output": out, "status": "done"}),
                        )

        try:
            for token in stream_and_log(
                agent=agent,
                surface="streamlit",
                thread_id=thread_id,
                user_message=memory_prefix + message,
                result=holder,
            ):
                # Flush tool progress before the token, so the chips appear in
                # the order things actually happened.
                _drain_tool_events()
                loop.call_soon_threadsafe(queue.put_nowait, ("token", token))
            _drain_tool_events()
        except Exception as e:  # noqa: BLE001
            loop.call_soon_threadsafe(queue.put_nowait, ("error", str(e)))
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
        from agent import extract_search_options

        # Returns a single {kind, options, raw} for the most recent search, not
        # a dict of groups — the UI's `items` key maps to `options` here.
        found = extract_search_options(holder.response) or {}
        items = found.get("options") or []
        if items:
            # The extractor returns a singular kind ("tour", "hotel") but the
            # UI selects its card component on the plural. Left unmapped, a
            # hotel search silently rendered with the tour card layout.
            kind = str(found.get("kind") or "tour")
            plural = {"tour": "tours", "hotel": "hotels", "flight": "flights"}
            option_groups = [{"kind": plural.get(kind, kind), "items": items}]
    except Exception as e:  # noqa: BLE001 — cards are a bonus, never the reply
        log.warning("option_extraction_failed: %s", e)

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

    yield _sse("done", {"message_id": str(saved["id"])})


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
