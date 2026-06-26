"""FastAPI WebSocket server for the Pipecat voice agent POC."""

import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("server")

app = FastAPI()


@app.get("/")
async def root():
    return {"status": "voice-agent running", "ws": "ws://localhost:8765/ws"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    from bot import run_bot
    await websocket.accept()
    try:
        await run_bot(websocket)
    except WebSocketDisconnect:
        logger.info("Client disconnected.")
    except Exception as e:
        logger.error("Bot error: %s", e)
