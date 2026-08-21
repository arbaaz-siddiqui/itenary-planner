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
import threading
import time
import uuid
from typing import Any

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
    _patch_last_turn,
    _pick_backchannel,
    run_planner_turn,
)
from agent import configure_logging, get_logger
from booking_api.http_client import latest_http_seq

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
# One pool, walked in order: the phrases already escalate on their own
# ("dekh rahi hu" -> "thoda wait" -> "sorry for delay" -> "bs ho gaya"), so a
# separate late pool only duplicated the same reassurance in different words.
_HEARTBEATS = [
    "dekh rahi hu results",
    "Please thoda sa wait",
    "Sorry for the delay, thoda sa wait aur kriyega",
    "bs hi ho gaya, almost done",
]

# Cadence: first nudge quickly (feels responsive), then relax so we don't
# chatter over the caller. seconds to wait before heartbeat #1, #2, #3, ...
_HEARTBEAT_GAPS = [2.5, 3.5, 4.5, 5.0, 5.5]

_NO_TOOL_GRACE_S = 6.0  # if somehow no HTTP yet but we're this slow, reassure anyway


def _heartbeat_phrase(idx: int) -> str:
    """Pick the next heartbeat, in order, wrapping on a very long wait.

    Wrapping (rather than sticking on the last phrase) keeps a slow turn from
    repeating one line over and over, which is what makes it sound like a stuck
    recording.
    """
    return _HEARTBEATS[idx % len(_HEARTBEATS)]

_GREETING = (
    "Hi I am Nikki, from Gujju tours, How may I help you?"
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
        # Pass the RAW transcript: run_planner_turn re-detects intent and appends
        # the length hint itself. Appending it here too produced a doubled
        # "[DETAIL MODE: ...] [DETAIL MODE: ...]" on every detail turn, which
        # pushed the model past the 2-sentence rule and then got hard-truncated
        # mid-thought by format_for_voice. (The Vapi path already passes raw.)
        future = loop.run_in_executor(None, run_planner_turn, transcript, session_id)

        # 3. Heartbeats — each its own say() → real-time playback. Cadence starts
        #    quick then relaxes (see _HEARTBEAT_GAPS) so it feels attentive, not chatty.
        heartbeat_idx = 0
        heartbeat_log: list[dict] = []
        # Keep the handles: say() is fire-and-forget and queues into a FIFO, so a
        # heartbeat that started just before the planner returned would otherwise
        # still be playing when we speak the answer — the caller hears "thoda sa
        # wait kriyega" AFTER the result was already in hand. We interrupt any
        # still-playing heartbeat before delivering the reply.
        pending_says: list[Any] = []
        http_at_start = latest_http_seq()
        while not future.done():
            gap = _HEARTBEAT_GAPS[min(heartbeat_idx, len(_HEARTBEAT_GAPS) - 1)]
            try:
                await asyncio.wait_for(asyncio.shield(future), timeout=gap)
            except asyncio.TimeoutError:
                if future.done():
                    break  # answer landed while we were waiting — don't start filler
                # Only reassure if we're genuinely waiting on a supplier search.
                # A pure conversational turn ("kitne log hain?") makes no HTTP
                # request, so it stays silent instead of apologising for a delay
                # that isn't a search. The grace window still covers a turn that
                # is slow for some other reason, so we never go quiet for long.
                searching = latest_http_seq() > http_at_start
                if not searching and (time.perf_counter() - t0) < _NO_TOOL_GRACE_S:
                    continue
                phrase = _heartbeat_phrase(heartbeat_idx)
                t_hb = round(time.perf_counter() - t0, 3)
                log.info(
                    "livekit_heartbeat",
                    session=session_id[:8],
                    t_s=t_hb,
                    text=phrase,
                    searching=searching,
                )
                heartbeat_log.append({"text": phrase, "t_s": t_hb})
                pending_says.append(self.session.say(phrase, add_to_chat_ctx=False))
                heartbeat_idx += 1

        # Cut off any heartbeat still speaking so the answer isn't queued behind it.
        for handle in pending_says:
            try:
                handle.interrupt()
            except Exception:  # noqa: BLE001 - already finished, or no such handle
                pass

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
    # NEVER fall back to a constant: session_id becomes the LangGraph checkpoint
    # thread id, so two calls sharing it would share one conversation — the exact
    # trip-state leak the per-call room name fixed for outbound calls.
    session_id = (
        ctx.room.name
        or (ctx.job.id if ctx.job else None)
        or f"livekit-{uuid.uuid4().hex}"
    )
    log.info("livekit_call_start", room=session_id)

    await ctx.connect()

    # Sarvam for STT + TTS — same provider the Vapi agent uses. Indian-language
    # tuned (Hinglish), single API key. en-IN handles Hindi+English code-switching.
    sarvam_key = os.getenv("SARWAM_AI_API_KEY") or os.getenv("SARVAM_API_KEY")

    # saaras:v3, not v4. v4 IS a real Sarvam model, but this plugin version has
    # no MODEL_CONFIGS entry for it, so it falls into the unknown-model path:
    # `mode=` is silently DROPPED and every VAD-tuning knob is ignored. Those
    # knobs are exactly what stop the agent being cut off mid-sentence, so on v4
    # the caller hears Nikki break up. v3 is plugin-aware.
    stt = sarvam.STT(
        language="en-IN",
        mode="transcribe",
        model=os.getenv("SARVAM_STT_MODEL", "saaras:v3"),
        api_key=sarvam_key,
    )

    tts = sarvam.TTS(
        target_language_code="en-IN",
        model="bulbul:v3",
        speaker=os.getenv("SARVAM_SPEAKER", "pooja"),  # from env get ritu if not set use pooja
        pace=1,
        # 22050 is the plugin default. 16000 is legal but a lower-fidelity
        # stream that resamples for the SIP leg, which made speech sound broken.
        speech_sample_rate=int(os.getenv("SARVAM_TTS_SAMPLE_RATE", "22050")),
        api_key=sarvam_key,
    )

    session = AgentSession(
        # Why this exists: the caller reported Nikki "breaking" — audio cutting
        # out mid-sentence. Defaults are min_duration=0.5s and min_words=0, so
        # ANY half-second of sound aborts her turn: line noise, a cough, or our
        # own filler/heartbeat bleeding back on a speakerphone. Requiring real
        # words plus a longer burst means only a genuine interruption stops her,
        # and resume_false_interruption picks the sentence back up if we were
        # wrong.
        turn_handling={
            "endpointing": {"min_delay": 0.6},
            "interruption": {
                "enabled": True,
                "min_duration": 0.9,
                "min_words": 2,
                "resume_false_interruption": True,
                "false_interruption_timeout": 2.0,
            },
        },
        stt=stt,
        tts=tts,
        # Reuse the VAD loaded in prewarm(); fall back if this process somehow
        # started without it (e.g. `python voice_livekit.py dev` in some modes).
        vad=(ctx.proc.userdata.get("vad") if ctx.proc else None) or silero.VAD.load(),
    )

    await session.start(agent=NikkiAgent(session_id=session_id), room=ctx.room)
    log.info("livekit_session_started", room=session_id)


def prewarm(proc: agents.JobProcess) -> None:
    """Pay every one-time cost BEFORE a caller is on the line.

    Measured on this machine, the first turn of a fresh process cost ~9.3s to
    answer "dubai jana hai" — a question that calls no tool at all. The same
    question later took ~1.0-1.8s. The gap was pure warm-up, not per-turn work:

        build the LangGraph agent + tool registry   1.61s
        first LLM call (TLS + connection setup)     ~1.0s
        misc first-call allocation                  remainder

    None of it recurs, so doing it here means the caller's FIRST question gets
    steady-state latency instead of wearing the whole warm-up.

    Note it is NOT the prompt size: with output held to 5 words, a 5,545-token
    system prompt cost only ~0.5s more than a tiny one. Shrinking the prompt was
    the obvious-looking fix and would have bought almost nothing.

    Silero VAD is loaded here too (the usual LiveKit prewarm use) and handed to
    the session via proc.userdata, so we don't pay ~0.3s per call for it either.
    """
    t0 = time.perf_counter()
    # VAD only — this is fast (~0.6s) and LiveKit kills prewarm at 10s
    # (WorkerOptions.initialize_process_timeout). Building the agent takes ~20s
    # in a cold process and blew that budget, so the worker could never accept a
    # call: "error initializing process / TimeoutError".
    proc.userdata["vad"] = silero.VAD.load()
    log.info("livekit_prewarm_done", t_s=round(time.perf_counter() - t0, 2))

    # The expensive warm-up (LangGraph agent + tool tree + LLM TLS handshake,
    # ~22s cold) runs in a BACKGROUND thread so it does not count against the
    # prewarm deadline. It races the first caller rather than blocking startup:
    # by the time someone speaks, it is usually done. If a call lands first, the
    # first turn simply pays the cost as it did before — never a failure.
    def _warm_planner() -> None:
        t = time.perf_counter()
        try:
            from voice_service import get_voice_agent

            get_voice_agent()
            from llm import build_llm

            build_llm().invoke("hi")
            log.info("livekit_planner_warm", t_s=round(time.perf_counter() - t, 2))
        except Exception as e:  # noqa: BLE001 — a slow cold start is not fatal
            log.warning("livekit_planner_warm_failed", error=str(e))

    threading.Thread(target=_warm_planner, name="planner-warmup", daemon=True).start()


# agent_name enables EXPLICIT dispatch: the SIP dispatch rule (see
# livekit_config.py) names this agent so inbound calls are routed to it. Must
# match RoomAgentDispatch.agent_name in the dispatch rule.
AGENT_NAME = "nikki-trip-planner"

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name=AGENT_NAME,
        )
    )
