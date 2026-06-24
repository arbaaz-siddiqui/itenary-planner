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
            "model": "nova-2-general",     # nova-2 has better en-IN than nova-3 currently
            "language": "en-IN",           # Indian English — handles accent well
            "smartFormat": True,           # formats numbers, currencies, dates naturally
            "punctuate": True,
            "utteranceEndMs": "1200",      # wait 1.2s of silence before end-of-turn
                                           # longer than default (800ms) — Indian speech
                                           # has natural mid-sentence pauses
            "endpointing": 300,            # ms of silence to detect end of utterance
            "keywords": [                  # boost recognition of domain vocabulary
                "Dubai:2",
                "Mumbai:2",
                "Delhi:2",
                "Bangalore:2",
                "AED:2",
                "INR:2",
                "Burj Khalifa:3",
                "Atlantis:2",
                "Palm Jumeirah:2",
                "Desert Safari:2",
                "Technoheaven:1",
                "itinerary:2",
                "Emirates:2",
                "IndiGo:2",
                "Air India:2",
                "lakh:2",
                "crore:2",
            ],
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
    return """You are a warm, sharp Dubai trip planner at an Indian travel agency, speaking to a customer on a phone call.

## Voice rules — READ THESE FIRST
- You are on a PHONE CALL. Every response is spoken aloud by text-to-speech.
- Keep responses SHORT — 2 to 3 sentences maximum per turn.
- NEVER use bullet points, tables, markdown, asterisks, or headings. None of these render on voice.
- NEVER read out URLs, booking IDs, or long reference numbers.
- Use natural spoken English. Say "one lakh" not "₹1,00,000". Say "twenty three AED" not "AED 23".
- Use conversational fillers naturally: "Sure, let me check that", "Great choice", "Absolutely".
- When searching, say something like "Checking flights for you..." IMMEDIATELY — don't go silent.

## Personality
You are warm, confident, and genuinely helpful — like a knowledgeable friend who works in travel.
Match the customer's energy. If they are casual, be casual. If they are serious, be precise.
Use natural affirmations: "Perfect", "Got it", "Sure thing", "Sounds good".

## Core rules
- NEVER invent prices. Only quote numbers that came from a tool call.
- NEVER fabricate hotel names, flight numbers, or tour prices.
- If a search returns nothing, say so plainly and offer to try different dates.
- All prices in INR. Say "rupees" or "lakh rupees" — not the symbol.

## What you can do
Search flights, hotels, tours, transfers, restaurants, and visa info for Dubai trips.
Compute budgets, party splits, and payment summaries.
You CANNOT book — hand off to the booking team when the customer is ready.

## Handoff
When the customer wants to book: "I'll connect you with our booking team — they handle payment and confirmation. They'll call you back shortly."
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

        # ── Voice (TTS) ─────────────────────────────────────────────────────
        "voice": _tts_config(),

        # ── Conversation behaviour ──────────────────────────────────────────
        # How long Vapi waits after the LLM starts responding before speaking.
        # Lower = faster feel. 0.3s is the practical minimum without clipping.
        "responseDelaySeconds": 0.3,

        # How long after STT finishes before we send to LLM.
        # 0.1s gives the caller a chance to finish their sentence.
        "llmRequestDelaySeconds": 0.1,

        # Caller can interrupt the agent mid-sentence — natural conversation.
        "interruptionsEnabled": True,

        # Background noise removal (call centre / road noise).
        "backgroundDenoisingEnabled": True,

        # Backchannels: Vapi inserts "mm-hmm", "I see", "got it" while
        # the agent is processing — makes silence feel alive.
        "backchannel": {
            "enabled": True,
            "plan": {
                "messages": [
                    {"type": "custom", "message": "Mm-hmm..."},
                    {"type": "custom", "message": "Got it..."},
                    {"type": "custom", "message": "Sure..."},
                    {"type": "custom", "message": "Right..."},
                    {"type": "custom", "message": "Okay..."},
                    {"type": "custom", "message": "I see..."},
                ],
                "randomized": True,
            },
        },

        # Filler injection: spoken IMMEDIATELY when Vapi detects the caller
        # has finished speaking, before the LLM even responds.
        # This is the #1 fix for "dead air" latency perception.
        "fillerInjection": {
            "enabled": True,
            "plan": {
                "messages": [
                    {"type": "custom", "message": "Let me check that for you..."},
                    {"type": "custom", "message": "One moment..."},
                    {"type": "custom", "message": "Sure, looking that up..."},
                    {"type": "custom", "message": "Give me just a second..."},
                ],
                "randomized": True,
            },
        },

        # ── Call lifecycle ──────────────────────────────────────────────────
        "firstMessage": (
            "Hi! I'm your Dubai trip planner. "
            "Where are you flying from, and when are you looking to travel?"
        ),
        "firstMessageMode": "assistant-speaks-first",

        "endCallMessage": (
            "Lovely speaking with you! "
            "Our booking team will follow up with all the details. "
            "Have a wonderful trip to Dubai!"
        ),

        # Silence for 30s → end call gracefully
        "silenceTimeoutSeconds": 30,
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
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def create_assistant() -> dict:
    """Create a new Vapi assistant. Prints the new assistant ID."""
    config = build_assistant_config()
    result = _vapi_request("POST", "/assistant", config)
    print(f"✅ Assistant created: {result['id']}")
    print(f"   Add to .env:  VAPI_ASSISTANT_ID={result['id']}")
    return result


def update_assistant(assistant_id: str | None = None) -> dict:
    """Push current config to existing assistant (PATCH)."""
    aid = assistant_id or VAPI_ASSISTANT_ID
    if not aid:
        raise ValueError("No VAPI_ASSISTANT_ID set — run create_assistant() first.")
    config = build_assistant_config()
    result = _vapi_request("PATCH", f"/assistant/{aid}", config)
    print(f"✅ Assistant updated: {aid}")
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
