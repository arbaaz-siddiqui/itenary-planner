"""voice_livekit.py — LiveKit-based voice agent for the Dubai Trip Planner.

Replaces the Vapi Custom-LLM webhook approach. LiveKit gives us a persistent
WebSocket per call, so filler/heartbeat phrases are spoken IMMEDIATELY as they
arrive — not buffered until the full response is ready (Vapi's SSE limitation).

Architecture:
  Phone call (SIP/WebRTC)
    → LiveKit Cloud room
      → This agent (Worker)
          ├─ STT: Deepgram nova-3 (streaming, per-word)
          ├─ LLM: TripPlannerLLM (wraps our run_planner_turn + heartbeats)
          └─ TTS: 11Labs turbo v2.5 (streams audio as text chunks arrive)

Run locally:
  python voice_livekit.py dev

Environment variables needed (.env):
  LIVEKIT_URL          wss://your-project.livekit.cloud
  LIVEKIT_API_KEY      your-api-key
  LIVEKIT_API_SECRET   your-api-secret
  DEEPGRAM_API_KEY     your-deepgram-key
  ELEVEN_API_KEY       your-11labs-key  (or ELEVENLABS_API_KEY)
  VAPI_11LABS_VOICE_ID  (optional, falls back to hardcoded voice)
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from livekit import agents
from livekit.agents import JobContext, WorkerOptions
from livekit.agents import llm
from livekit.agents.llm import ChatChunk, ChoiceDelta, ChatContext
from livekit.agents.types import APIConnectOptions
from livekit.plugins import deepgram, elevenlabs

# Our existing planner helpers — no changes needed to voice_service.py
from voice_service import (
    _build_smart_filler,
    _detect_intent,
    _length_instruction,
    _patch_last_turn,
    _pick_backchannel,
    run_planner_turn,
)
from agent import configure_logging, get_logger

configure_logging(prod=False)
log = get_logger("livekit")

# =============================================================================
# Heartbeat phrases — spoken every ~4s while planner searches tools
# =============================================================================
_HEARTBEATS = [
    "Thoda waqt dijiye, results check ho rahe hain...",
    "Haan, almost aa gaye...",
    "Bas ek second aur...",
    "Results aa rahe hain, please hold...",
]

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load_voice_prompt() -> str:
    p = _PROMPTS_DIR / "system_prompt_voice.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# =============================================================================
# Custom LLM — wraps run_planner_turn() + streams filler/heartbeats in real time
# =============================================================================

class TripPlannerLLM(llm.LLM):
    """Wraps our LangGraph planner as a LiveKit LLM.

    Flow per turn:
      1. Yield filler phrase immediately → TTS speaks it within ~300ms of caller stopping
      2. Run planner in a background thread (tool calls, API searches)
      3. While waiting, yield heartbeat phrases every 4s → TTS speaks each immediately
      4. Yield final answer → TTS speaks it

    Unlike Vapi's HTTP SSE (buffered until the full response arrives), LiveKit
    WebSocket streams each chunk to TTS the instant it's yielded.
    """

    def chat(
        self,
        *,
        chat_ctx: ChatContext,
        tools: list | None = None,
        conn_options: APIConnectOptions = APIConnectOptions(),
        **kwargs,
    ) -> "TripPlannerStream":
        # Extract last user turn and derive session_id from room name via metadata
        transcript = ""
        session_id = "livekit-unknown"

        for msg in reversed(chat_ctx.messages):
            if msg.role == "user":
                content = msg.content
                if isinstance(content, list):
                    content = " ".join(
                        p.text if hasattr(p, "text") else str(p) for p in content
                    )
                transcript = str(content or "").strip()
                break

        return TripPlannerStream(
            llm_instance=self,
            transcript=transcript,
            session_id=session_id,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
        )


class TripPlannerStream(llm.LLMStream):
    """Yields ChatChunks that LiveKit pipes straight to TTS."""

    def __init__(
        self,
        llm_instance: TripPlannerLLM,
        transcript: str,
        session_id: str,
        chat_ctx: ChatContext,
        tools: list,
        conn_options: APIConnectOptions,
    ):
        super().__init__(
            llm_instance,
            chat_ctx=chat_ctx,
            tools=tools,
            conn_options=conn_options,
        )
        self._transcript = transcript
        self._session_id = session_id

    def _chunk(self, chunk_id: str, text: str) -> ChatChunk:
        return ChatChunk(id=chunk_id, delta=ChoiceDelta(role="assistant", content=text))

    async def _run(self) -> None:
        transcript = self._transcript
        session_id = self._session_id
        t0 = time.perf_counter()

        if not transcript:
            self._event_ch.send_nowait(
                self._chunk("lk-greeting", "Haan ji! Main Nikki hoon. Kahan jaana hai?")
            )
            return

        intent = _detect_intent(transcript)

        # 1. Filler — spoken IMMEDIATELY (sub-100ms after caller stops talking)
        backchannel = _pick_backchannel(transcript, intent)
        filler = _build_smart_filler(transcript, intent)
        filler_text = backchannel + " " + filler

        t_filler = round(time.perf_counter() - t0, 3)
        log.info("livekit_filler_sent", session=session_id[:8], t_s=t_filler, text=filler_text)
        print(f"\n[FILLER] [{session_id[:8]}] @{t_filler}s -> {filler_text}", flush=True)

        self._event_ch.send_nowait(self._chunk("lk-filler", filler_text))

        # Tiny yield so TTS pipeline can start speaking filler before blocking thread starts
        await asyncio.sleep(0.05)

        # 2. Run planner in a background thread (blocking I/O — tool calls, API searches)
        loop = asyncio.get_event_loop()
        augmented = transcript + _length_instruction(intent)
        future = loop.run_in_executor(None, run_planner_turn, augmented, session_id)

        # 3. Heartbeat every 4s — spoken immediately via WebSocket while planner is thinking
        heartbeat_idx = 0
        heartbeat_log: list[dict] = []

        while not future.done():
            try:
                await asyncio.wait_for(asyncio.shield(future), timeout=4.0)
            except asyncio.TimeoutError:
                phrase = _HEARTBEATS[heartbeat_idx % len(_HEARTBEATS)]
                t_hb = round(time.perf_counter() - t0, 3)
                log.info("livekit_heartbeat", session=session_id[:8], t_s=t_hb, text=phrase)
                print(f"[HB] [{session_id[:8]}] @{t_hb}s -> {phrase}", flush=True)
                self._event_ch.send_nowait(self._chunk(f"lk-hb-{heartbeat_idx}", " " + phrase))
                heartbeat_log.append({"text": phrase, "t_s": t_hb})
                heartbeat_idx += 1

        try:
            final_reply = future.result()
        except Exception as exc:
            log.error("livekit_planner_error", error=str(exc))
            final_reply = "Sorry, kuch issue aa gaya. Ek baar aur try karein?"

        t_results = round(time.perf_counter() - t0, 3)
        log.info(
            "livekit_results_sent",
            session=session_id[:8],
            t_s=t_results,
            heartbeats=heartbeat_idx,
        )
        print(f"[RESULTS] [{session_id[:8]}] @{t_results}s -> {final_reply[:80]}", flush=True)

        prefix = "Results aa gaye. " if heartbeat_idx > 0 else ""
        self._event_ch.send_nowait(self._chunk("lk-result", " " + prefix + final_reply))

        # Write timing into trace for Streamlit UI
        _patch_last_turn(session_id, {
            "filler": filler_text,
            "t_filler_s": t_filler,
            "heartbeats": heartbeat_log,
            "t_results_s": t_results,
        })


# =============================================================================
# Agent — Nikki with opening greeting
# =============================================================================

class NikkiAgent(agents.Agent):

    def __init__(self, session_id: str) -> None:
        super().__init__(
            instructions=_load_voice_prompt(),
            llm=TripPlannerLLM(),
        )
        self._session_id = session_id

    async def on_enter(self) -> None:
        await self.session.say(
            "Haan ji, Gujju Tours mein aapka swagat hai! Main Nikki hoon. "
            "Kahan jaana hai aapko?"
        )


# =============================================================================
# Entrypoint — called for every incoming call/room
# =============================================================================

async def entrypoint(ctx: JobContext) -> None:
    session_id = ctx.room.name or ctx.job.id or "livekit-room"
    log.info("livekit_call_start", room=session_id)
    print(f"\n*** CALL START room={session_id}", flush=True)

    await ctx.connect()

    # STT — Deepgram nova-3, multilingual for Hinglish
    stt = deepgram.STT(
        model="nova-3",
        language="hi",           # Hindi base; nova-3 handles code-switching automatically
        smart_format=True,
        endpointing_ms=150,      # fast endpoint detection (was 300 in Vapi — halved)
        filler_words=False,      # don't forward "umm" / "aah" to LLM
    )

    # TTS — 11Labs turbo v2.5 (lowest latency in 11Labs lineup)
    voice_id = os.getenv("VAPI_11LABS_VOICE_ID", "tA6LGZpsqStKtSaGiXND")
    tts = elevenlabs.TTS(
        model="eleven_turbo_v2_5",
        voice_id=voice_id,
        voice_settings=elevenlabs.VoiceSettings(
            stability=0.45,
            similarity_boost=0.80,
            style=0.20,
            use_speaker_boost=True,
        ),
        streaming_latency=4,     # max 11Labs latency optimisation (0-4)
        auto_mode=False,         # stream chunks immediately, don't batch
    )

    # AgentSession wires STT → LLM → TTS over the LiveKit room WebSocket
    session = agents.AgentSession(
        stt=stt,
        tts=tts,
        allow_interruptions=True,
        min_endpointing_delay=0.15,   # 150ms — match Deepgram endpointing
        max_endpointing_delay=0.8,
    )

    await session.start(NikkiAgent(session_id=session_id), room=ctx.room)
    log.info("livekit_session_started", room=session_id)


# =============================================================================
# Worker — connects to LiveKit Cloud, handles incoming rooms/calls
# =============================================================================

if __name__ == "__main__":
    agents.cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            ws_url=os.getenv("LIVEKIT_URL", ""),
            api_key=os.getenv("LIVEKIT_API_KEY", ""),
            api_secret=os.getenv("LIVEKIT_API_SECRET", ""),
        )
    )
