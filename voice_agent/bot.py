"""Pipecat voice agent POC — Groq STT + Groq LLM + Sarvam TTS (bulbul:v3, Ishita).

Run from voice_agent/ with:
    uvicorn server:app --port 8765 --ws wsproto
Then open voice_agent/index.html in Chrome.
"""

from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMMessagesFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.services.groq import GroqLLMService, GroqSTTService
from pipecat.services.sarvam import SarvamTTSService, SarvamTTSSettings, SarvamTTSModel, SarvamTTSSpeakerV3
from pipecat.transports.network.fastapi_websocket import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

# ---------------------------------------------------------------------------
# Env
# ---------------------------------------------------------------------------
groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
sarvam_api_key = os.getenv("SARWAM_AI_API_KEY", "").strip()

if not groq_api_key:
    sys.exit("GROQ_API_KEY not set in .env")
if not sarvam_api_key:
    sys.exit("SARWAM_AI_API_KEY not set in .env")

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are Nikki — a friendly female travel consultant at Gujju Tours on a phone call.
Help the customer plan a Dubai trip. Speak natural Hinglish (Hindi words + English travel terms).
Keep replies SHORT — max 2 sentences. This is a phone call, not a chat.
Use feminine Hindi verbs: karungi, karegi, sakti hoon, samajh gayi.
Never use Devanagari script — Roman only.
When searching, say a short filler first: "Ek moment, dekh rahi hoon..." then give the answer.
Common fillers to vary: "Please wait...", "Haan sir, abhi dekhti hoon...", "Just a moment..."
"""

# ---------------------------------------------------------------------------
# Tool dispatch (5 travel tools)
# ---------------------------------------------------------------------------
TOOL_DISPATCH: dict[str, callable] = {}

try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from agent import build_react_agent, build_sqlite_checkpoint, invoke_and_log, extract_assistant_text
    from voice_service import run_planner_turn

    async def _execute_tool(name: str, args: dict) -> str:
        """Delegate to the full planner agent."""
        return run_planner_turn(json.dumps({"tool": name, "args": args}), "poc-session")

    TOOL_DISPATCH = {
        "search_flights": _execute_tool,
        "search_hotels": _execute_tool,
        "search_tours": _execute_tool,
        "search_transfers": _execute_tool,
        "search_visa": _execute_tool,
    }
except Exception:
    # If planner imports fail just run without tools (pure LLM mode)
    TOOL_DISPATCH = {}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Search {name.replace('search_', '')} options for Dubai travel",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query with dates and requirements"}
                },
                "required": ["query"],
            },
        },
    }
    for name in TOOL_DISPATCH
]


async def run_bot(websocket) -> None:
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=True,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.4)),
            vad_audio_passthrough=True,
            serializer=ProtobufFrameSerializer(),
        ),
    )

    stt = GroqSTTService(
        api_key=groq_api_key,
        model="whisper-large-v3-turbo",
        language="hi",  # Hinglish — Whisper handles code-switching well with hi
    )

    llm = GroqLLMService(
        api_key=groq_api_key,
        model="llama-3.1-8b-instant",
        temperature=0.4,
    )

    tts = SarvamTTSService(
        api_key=sarvam_api_key,
        settings=SarvamTTSSettings(
            model=SarvamTTSModel.BULBUL_V3,
            voice=SarvamTTSSpeakerV3.ISHITA,   # female, clear Indian voice
            language="hi-IN",
            pace=1.0,
            enable_preprocessing=True,
        ),
    )

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    context = OpenAILLMContext(messages, TOOLS if TOOL_DISPATCH else [])
    context_aggregator = llm.create_context_aggregator(context)

    # Register tool handlers
    async def _tool_handler(function_name, tool_call_id, arguments, llm_service, ctx, result_callback):
        handler = TOOL_DISPATCH.get(function_name)
        if handler:
            result = await handler(function_name, arguments)
        else:
            result = f"Tool {function_name} not available."
        await result_callback(result)

    for tool_name in TOOL_DISPATCH:
        llm.register_function(tool_name, _tool_handler)

    pipeline = Pipeline([
        transport.input(),
        stt,
        context_aggregator.user(),
        llm,
        tts,
        transport.output(),
        context_aggregator.assistant(),
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(allow_interruptions=True),
    )

    @transport.event_handler("on_client_connected")
    async def on_connected(transport, client):
        greeting = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "Greet me briefly in Hinglish as Nikki from Gujju Tours."}]
        await task.queue_frames([LLMMessagesFrame(greeting)])

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)
