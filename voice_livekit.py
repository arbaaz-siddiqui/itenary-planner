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
  SARVAM_SPEAKER                                      — voice (default "anushka")
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

# Heartbeat phrases — spoken every ~4s while the planner searches tools.
_HEARTBEATS = [
    "Thoda waqt dijiye, results check ho rahe hain...",
    "Haan, almost aa gaye...",
    "Bas ek second aur...",
    "Results aa rahe hain, please hold...",
]

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

        # 3. Heartbeats every 4s — each its own say() → real-time playback.
        heartbeat_idx = 0
        heartbeat_log: list[dict] = []
        while not future.done():
            try:
                await asyncio.wait_for(asyncio.shield(future), timeout=4.0)
            except asyncio.TimeoutError:
                phrase = _HEARTBEATS[heartbeat_idx % len(_HEARTBEATS)]
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
        prefix = "Results aa gaye. " if heartbeat_idx > 0 else ""
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
        speaker=os.getenv("SARVAM_SPEAKER", "anushka"),
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
