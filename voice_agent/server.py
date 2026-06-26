"""server.py — FastAPI server for the Pipecat voice agent.

Endpoints:
    POST /start-call  — create a Daily.co room, return {room_url, token}
    POST /bot         — Daily webhook entry point; spawns bot.py in background
    GET  /health      — liveness check

Environment variables required:
    DAILY_API_KEY
    GROQ_API_KEY
    CARTESIA_API_KEY

Optional:
    CARTESIA_VOICE_ID   (default: a0e99841-438c-4a64-b679-ae501e7d6091)
    HOST                (default: 0.0.0.0)
    PORT                (default: 8080)

Run:
    uvicorn voice_agent.server:app --reload
  or
    python -m voice_agent.server
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

load_dotenv()

logger = logging.getLogger(__name__)

app = FastAPI(title="Nikki Voice Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DAILY_API_KEY = os.environ.get("DAILY_API_KEY", "")
DAILY_API_URL = "https://api.daily.co/v1"

# Room auto-deletes after 30 minutes
ROOM_EXPIRY_MINUTES = 30


# ---------------------------------------------------------------------------
# Daily.co helpers
# ---------------------------------------------------------------------------
async def _daily_request(method: str, path: str, body: dict | None = None) -> dict:
    """Make an authenticated request to the Daily.co REST API."""
    if not DAILY_API_KEY:
        raise HTTPException(status_code=500, detail="DAILY_API_KEY not configured.")

    headers = {
        "Authorization": f"Bearer {DAILY_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{DAILY_API_URL}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, url, headers=headers, json=body)
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"Daily.co API error {resp.status_code}: {resp.text}",
        )
    return resp.json()


async def create_daily_room() -> dict:
    """Create a Daily.co room that auto-deletes after ROOM_EXPIRY_MINUTES."""
    exp = datetime.now(timezone.utc) + timedelta(minutes=ROOM_EXPIRY_MINUTES)
    body = {
        "privacy": "private",
        "properties": {
            "exp": int(exp.timestamp()),
            "eject_at_room_exp": True,
            "enable_recording": "local",
            "max_participants": 2,  # 1 caller + 1 bot
        },
    }
    return await _daily_request("POST", "/rooms", body)


async def create_daily_token(room_name: str, is_owner: bool = False) -> str:
    """Create a meeting token for a room."""
    exp = datetime.now(timezone.utc) + timedelta(minutes=ROOM_EXPIRY_MINUTES)
    body = {
        "properties": {
            "room_name": room_name,
            "is_owner": is_owner,
            "exp": int(exp.timestamp()),
        }
    }
    data = await _daily_request("POST", "/meeting-tokens", body)
    return data["token"]


# ---------------------------------------------------------------------------
# Bot process launcher
# ---------------------------------------------------------------------------
def _spawn_bot(room_url: str, token: str) -> None:
    """
    Launch bot.py as a child process.

    We use subprocess instead of asyncio.create_task so the bot can crash or
    run to completion independently of the server process, and so that multiple
    concurrent calls each get their own isolated pipeline.
    """
    bot_script = os.path.join(os.path.dirname(__file__), "bot.py")
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    env = os.environ.copy()
    # Make sure the parent project is importable inside the child process
    pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{project_root}{os.pathsep}{pythonpath}" if pythonpath else project_root

    cmd = [sys.executable, bot_script, "--room-url", room_url, "--token", token]

    logger.info("Spawning bot process: %s", " ".join(cmd))
    # Detach: we don't wait for the child — it runs for the lifetime of the call
    subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        # On Windows, CREATE_NEW_PROCESS_GROUP prevents Ctrl-C propagating to child
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    """Liveness probe."""
    return {
        "status": "ok",
        "service": "nikki-voice-agent",
        "daily_key_configured": bool(DAILY_API_KEY),
        "groq_key_configured": bool(os.environ.get("GROQ_API_KEY")),
        "cartesia_key_configured": bool(os.environ.get("CARTESIA_API_KEY")),
    }


@app.post("/start-call")
async def start_call(background_tasks: BackgroundTasks):
    """
    Create a Daily.co room and a caller token.
    The bot is NOT started here — it starts when Daily calls /bot webhook,
    or you can call /start-call-and-bot for an integrated flow.

    Returns:
        {room_url, token, room_name, expires_in_minutes}
    """
    room = await create_daily_room()
    room_name: str = room["name"]
    room_url: str = room["url"]

    # Caller token (not owner — bot will join as owner)
    caller_token = await create_daily_token(room_name, is_owner=False)
    bot_token = await create_daily_token(room_name, is_owner=True)

    # Spawn the bot immediately in the background
    background_tasks.add_task(_spawn_bot, room_url, bot_token)

    return JSONResponse(
        {
            "room_url": room_url,
            "token": caller_token,
            "room_name": room_name,
            "expires_in_minutes": ROOM_EXPIRY_MINUTES,
        }
    )


@app.post("/bot")
async def bot_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Daily.co webhook entry point.
    Daily calls this URL when a participant joins a room that has this server
    registered as a bot endpoint.

    Expects JSON body: {"room_url": "...", "token": "..."}
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    room_url = data.get("room_url")
    token = data.get("token")

    if not room_url or not token:
        raise HTTPException(
            status_code=422,
            detail="Body must contain 'room_url' and 'token'.",
        )

    background_tasks.add_task(_spawn_bot, room_url, token)
    return JSONResponse({"status": "bot_starting"})


# ---------------------------------------------------------------------------
# Dev server entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run("voice_agent.server:app", host=host, port=port, reload=True)
