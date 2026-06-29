"""vapi_config.py — Production Vapi assistant definition for the Dubai Trip Planner.

This file is the single source of truth for how the voice agent is configured.
Run `python vapi_config.py` to push the config to Vapi (create or update).

Environment variables needed:
  VAPI_API_KEY          — from Vapi dashboard → API Keys
  VAPI_ASSISTANT_ID     — set after first create; used for updates
  VOICE_SERVICE_URL     — public URL of your deployed voice_service.py
                          e.g. https://voice-planner.up.railway.app

Design goals:
  - Human-like conversation: backchannels, fillers, natural pacing
  - Low latency: Deepgram nova-3, 11Labs turbo, minimal delay settings
  - Indian English optimised: en-IN language, travel keywords boosted
  - Fully configurable: change STT/TTS/model by editing PROVIDER constants below
"""

from __future__ import annotations

import json
import os
import urllib.request

from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# Read env — all provider choices are env-configurable, no code change needed
# =============================================================================
VAPI_API_KEY       = os.getenv("VAPI_API_KEY", "")
VAPI_ASSISTANT_ID  = os.getenv("VAPI_ASSISTANT_ID", "")
VOICE_SERVICE_URL  = os.getenv("VOICE_SERVICE_URL", "https://your-voice-service.up.railway.app").rstrip("/")

# STT options:  "deepgram" | "talkscriber" | "gladia" | "assembly-ai"
STT_PROVIDER = os.getenv("VAPI_STT_PROVIDER", "deepgram")

# TTS options:  "11labs" | "azure" | "playht" | "lmnt" | "openai" | "neets"
TTS_PROVIDER = os.getenv("VAPI_TTS_PROVIDER", "11labs")

# 11Labs voice ID (only used when TTS_PROVIDER=11labs)
ELEVENLABS_VOICE_ID = os.getenv("VAPI_11LABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

# If using Vapi's own hosted LLM instead of your Custom LLM server,
# set CUSTOM_LLM=false in .env and set VAPI_LLM_PROVIDER / VAPI_LLM_MODEL.
CUSTOM_LLM  = os.getenv("VAPI_CUSTOM_LLM", "true").lower() != "false"
LLM_PROVIDER = os.getenv("VAPI_LLM_PROVIDER", "anthropic")
LLM_MODEL    = os.getenv("VAPI_LLM_MODEL", "claude-sonnet-4-6")


# =============================================================================
# STT config — Deepgram nova-3, tuned for Indian English + travel vocabulary
# =============================================================================
def _stt_config() -> dict:
    if STT_PROVIDER == "deepgram":
        return {
            "provider": "deepgram",
            "model": "nova-3",
            "language": "multi",  # multilingual — handles Hindi + English mixed (Hinglish)
            "smartFormat": True,
            "endpointing": 150,  # ms before STT finalises — lower = faster response
        }
    if STT_PROVIDER == "gladia":
        return {
            "provider": "gladia",
            "language": "en",
        }
    if STT_PROVIDER == "assembly-ai":
        return {
            "provider": "assembly-ai",
            "language": "en",
        }
    # talkscriber fallback
    return {"provider": "talkscriber", "language": "en"}


# =============================================================================
# TTS config — 11Labs, warm professional Indian-adjacent voice
# =============================================================================
def _tts_config() -> dict:
    if TTS_PROVIDER == "11labs":
        return {
            "provider": "11labs",
            # "Rachel" — clear, warm, professional, neutral accent.
            # Good for travel/sales context without sounding robotic.
            # Other good options:
            #   "21m00Tcm4TlvDq8ikWAM" = Rachel
            #   "AZnzlk1XvdvUeBnXmlld" = Domi (more energetic)
            #   "EXAVITQu4vr4xnSDxMaL" = Bella (softer)
            #   "pNInz6obpgDQGcFmaJgB" = Adam (male, authoritative)
            "voiceId": ELEVENLABS_VOICE_ID,
            "model": "eleven_turbo_v2_5",  # lowest latency 11Labs model
            "stability": 0.45,             # slight variation — sounds more human
            "similarityBoost": 0.80,
            "style": 0.20,                 # subtle expressiveness
            "useSpeakerBoost": True,
            "optimizeStreamingLatency": 4, # max latency optimisation (0-4)
        }
    if TTS_PROVIDER == "azure":
        return {
            "provider": "azure",
            "voiceId": "en-IN-NeerjaNeural",  # Indian English female voice
        }
    if TTS_PROVIDER == "playht":
        return {
            "provider": "playht",
            "voiceId": "jennifer",
            "speed": 1.0,
            "quality": "premium",
        }
    if TTS_PROVIDER == "openai":
        return {
            "provider": "openai",
            "voiceId": "nova",  # warm, clear
        }
    # lmnt / neets fallback
    return {"provider": TTS_PROVIDER}


# =============================================================================
# LLM / model config
# =============================================================================
def _model_config() -> dict:
    if CUSTOM_LLM:
        return {
            "provider": "custom-llm",
            "url": f"{VOICE_SERVICE_URL}/api/webhook",
            "model": "trip-planner",
            # Pass the call object so voice_service can extract call.id as session_id
            "urlRequestMetadataEnabled": True,
        }
    return {
        "provider": LLM_PROVIDER,
        "model": LLM_MODEL,
        "temperature": 0.3,
        "systemPrompt": _voice_system_prompt(),
    }


# =============================================================================
# Voice-optimised system prompt
# (stripped of all Streamlit card signals, PDF, calendar, WhatsApp sections)
# =============================================================================
def _voice_system_prompt() -> str:
    return """You are Nikki — an experienced female travel consultant at Gujju Tours. You are on a phone call helping a customer plan a Dubai trip.

## SCRIPT — ROMAN ONLY, NEVER DEVANAGARI
ALWAYS write in Roman script (English letters). NEVER use Devanagari (Hindi script like हां, बिल्कुल, शानदार).
Even when customer speaks in Hindi, YOUR reply must be in Roman Hinglish — never Devanagari.
WRONG: "बिल्कुल! Dubai एक शानदार destination है।"
RIGHT: "Bilkul! Dubai bahut accha choice hai."

## GENDER — YOU ARE A WOMAN, ALWAYS FEMININE VERBS
WRONG (masculine — never use): karunga, karega, hoga, padega, sakta hoon, samajh gaya, batata hoon, nikal sakta hoon
RIGHT (feminine — always use): karungi, karegi, hogi, padegi, sakti hoon, samajh gayi, bata sakti hoon, nikal sakti hoon
When in doubt, use feminine. No exceptions.

## BREVITY — THE MOST IMPORTANT RULE FOR VOICE
THIS IS A PHONE CALL. Keep every reply to MAX 2 short sentences. That's it.

WRONG (too long — never do this):
"Sharing mein Desert Safari — chhah logon ke liye total around paanch hazaar sixty rupees. Private option ke liye — yeh tour Private Transfers option deta hai. Iska matlab hai ki tour toh shared hoga lekin pickup aur drop aapki apni private vehicle mein hogi. Private transfer ke exact price ke liye mujhe detailed rate check karni padegi."

RIGHT (short and clear):
"Sharing mein six logon ka total around five thousand rupees padega. Private ke liye ek second — check kar rahi hoon."

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

## HANDOFF
"Bahut badhiya sir! Main booking team ko details forward kar rahi hoon — woh fifteen-twenty minutes mein call karenge aapko."
"""


# =============================================================================
# Full assistant config
# =============================================================================
def build_assistant_config() -> dict:
    return {
        "name": "Dubai Trip Planner — Voice",

        # ── Brain ───────────────────────────────────────────────────────────
        "model": _model_config(),

        # ── Ears (STT) ──────────────────────────────────────────────────────
        "transcriber": _stt_config(),

        # ── Voice (TTS) + chunk plan — flush SSE chunks to TTS immediately ───
        # chunkPlan.minCharacters=1: Vapi flushes to TTS the instant ANY chunk
        # lands, not after buffering to the default ~30 chars. This is what makes
        # filler phrases play immediately instead of all at the end.
        "voice": {
            **_tts_config(),
            "chunkPlan": {
                "enabled": True,
                "minCharacters": 1,
                "punctuationBoundaries": [".", "!", "?", ","],
                "formatPlan": {
                    "enabled": False,
                },
            },
        },

        # ── Conversation behaviour ──────────────────────────────────────────
        "responseDelaySeconds": 0,
        "llmRequestDelaySeconds": 0.1,
        "interruptionsEnabled": True,
        "firstMessageInterruptionsEnabled": True,
        "backgroundDenoisingEnabled": True,
        "backchannelingEnabled": True,

        # ── Start speaking plan — no delay, no smart buffering ──────────────
        "startSpeakingPlan": {
            "waitSeconds": 0,
            "smartEndpointingEnabled": False,
        },


        # ── Call lifecycle ──────────────────────────────────────────────────
        "firstMessage": (
            "Haan ji, Gujju Tours mein aapka swagat hai! Main Nikki hoon. "
            "How can I help you?"
        ),
        "firstMessageMode": "assistant-speaks-first",

        "endCallMessage": (
            "Lovely speaking with you! "
        ),

        # Silence for 60s → end call gracefully.
        # 30s was too short — if the agent is speaking a heartbeat and the caller
        # is quiet, Vapi was counting that silence and cutting the call early.
        "silenceTimeoutSeconds": 60,
        "maxDurationSeconds": 2700,   # 45 min hard cap

        # What to say if the call hits the time limit
        "endCallPhrases": [
            "goodbye",
            "bye bye",
            "that's all",
            "thank you bye",
            "ok bye",
            "alvida",
        ],

        # ── Compliance / recording ──────────────────────────────────────────
        "recordingEnabled": True,
        "hipaaEnabled": False,

        # ── Voicemail detection ─────────────────────────────────────────────
        "voicemailDetection": {
            "provider": "twilio",
            "enabled": True,
            "voicemailDetectionTypes": ["machine_end_beep", "machine_end_silence"],
        },
        "voicemailMessage": (
            "Hi, this is your Dubai trip planner calling back. "
            "Please call us or visit our website to plan your trip. Goodbye!"
        ),
    }


# =============================================================================
# Deploy helpers — push config to Vapi via REST
# =============================================================================
def _vapi_request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"https://api.vapi.ai{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {VAPI_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "trip-planner-vapi-config/1.0",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"HTTP {e.code} {e.reason}")
        print(body)
        raise


def create_assistant() -> dict:
    """Create a new Vapi assistant. Prints the new assistant ID."""
    config = build_assistant_config()
    result = _vapi_request("POST", "/assistant", config)
    print(f"[OK] Assistant created: {result['id']}")
    print(f"   Add to .env:  VAPI_ASSISTANT_ID={result['id']}")
    return result


def update_assistant(assistant_id: str | None = None) -> dict:
    """Push current config to existing assistant (PATCH)."""
    aid = assistant_id or VAPI_ASSISTANT_ID
    if not aid:
        raise ValueError("No VAPI_ASSISTANT_ID set — run create_assistant() first.")
    config = build_assistant_config()
    result = _vapi_request("PATCH", f"/assistant/{aid}", config)
    print(f"[OK] Assistant updated: {aid}")
    return result


def get_assistant(assistant_id: str | None = None) -> dict:
    """Fetch current assistant config from Vapi."""
    aid = assistant_id or VAPI_ASSISTANT_ID
    return _vapi_request("GET", f"/assistant/{aid}")


def print_config() -> None:
    """Pretty-print the config that would be sent to Vapi (dry run)."""
    print(json.dumps(build_assistant_config(), indent=2))


# =============================================================================
# CLI
# =============================================================================
if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "print"

    if cmd == "create":
        if not VAPI_API_KEY:
            print("❌ VAPI_API_KEY not set in .env")
            sys.exit(1)
        create_assistant()

    elif cmd == "update":
        if not VAPI_API_KEY:
            print("❌ VAPI_API_KEY not set in .env")
            sys.exit(1)
        update_assistant()

    elif cmd == "get":
        if not VAPI_ASSISTANT_ID:
            print("❌ VAPI_ASSISTANT_ID not set in .env")
            sys.exit(1)
        print(json.dumps(get_assistant(), indent=2))

    elif cmd == "print":
        print_config()

    else:
        print("Usage: python vapi_config.py [create|update|get|print]")
        print("  create — create a new Vapi assistant, prints the ID")
        print("  update — push current config to existing assistant")
        print("  get    — fetch live config from Vapi")
        print("  print  — dry-run: print config without calling Vapi")
