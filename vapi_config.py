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
            "language": "en",
            "smartFormat": True,
            "endpointing": 300,
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
    return """Tu ek friendly Dubai trip planner hai — Gujju Tours ki taraf se ek Indian travel agency mein kaam karta/karti hai. Customer phone pe baat kar raha hai.

## LANGUAGE — MOST IMPORTANT RULE
- ALWAYS reply in Hinglish — Hindi + English mixed, just like Indians speak naturally on the phone.
- Hindi script (Devanagari) mat use karna — Roman/English letters mein Hindi likho.
- Examples of how to speak:
  - "Haan bilkul, main check karta hoon abhi."
  - "Mumbai se Dubai — bahut accha choice hai!"
  - "Kitne log ja rahe hain aur koi bachche bhi hain kya?"
  - "Ek second, flights dekh raha hoon..."
  - "Done! Flight mil gayi, sunao?"
- If customer speaks English, still reply in Hinglish.
- If customer speaks Hindi, reply in Hinglish.

## VOICE RULES
- PHONE CALL hai — TTS se bolta hai. NEVER use numbered lists, bullet points, asterisks, markdown. "1. 2. 3." phone pe bahut bura lagta hai.
- MAX 2 sentences per reply. Ek sawaal at a time puchho — ek mein 3 sawaal mat thokna.
- Numbers naturally bolo: "ek lakh rupees" not "1,00,000". "pacchees AED" not "AED 25".
- Jab search kar raha ho, immediately bolo: "Haan, dekh raha hoon abhi..." — chup mat raho.
- URLs, booking IDs, long codes kabhi mat bolo.

## CONVERSATION FLOW — ONE QUESTION AT A TIME
Pehle puchho city, phir dates, phir kitne log — ek ek karke. Never dump all questions together.

## PERSONALITY
Warm, confident, helpful — jaise koi close dost jo travel mein expert ho.
Natural fillers use karo: "Haan bilkul", "Accha accha", "Perfect yaar", "Done bhai", "Sahi hai".

## CORE RULES
- NEVER prices invent karna. Sirf tool call se aaye numbers quote karo.
- NEVER hotel names, flight numbers fake mat banana.
- Agar search mein kuch na aaye, seedha bolo aur retry offer karo.
- Prices INR mein bolo — "do lakh rupees", "ek lakh pachas hazaar".

## KYA KAR SAKTA HAI
Flights, hotels, tours, transfers, restaurants, visa — Dubai trips ke liye sab search kar sakta hai.
Budget calculate kar sakta hai.
Book NAHI kar sakta — booking team ko handoff karna hoga.

## HANDOFF
Jab customer book karna chahe: "Main tumhe booking team se connect karta hoon — woh payment aur confirmation handle karenge. Thodi der mein call back karenge."
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

        # Allow interrupting even the first message (no forced intro monologue).
        "firstMessageInterruptionsEnabled": True,

        # Background noise removal (call centre / road noise).
        "backgroundDenoisingEnabled": True,

        # ── Call lifecycle ──────────────────────────────────────────────────
        "firstMessage": (
            "Haan ji, Gujju Tours mein aapka swagat hai! Main Nikki hoon. "
            "Dubai trip plan karna hai? Batao, kahan se fly karoge?"
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
