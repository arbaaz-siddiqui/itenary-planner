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
        print(f"[OK] Dispatch rule: {created_rule.sip_dispatch_rule_id}  → agent '{AGENT_NAME}'")
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
    elif cmd in ("setup", "list", "delete"):
        _require_env()
        asyncio.run({"setup": _setup, "list": _list, "delete": _delete}[cmd]())
    else:
        print("Usage: python livekit_config.py [setup|list|delete|print]")
        print("  setup  — create inbound SIP trunk + agent dispatch rule")
        print("  list   — show current trunks + dispatch rules")
        print("  delete — remove the trunk + rule created by setup")
        print("  print  — dry-run (no API calls)")
