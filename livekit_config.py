"""livekit_config.py — provision LiveKit SIP + agent dispatch for the voice agent.

The counterpart to vapi_config.py. Where vapi_config pushed an assistant config
to Vapi, this wires up the LiveKit telephony path so inbound phone calls reach
the Nikki agent (voice_livekit.py):

    Phone (PSTN)
      → SIP provider (Twilio/Exotel/Plivo) SIP trunk
        → LiveKit SIP Inbound Trunk        (accepts the call)
          → SIP Dispatch Rule              (puts caller in a room + dispatches the agent)
            → voice_livekit.py worker        (agent_name = "nikki-trip-planner")

Run:
  python livekit_config.py setup     # create inbound trunk + dispatch rule
  python livekit_config.py list      # show current trunks + dispatch rules
  python livekit_config.py delete    # remove the trunk + rule we created
  python livekit_config.py print     # dry-run: show what setup would create

Env vars (see .env):
  LIVEKIT_URL          wss://<project>.livekit.cloud
  LIVEKIT_API_KEY      project API key
  LIVEKIT_API_SECRET   project API secret
  LIVEKIT_SIP_NUMBER   (optional) the DID/phone number your SIP provider routes in;
                       when set, the dispatch rule is scoped to that number.

NOTE: You still create the SIP trunk on your telephony provider (Twilio/Exotel)
and point it at LiveKit's SIP URI (shown in the LiveKit Cloud dashboard). This
script configures the LiveKit *side* (trunk + dispatch). Outbound dialing uses a
separate outbound trunk — add it later when the dialer is built.
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

import livekit.api as api

# Must match WorkerOptions(agent_name=...) in voice_livekit.py.
AGENT_NAME = "nikki-trip-planner"
TRUNK_NAME = "gujjutours-inbound"
RULE_NAME = "gujjutours-dispatch"
ROOM_PREFIX = "call-"
OUTBOUND_TRUNK_NAME = "gujjutours-outbound"


def _require_env() -> None:
    missing = [
        k for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
        if not os.getenv(k)
    ]
    if missing:
        print(f"[X] Missing env vars: {', '.join(missing)}")
        print("    Set them in .env from your LiveKit Cloud project settings.")
        sys.exit(1)


def _client() -> "api.LiveKitAPI":
    # Reads LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET from env.
    return api.LiveKitAPI()


async def _setup() -> None:
    """Create (idempotently) the inbound SIP trunk + dispatch rule."""
    number = os.getenv("LIVEKIT_SIP_NUMBER", "").strip()
    lk = _client()
    try:
        # 1. Inbound trunk — accepts calls from your SIP provider. If a DID is
        #    configured, scope the trunk to it; else accept any (dev/testing).
        trunk = api.SIPInboundTrunkInfo(
            name=TRUNK_NAME,
            numbers=[number] if number else [],
        )
        created_trunk = await lk.sip.create_sip_inbound_trunk(
            api.CreateSIPInboundTrunkRequest(trunk=trunk)
        )
        trunk_id = created_trunk.sip_trunk_id
        print(f"[OK] Inbound trunk: {trunk_id}  (numbers={list(trunk.numbers) or 'any'})")

        # 2. Dispatch rule — each caller gets their own room (call-<random>) and
        #    the Nikki agent is dispatched into it by name.
        rule = api.SIPDispatchRule(
            dispatch_rule_individual=api.SIPDispatchRuleIndividual(room_prefix=ROOM_PREFIX)
        )
        req = api.CreateSIPDispatchRuleRequest(
            name=RULE_NAME,
            rule=rule,
            trunk_ids=[trunk_id],
            room_config=api.RoomConfiguration(
                agents=[api.RoomAgentDispatch(agent_name=AGENT_NAME)]
            ),
        )
        if number:
            req.inbound_numbers.append(number)
        created_rule = await lk.sip.create_sip_dispatch_rule(req)
        print(f"[OK] Dispatch rule: {created_rule.sip_dispatch_rule_id}  -> agent '{AGENT_NAME}'")
        print("\nInbound calls will now spawn a room and dispatch the Nikki agent.")
        print("Point your SIP provider's trunk at the LiveKit SIP URI (LiveKit dashboard → SIP).")
    finally:
        await lk.aclose()


async def _list() -> None:
    lk = _client()
    try:
        trunks = await lk.sip.list_sip_inbound_trunk(api.ListSIPInboundTrunkRequest())
        print("Inbound trunks:")
        for t in trunks.items:
            print(f"  {t.sip_trunk_id}  name={t.name}  numbers={list(t.numbers) or 'any'}")
        rules = await lk.sip.list_sip_dispatch_rule(api.ListSIPDispatchRuleRequest())
        print("Dispatch rules:")
        for r in rules.items:
            agents = []
            if r.room_config and r.room_config.agents:
                agents = [a.agent_name for a in r.room_config.agents]
            print(f"  {r.sip_dispatch_rule_id}  name={r.name}  trunks={list(r.trunk_ids)}  agents={agents}")
    finally:
        await lk.aclose()


async def _delete() -> None:
    """Remove the trunk + rule this script created (matched by name)."""
    lk = _client()
    try:
        rules = await lk.sip.list_sip_dispatch_rule(api.ListSIPDispatchRuleRequest())
        for r in rules.items:
            if r.name == RULE_NAME:
                await lk.sip.delete_sip_dispatch_rule(
                    api.DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=r.sip_dispatch_rule_id)
                )
                print(f"[OK] Deleted dispatch rule {r.sip_dispatch_rule_id}")
        trunks = await lk.sip.list_sip_inbound_trunk(api.ListSIPInboundTrunkRequest())
        for t in trunks.items:
            if t.name == TRUNK_NAME:
                await lk.sip.delete_sip_trunk(
                    api.DeleteSIPTrunkRequest(sip_trunk_id=t.sip_trunk_id)
                )
                print(f"[OK] Deleted trunk {t.sip_trunk_id}")
    finally:
        await lk.aclose()


async def _setup_outbound() -> None:
    """Create the OUTBOUND SIP trunk so LiveKit can dial user phones via Twilio.

    This powers the Streamlit "Get a call" button: LiveKit places a call OUT
    through Twilio's termination domain, with our DID as caller ID.
    """
    domain = os.getenv("TWILIO_TERMINATION_DOMAIN", "").strip()
    user = os.getenv("TWILIO_TERMINATION_USER", "").strip()
    pw = os.getenv("TWILIO_TERMINATION_PASS", "").strip()
    caller_id = os.getenv("LIVEKIT_SIP_NUMBER", "").strip()
    missing = [
        k for k, v in {
            "TWILIO_TERMINATION_DOMAIN": domain,
            "TWILIO_TERMINATION_USER": user,
            "TWILIO_TERMINATION_PASS": pw,
            "LIVEKIT_SIP_NUMBER": caller_id,
        }.items() if not v
    ]
    if missing:
        print(f"[X] Missing env for outbound trunk: {', '.join(missing)}")
        sys.exit(1)

    lk = _client()
    try:
        trunk = api.SIPOutboundTrunkInfo(
            name=OUTBOUND_TRUNK_NAME,
            address=domain,
            transport=api.SIPTransport.SIP_TRANSPORT_TCP,
            numbers=[caller_id],          # caller ID shown to the person we dial
            auth_username=user,
            auth_password=pw,
        )
        created = await lk.sip.create_sip_outbound_trunk(
            api.CreateSIPOutboundTrunkRequest(trunk=trunk)
        )
        print(f"[OK] Outbound trunk: {created.sip_trunk_id}  (via {domain}, caller-id {caller_id})")
        print(f"\nAdd this to .env:\n  LIVEKIT_OUTBOUND_TRUNK_ID={created.sip_trunk_id}")
    finally:
        await lk.aclose()


async def place_call(number: str, *, room_name: str | None = None) -> dict:
    """Dial `number` and dispatch Nikki into the room. Returns {room, participant}.

    Used by the Streamlit voice tab (via voice_service.place_call_livekit).
    """
    trunk_id = os.getenv("LIVEKIT_OUTBOUND_TRUNK_ID", "").strip()
    if not trunk_id:
        return {"error": "LIVEKIT_OUTBOUND_TRUNK_ID not set (run: python livekit_config.py setup-outbound)"}
    if not number:
        return {"error": "no phone number"}

    # A unique room per call. No Date/random here — the caller passes one, else
    # we derive from the number (fine for one-at-a-time manual testing).
    room = room_name or f"{ROOM_PREFIX}{number.lstrip('+')}"

    lk = _client()
    try:
        # 1. Dispatch the agent into the room FIRST so Nikki is present when the
        #    callee answers (explicit dispatch — matches agent_name).
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name=AGENT_NAME, room=room)
        )
        # 2. Dial the user; SIP participant joins the same room.
        participant = await lk.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=number,
                room_name=room,
                participant_identity=f"caller-{number.lstrip('+')}",
                participant_name="caller",
                play_dialtone=True,
            )
        )
        return {"room": room, "participant": participant.participant_identity, "number": number}
    finally:
        await lk.aclose()


def _print() -> None:
    number = os.getenv("LIVEKIT_SIP_NUMBER", "").strip() or "(any — no LIVEKIT_SIP_NUMBER set)"
    print("Would create:")
    print(f"  Inbound trunk '{TRUNK_NAME}'  numbers={number}")
    print(f"  Dispatch rule '{RULE_NAME}'  room_prefix='{ROOM_PREFIX}'  agent='{AGENT_NAME}'")
    print(f"  LIVEKIT_URL={os.getenv('LIVEKIT_URL', '(unset)')}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "print"
    if cmd == "print":
        _print()
    elif cmd == "setup-outbound":
        _require_env()
        asyncio.run(_setup_outbound())
    elif cmd == "call":
        _require_env()
        num = sys.argv[2] if len(sys.argv) > 2 else ""
        print(asyncio.run(place_call(num)))
    elif cmd in ("setup", "list", "delete"):
        _require_env()
        asyncio.run({"setup": _setup, "list": _list, "delete": _delete}[cmd]())
    else:
        print("Usage: python livekit_config.py [setup|setup-outbound|call <number>|list|delete|print]")
        print("  setup          — create inbound SIP trunk + agent dispatch rule")
        print("  setup-outbound — create outbound SIP trunk (for click-to-call)")
        print("  call <number>  — dial a number now + dispatch Nikki (test)")
        print("  list           — show current trunks + dispatch rules")
        print("  delete         — remove the trunk + rule created by setup")
        print("  print          — dry-run (no API calls)")
