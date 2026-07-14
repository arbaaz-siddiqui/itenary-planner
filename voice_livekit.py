"""voice_livekit.py — LiveKit voice agent for the Dubai Trip Planner.

Replaces the Vapi Custom-LLM webhook. The Vapi approach streamed filler +
heartbeats + the final answer through ONE LLM/SSE response, so the TTS engine
synthesised them as a single utterance — the caller heard all the "please hold"
lines bunched together right before the results.

LiveKit fixes this: we take over the turn in `on_user_turn_completed` and speak
each phrase with its OWN `session.say()` call, so every filler/heartbeat plays in
REAL TIME as it is generated, then the final planner reply is spoken. The default
LLM pipeline is suppressed with `raise StopResponse()` — our LangGraph planner is
the brain, driven in a background thread.

Architecture:
  Phone call (SIP) → LiveKit Cloud room → this Worker
    ├─ STT: Sarvam saarika:v2.5 (Indian-language / Hinglish)
    ├─ VAD: Silero (turn detection)
    ├─ TTS: Sarvam bulbul:v3
    └─ Brain: run_planner_turn() (LangGraph) in a background thread

  (Sarvam is the same STT/TTS provider the Vapi agent uses — one API key.)

Run locally (needs a LiveKit dev key + a connected room):
  python voice_livekit.py dev

Env vars (see .env):
  LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET   — LiveKit Cloud project
  SARWAM_AI_API_KEY (or SARVAM_API_KEY)              — Sarvam STT + TTS
  SARVAM_SPEAKER                                      — voice (default "pooja")
"""

from __future__ import annotations

import asyncio
import os
import time

from dotenv import load_dotenv

load_dotenv()

from livekit import agents
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.agents.llm import ChatContext, ChatMessage, StopResponse
from livekit.plugins import sarvam, silero

# Shared planner helpers — identical brain as the Vapi path, so every chat fix
# (fast-first flights, loop prevention, date guard, diversify) applies here too.
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

# Quiet LiveKit's chatty INFO noise that clutters the console during calls:
#  - "ignoring byte/text stream with topic 'lk.agent.session'/'lk.transcription'"
#    (data streams we don't subscribe to — harmless)
#  - OpenTelemetry 429 QuotaStatusExceeded (LiveKit Cloud's free-tier tracing —
#    unrelated to call quality)
import logging as _logging

_logging.getLogger("root").setLevel(_logging.WARNING)
for _n in (
    "opentelemetry.exporter.otlp.proto.http._log_exporter",
    "opentelemetry.exporter.otlp.proto.http.trace_exporter",
):
    _logging.getLogger(_n).setLevel(_logging.CRITICAL)

# Heartbeat phrases — spoken while the planner searches tools. Kept SHORT and
# varied so the caller hears a live, attentive person, not a stuck recording.
# Grouped by "position" so early ones sound fresh and later ones reassure.
_HEARTBEATS_EARLY = [
    "Haan, dekh rahi hoon...",
    "Ek second...",
    "Check kar rahi hoon...",
    "Haan haan, mil raha hai...",
]
_HEARTBEATS_LATE = [
    "Bas aa hi gaya...",
    "Almost ho gaya...",
    "Thoda sa aur...",
    "Haan, bas ek pal...",
]

# Cadence: first nudge quickly (feels responsive), then relax so we don't
# chatter over the caller. seconds to wait before heartbeat #1, #2, #3, ...
_HEARTBEAT_GAPS = [2.5, 3.5, 4.0, 4.0, 5.0]


def _heartbeat_phrase(idx: int) -> str:
    """Pick a short, varied heartbeat. Early idx = 'still on it', later = 'almost'."""
    pool = _HEARTBEATS_EARLY if idx < 2 else _HEARTBEATS_LATE
    return pool[idx % len(pool)]

_GREETING = (
    "Haan ji, Gujju Tours mein aapka swagat hai! Main Nikki hoon. "
    "Kahan jaana hai aapko?"
)


def _load_voice_prompt() -> str:
    from agent import load_system_prompt

    return load_system_prompt(surface="voice")


# =============================================================================
# Agent — Nikki. Drives the planner + speaks each phrase via session.say() so
# fillers/heartbeats play in real time (not bundled into one TTS utterance).
# =============================================================================
class NikkiAgent(Agent):
    def __init__(self, session_id: str) -> None:
        # No llm= : we generate the reply ourselves in on_user_turn_completed
        # and suppress the default LLM with StopResponse.
        super().__init__(instructions=_load_voice_prompt())
        self._session_id = session_id

    async def on_enter(self) -> None:
        await self.session.say(_GREETING)

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        transcript = (new_message.text_content or "").strip()
        if not transcript:
            # Nothing to act on — let the default pipeline handle it (will no-op).
            return

        session_id = self._session_id
        t0 = time.perf_counter()
        intent = _detect_intent(transcript)

        # 1. Filler — spoken immediately, on its own, so it plays right away.
        backchannel = _pick_backchannel(transcript, intent)
        filler = _build_smart_filler(transcript, intent)
        filler_text = f"{backchannel} {filler}".strip()
        t_filler = round(time.perf_counter() - t0, 3)
        log.info("livekit_filler", session=session_id[:8], t_s=t_filler, text=filler_text)
        self.session.say(filler_text, add_to_chat_ctx=False)

        # 2. Run the blocking LangGraph planner in a background thread.
        loop = asyncio.get_event_loop()
        augmented = transcript + _length_instruction(intent)
        future = loop.run_in_executor(None, run_planner_turn, augmented, session_id)

        # 3. Heartbeats — each its own say() → real-time playback. Cadence starts
        #    quick then relaxes (see _HEARTBEAT_GAPS) so it feels attentive, not chatty.
        heartbeat_idx = 0
        heartbeat_log: list[dict] = []
        while not future.done():
            gap = _HEARTBEAT_GAPS[min(heartbeat_idx, len(_HEARTBEAT_GAPS) - 1)]
            try:
                await asyncio.wait_for(asyncio.shield(future), timeout=gap)
            except asyncio.TimeoutError:
                phrase = _heartbeat_phrase(heartbeat_idx)
                t_hb = round(time.perf_counter() - t0, 3)
                log.info("livekit_heartbeat", session=session_id[:8], t_s=t_hb, text=phrase)
                heartbeat_log.append({"text": phrase, "t_s": t_hb})
                self.session.say(phrase, add_to_chat_ctx=False)
                heartbeat_idx += 1

        # 4. Final planner reply.
        try:
            final_reply = future.result()
        except Exception as exc:  # noqa: BLE001
            log.error("livekit_planner_error", error=str(exc))
            final_reply = "Sorry, kuch issue aa gaya. Ek baar aur try karein?"

        t_results = round(time.perf_counter() - t0, 3)
        log.info("livekit_results", session=session_id[:8], t_s=t_results, heartbeats=heartbeat_idx)
        # No "results aa gaye" preamble — the answer itself lands faster and
        # crisper. Just a tiny lead-in if we made them wait, else straight to it.
        prefix = "Haan, " if heartbeat_idx >= 2 else ""
        await self.session.say(prefix + final_reply)

        _patch_last_turn(session_id, {
            "filler": filler_text,
            "t_filler_s": t_filler,
            "heartbeats": heartbeat_log,
            "t_results_s": t_results,
        })

        # We produced the whole reply ourselves — stop the default LLM from also
        # generating a response for this turn.
        raise StopResponse()


# =============================================================================
# Worker entrypoint — one per incoming call/room
# =============================================================================
async def entrypoint(ctx: JobContext) -> None:
    session_id = ctx.room.name or (ctx.job.id if ctx.job else None) or "livekit-room"
    log.info("livekit_call_start", room=session_id)

    await ctx.connect()

    # Sarvam for STT + TTS — same provider the Vapi agent uses. Indian-language
    # tuned (Hinglish), single API key. en-IN handles Hindi+English code-switching.
    sarvam_key = os.getenv("SARWAM_AI_API_KEY") or os.getenv("SARVAM_API_KEY")

    stt = sarvam.STT(
        language="en-IN",
        model="saarika:v2.5",
        api_key=sarvam_key,
    )

    tts = sarvam.TTS(
        target_language_code="en-IN",
        model="bulbul:v3",
        speaker=os.getenv("SARVAM_SPEAKER", "pooja"),  # valid bulbul:v3 female voice
        api_key=sarvam_key,
    )

    session = AgentSession(
        stt=stt,
        tts=tts,
        vad=silero.VAD.load(),
    )

    await session.start(agent=NikkiAgent(session_id=session_id), room=ctx.room)
    log.info("livekit_session_started", room=session_id)


# agent_name enables EXPLICIT dispatch: the SIP dispatch rule (see
# livekit_config.py) names this agent so inbound calls are routed to it. Must
# match RoomAgentDispatch.agent_name in the dispatch rule.
AGENT_NAME = "nikki-trip-planner"

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name=AGENT_NAME))
