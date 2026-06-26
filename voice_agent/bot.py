"""bot.py — Pipecat pipeline backend for the Dubai Trip Planner voice agent.

Stack:
  STT  : Groq Whisper (whisper-large-v3-turbo)
  LLM  : Groq Llama 3.3 70B with tool calling
  TTS  : Cartesia Sonic-2
  Transport: FastAPI WebSocket (works on Windows — daily-python is Linux/macOS only)

Usage (called from server.py via asyncio.create_task):
    await run_bot(websocket)

Environment variables:
    GROQ_API_KEY
    CARTESIA_API_KEY
    CARTESIA_VOICE_ID   (optional, defaults to a Hindi female voice)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

from dotenv import load_dotenv
from starlette.websockets import WebSocket

load_dotenv()

# ---------------------------------------------------------------------------
# Add parent project to path so we can import mcp_tools
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Pipecat imports
# ---------------------------------------------------------------------------
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import EndFrame, LLMMessagesFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.groq.stt import GroqSTTService
from pipecat.transports.network.fastapi_websocket import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Travel tool implementations (imported from parent project)
# ---------------------------------------------------------------------------
try:
    from mcp_tools.search_flights import _impl as search_flights_impl
    from mcp_tools.search_hotels import _impl as search_hotels_impl
    from mcp_tools.search_tours import _impl as search_tours_impl
    from mcp_tools.search_transfers import _impl as search_airport_transfer_dubai_impl
    from mcp_tools.get_visa_info import _impl as get_visa_info_impl
    _TOOLS_AVAILABLE = True
    logger.info("Travel tools imported from parent project.")
except ImportError as e:
    logger.warning("Travel tools not available: %s — using stubs.", e)
    _TOOLS_AVAILABLE = False

    def search_flights_impl(**kw):  # type: ignore[misc]
        return {"error": True, "message": "Travel tools not available in this environment."}

    def search_hotels_impl(**kw):  # type: ignore[misc]
        return {"error": True, "message": "Travel tools not available in this environment."}

    def search_tours_impl(**kw):  # type: ignore[misc]
        return {"error": True, "message": "Travel tools not available in this environment."}

    def search_airport_transfer_dubai_impl(**kw):  # type: ignore[misc]
        return {"error": True, "message": "Travel tools not available in this environment."}

    def get_visa_info_impl(**kw):  # type: ignore[misc]
        return {"error": True, "message": "Travel tools not available in this environment."}


# ---------------------------------------------------------------------------
# System prompt — Nikki, female Hinglish travel agent
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are Nikki — an experienced female travel consultant at Gujju Tours. You are on a phone call helping a customer plan a Dubai trip.

## SCRIPT — ROMAN ONLY, NEVER DEVANAGARI
ALWAYS write in Roman script (English letters). NEVER use Devanagari (Hindi script like haan, bilkul, shandar).
Even when customer speaks in Hindi, YOUR reply must be in Roman Hinglish — never Devanagari.
WRONG: "Bilkul in Devanagari script."
RIGHT: "Bilkul! Dubai bahut accha choice hai."

## GENDER — YOU ARE A WOMAN, ALWAYS FEMININE VERBS
WRONG (masculine — never use): karunga, karega, hoga, padega, sakta hoon, samajh gaya, batata hoon, nikal sakta hoon
RIGHT (feminine — always use): karungi, karegi, hogi, padegi, sakti hoon, samajh gayi, bata sakti hoon, nikal sakti hoon
When in doubt, use feminine. No exceptions.

## BREVITY — THE MOST IMPORTANT RULE FOR VOICE
THIS IS A PHONE CALL. Keep every reply to MAX 2 short sentences. That's it.

When presenting two options, give ONE number first, then ask if they want the other:
"Sharing mein six logon ka around five thousand rupees. Private ka price bhi bataaoon?"

## WAITING PHRASES — use one every time a tool runs, vary them
- "Ek moment, dekh rahi hoon..."
- "Please wait, check kar rahi hoon..."
- "Thodi si wait karein, results aa rahe hain..."
- "Haan sir, abhi dekhti hoon..."
- "Just a moment, system se data aa raha hai..."

## LANGUAGE — NATURAL HINGLISH
Hindi connectors + English travel words. Like an educated Indian travel agent on the phone.
SAHI: "Sir kahan se travel karenge?" / "Dates kya soch rahi hain aap?" / "Budget roughly kitna?" / "Four-star chahiye ya five-star?"
GALAT: Pure Hindi (literary/formal) / Pure English (call-center) / Devanagari script

## DATE ACCURACY
Jo dates customer ne is call mein boli hain wohi use karo. Pichli search ki dates forget karo.
Pehle confirm: "Toh [exact dates] — sahi samjhi?" Phir search karo.

## ONE QUESTION AT A TIME
Ek sawaal, ruko, answer suno, phir agla sawaal. Kabhi 2-3 sawaal ek saath nahi.

## FLOW
City → Dates → Kitne log → Budget → Search → 1-2 options briefly → Handoff

## NEVER
- Bullet points, numbered lists, asterisks, markdown
- INR (say "rupees"), long codes, URLs
- Invent prices or hotel names — only from tool results
- Go silent while searching — always say a waiting phrase first

## ERROR HANDLING
If a tool call fails, say: "Ek moment, system mein thodi dikkat aayi — phir se try karti hoon."

## HANDOFF
"Bahut badhiya sir! Main booking team ko details forward kar rahi hoon — woh fifteen-twenty minutes mein call karenge aapko."
"""

FIRST_MESSAGE = (
    "Haan ji, Gujju Tours mein aapka swagat hai! Main Nikki hoon. "
    "How can I help you?"
)

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI-style function calling schema for Groq)
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_flights",
            "description": "Search for flights between an Indian city and Dubai.",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin_city": {"type": "string", "description": "Indian source city, e.g. 'Delhi'."},
                    "destination_city": {"type": "string", "description": "Destination city, e.g. 'Dubai'."},
                    "departure_date": {"type": "string", "description": "ISO date yyyy-mm-dd."},
                    "return_date": {"type": "string", "description": "ISO return date; omit for one-way."},
                    "adults": {"type": "integer"},
                    "children": {"type": "integer"},
                    "cabin": {"type": "string", "enum": ["Y", "S", "C", "F"]},
                },
                "required": ["origin_city", "destination_city", "departure_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_hotels",
            "description": "Search for hotel availability in Dubai.",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination_city": {"type": "string"},
                    "check_in": {"type": "string", "description": "ISO date yyyy-mm-dd."},
                    "check_out": {"type": "string", "description": "ISO date yyyy-mm-dd."},
                    "adults": {"type": "integer"},
                    "children": {"type": "integer"},
                    "min_stars": {"type": "number"},
                    "max_stars": {"type": "number"},
                    "amenities": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["destination_city", "check_in", "check_out"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_tours",
            "description": "Search for Dubai tours and activities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination_city": {"type": "string"},
                    "travel_date": {"type": "string", "description": "ISO date yyyy-mm-dd."},
                    "tour_category_id": {"type": "integer"},
                },
                "required": ["destination_city", "travel_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_airport_transfer_dubai",
            "description": "Search for Dubai airport transfers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hotel_lat": {"type": "number"},
                    "hotel_lng": {"type": "number"},
                    "arrival_date": {"type": "string", "description": "ISO date yyyy-mm-dd."},
                    "arrival_time": {"type": "string"},
                    "return_date": {"type": "string"},
                    "adults": {"type": "integer"},
                },
                "required": ["hotel_lat", "hotel_lng", "arrival_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_visa_info",
            "description": "Get Dubai visa requirements for Indian nationals.",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination_country": {"type": "string"},
                    "nationality_country": {"type": "string"},
                    "travel_date": {"type": "string"},
                    "adults": {"type": "integer"},
                    "children": {"type": "integer"},
                },
                "required": ["destination_country", "nationality_country", "travel_date"],
            },
        },
    },
]

TOOL_DISPATCH: dict[str, Any] = {
    "search_flights": search_flights_impl,
    "search_hotels": search_hotels_impl,
    "search_tours": search_tours_impl,
    "search_airport_transfer_dubai": search_airport_transfer_dubai_impl,
    "get_visa_info": get_visa_info_impl,
}


# ---------------------------------------------------------------------------
# Tool call executor — runs blocking I/O in a thread pool
# ---------------------------------------------------------------------------
async def _execute_tool(tool_name: str, tool_args: dict[str, Any]) -> str:
    fn = TOOL_DISPATCH.get(tool_name)
    if fn is None:
        return json.dumps({"error": True, "message": f"Unknown tool: {tool_name}"})
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: fn(**tool_args))
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as exc:
        logger.exception("Tool %s raised an exception", tool_name)
        return json.dumps({"error": True, "message": "System error.", "detail": str(exc)})


# ---------------------------------------------------------------------------
# Pipeline builder — one call = one WebSocket connection
# ---------------------------------------------------------------------------
async def run_bot(websocket: WebSocket) -> None:
    """Build and run the Pipecat pipeline for a single WebSocket session."""

    groq_api_key = os.environ["GROQ_API_KEY"]
    cartesia_api_key = os.environ["CARTESIA_API_KEY"]
    cartesia_voice_id = os.environ.get("CARTESIA_VOICE_ID", "a0e99841-438c-4a64-b679-ae501e7d6091")

    # ── Transport ────────────────────────────────────────────────────────────
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=True,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
            vad_audio_passthrough=True,
            serializer=ProtobufFrameSerializer(),
        ),
    )

    # ── STT ──────────────────────────────────────────────────────────────────
    stt = GroqSTTService(
        api_key=groq_api_key,
        model="whisper-large-v3-turbo",
        language="en",
    )

    # ── LLM ──────────────────────────────────────────────────────────────────
    llm = GroqLLMService(
        api_key=groq_api_key,
        model="llama-3.3-70b-versatile",
        temperature=0.3,
    )

    # ── TTS ──────────────────────────────────────────────────────────────────
    tts = CartesiaTTSService(
        api_key=cartesia_api_key,
        voice_id=cartesia_voice_id,
        model="sonic-2",
        language="en",
    )

    # ── LLM context ──────────────────────────────────────────────────────────
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": FIRST_MESSAGE},
    ]
    context = OpenAILLMContext(messages, TOOLS)
    context_aggregator = llm.create_context_aggregator(context)

    # ── Pipeline ─────────────────────────────────────────────────────────────
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(allow_interruptions=True),
    )

    # ── Greet caller when WebSocket connects ──────────────────────────────────
    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        await task.queue_frames([LLMMessagesFrame(messages)])

    # ── Handle tool calls from Groq LLM ──────────────────────────────────────
    @llm.event_handler("on_tool_call_started")
    async def on_tool_call_started(llm_service, tool_call):
        tool_name = tool_call.get("function", {}).get("name", "")
        raw_args = tool_call.get("function", {}).get("arguments", "{}")
        tool_call_id = tool_call.get("id", "")
        logger.info("Tool call: %s  args=%s", tool_name, raw_args)
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            args = {}
        result_json = await _execute_tool(tool_name, args)
        context.add_message({"role": "tool", "tool_call_id": tool_call_id, "content": result_json})

    runner = PipelineRunner()
    await runner.run(task)
