"""server.py — FastAPI server for the Pipecat voice agent (WebSocket transport).

Endpoints:
    GET  /          — serves index.html
    WS   /ws        — WebSocket endpoint; each connection is one voice call
    GET  /health    — liveness check

Environment variables required:
    GROQ_API_KEY
    CARTESIA_API_KEY

Optional:
    CARTESIA_VOICE_ID   (default: a0e99841-438c-4a64-b679-ae501e7d6091)
    HOST                (default: 0.0.0.0)
    PORT                (default: 8080)

Run:
    cd voice_agent
    .\\venv_poc\\Scripts\\activate
    uvicorn server:app --reload --port 8080
  or
    python server.py
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

load_dotenv()

logger = logging.getLogger(__name__)

app = FastAPI(title="Nikki Voice Agent (Pipecat POC)", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "nikki-pipecat-poc",
        "groq_key_configured": bool(os.environ.get("GROQ_API_KEY")),
        "cartesia_key_configured": bool(os.environ.get("CARTESIA_API_KEY")),
    }


@app.get("/")
async def index():
    """Serve the browser UI."""
    return FileResponse(os.path.join(_THIS_DIR, "index.html"))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Each browser connection starts a fresh Pipecat pipeline.
    Audio streams in as raw PCM via protobuf frames; TTS audio streams back the same way.
    """
    from bot import run_bot  # imported here so server stays importable even if deps missing

    logger.info("New WebSocket connection from %s", websocket.client)
    try:
        await run_bot(websocket)
    except WebSocketDisconnect:
        logger.info("Client disconnected.")
    except Exception as exc:
        logger.exception("Pipeline error: %s", exc)


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
    uvicorn.run("server:app", host=host, port=port, reload=True)
