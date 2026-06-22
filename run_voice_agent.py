#!/usr/bin/env python
"""
run_voice_agent.py — One-file launcher + caller + live log viewer for the
trip-planner voice agent.

What it does, in order:
  1. Checks Postgres (Docker), the planner /voice bridge (8100), the VoiceCare
     API (3000), and the ngrok tunnel are reachable. Tells you what to start if
     anything is down.
  2. Places a Vapi call to your number.
  3. Tails the planner log live so you SEE every turn + every [BOOKING-API] call
     while you're on the phone.

Usage (from repo root, venv active):
    python run_voice_agent.py                 # call the default number
    python run_voice_agent.py +919876543210   # call a specific number
    python run_voice_agent.py --no-call       # just watch logs, don't call

Edit CONFIG below if your ports / paths / number differ.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request

# ============================================================================
# CONFIG — change these if needed
# ============================================================================
DEFAULT_NUMBER = "+918881310786"      # <- your phone number
PLANNER_URL = "http://127.0.0.1:8100"  # planner /voice bridge
VOICECARE_URL = "http://localhost:3000"  # NestJS API
NGROK_INSPECT = "http://127.0.0.1:4040/api/tunnels"  # ngrok local API
VOICECARE_ENV = os.path.join(
    os.path.dirname(__file__), "voice_agent", "voice-care", "voicecare-api", ".env"
)
# The planner log file this script tails. The launcher writes the planner's
# stdout here when it starts the bridge itself; if you run the bridge in your
# own terminal, just watch THAT terminal instead.
PLANNER_LOG = os.path.join(os.path.dirname(__file__), "planner_voice.log")


# ============================================================================
def _get(url: str, timeout: float = 3.0) -> tuple[int, str]:
    try:
        req = urllib.request.Request(url, headers={"ngrok-skip-browser-warning": "1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def _read_env(key: str) -> str:
    try:
        with open(VOICECARE_ENV, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return ""


def check_services() -> bool:
    print("\n=== Service check ===")
    ok = True

    code, _ = _get(f"{PLANNER_URL}/health")
    if code == 200:
        print("  [OK] planner /voice bridge (8100)")
    else:
        ok = False
        print("  [DOWN] planner bridge (8100). Start it:")
        print("     uvicorn surfaces.voice_app:app --host 127.0.0.1 --port 8100")

    code, _ = _get(f"{VOICECARE_URL}/health")
    if code == 200:
        print("  [OK] VoiceCare API (3000)")
    else:
        ok = False
        print("  [DOWN] VoiceCare API (3000). Start it:")
        print("     cd voice_agent/voice-care/voicecare-api ; npm run start")

    code, body = _get(NGROK_INSPECT)
    public = ""
    if code == 200:
        try:
            tunnels = json.loads(body).get("tunnels", [])
            public = next((t["public_url"] for t in tunnels if t.get("public_url", "").startswith("https")), "")
        except Exception:  # noqa: BLE001
            pass
    if public:
        print(f"  [OK] ngrok tunnel -> {public}")
        print("     (Vapi assistant model.url should be this + /api/webhook)")
    else:
        ok = False
        print("  [DOWN] ngrok. Start it:  ngrok http 3000")

    return ok


def place_call(number: str) -> None:
    ak = _read_env("VAPI_API_KEY")
    aid = _read_env("VAPI_ASSISTANT_ID")
    pn = _read_env("VAPI_PHONE_NUMBER_ID")
    if not (ak and aid and pn):
        print("  [ERROR] Missing VAPI creds in", VOICECARE_ENV)
        return

    payload = json.dumps(
        {"assistantId": aid, "phoneNumberId": pn, "customer": {"number": number}}
    ).encode()
    req = urllib.request.Request(
        "https://api.vapi.ai/call/phone",
        data=payload,
        headers={"Authorization": f"Bearer {ak}", "Content-Type": "application/json"},
        method="POST",
    )
    print(f"\n=== Calling {number} ... ===")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode())
            print(f"  callId: {d.get('id')}   status: {d.get('status')}")
    except Exception as e:  # noqa: BLE001
        print(f"  [ERROR] call failed: {e}")


def tail_planner_log() -> None:
    print("\n=== Live log (Ctrl+C to stop) ===")
    print("Watch for [BOOKING-API] lines = real API calls the agent makes.\n")
    if not os.path.exists(PLANNER_LOG):
        print(f"(No {PLANNER_LOG} yet — if you run the bridge in your own terminal,")
        print(" watch THAT window instead; the booking-API trace prints there.)")
        return
    with open(PLANNER_LOG, encoding="utf-8", errors="ignore") as f:
        f.seek(0, os.SEEK_END)
        try:
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.4)
                    continue
                s = re.sub(r"\x1b\[[0-9;]*m", "", line).rstrip()
                if "[BOOKING-API]" in s:
                    print("  >>", s)
                elif any(k in s for k in ("incoming_voice_turn", "voice_reply_sent", "error")):
                    print("  ..", s[:160])
        except KeyboardInterrupt:
            print("\n(stopped watching)")


def main() -> None:
    args = sys.argv[1:]
    number = DEFAULT_NUMBER
    do_call = True
    for a in args:
        if a == "--no-call":
            do_call = False
        elif a.startswith("+") or a.isdigit():
            number = a if a.startswith("+") else f"+{a}"

    services_ok = check_services()
    if not services_ok:
        print("\nFix the [DOWN] services above, then re-run.")
        if not do_call:
            return
        print("(Calling anyway — it will fail if the chain is broken.)")

    if do_call:
        place_call(number)

    tail_planner_log()


if __name__ == "__main__":
    main()
