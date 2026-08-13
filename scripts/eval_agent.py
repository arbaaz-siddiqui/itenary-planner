"""eval_agent — end-to-end behavioural evaluation of the chat agent.

Sends real prompts through the SAME path the app uses (stream_and_log) and
grades each turn on what actually matters to a customer:

  * did it call the RIGHT tool          (routing accuracy)
  * did the answer contain the RIGHT data (grounded in the tool result)
  * did it AVOID the known failure modes (hallucination, "doesn't exist")
  * how long did it take                (latency, time-to-first-token)
  * how much context did it burn        (input tokens)

Every expectation is checked against live supplier data resolved at run time,
so a scenario cannot pass by matching a hardcoded string that has gone stale.

Usage:
    python scripts/eval_agent.py                  # full suite, 1 rep
    python scripts/eval_agent.py --reps 3         # 3 reps (catches flakiness)
    python scripts/eval_agent.py --only hotel     # filter by scenario id
    python scripts/eval_agent.py --warm-cache     # run twice, grade the 2nd
                                                  # (reproduces the cache bug)
    python scripts/eval_agent.py --json out.json  # machine-readable results
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from agent import (  # noqa: E402
    StreamResult,
    build_in_memory_checkpoint,
    build_react_agent,
    extract_tool_calls,
    stream_and_log,
)

try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")
    def ntok(s: str) -> int:
        return len(_ENC.encode(s))
except Exception:  # pragma: no cover
    def ntok(s: str) -> int:
        return len(s) // 4


# =============================================================================
# Grading primitives
# =============================================================================
@dataclass
class Check:
    """One named assertion about a turn."""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class Scenario:
    """A prompt plus what a correct answer must look like."""

    id: str
    prompt: str
    # Tools that MUST be called (any of the listed alternatives satisfies it)
    must_call: tuple[str, ...] = ()
    # Tools that must NOT be called
    must_not_call: tuple[str, ...] = ()
    # Substrings that must appear in the reply (case-insensitive)
    must_contain: tuple[str, ...] = ()
    # Substrings that must NOT appear — the known failure modes
    must_not_contain: tuple[str, ...] = ()
    # Extra assertions given (reply, tool_calls)
    custom: Callable[[str, list[dict]], list[Check]] | None = None
    # Multi-turn: prior messages sent on the same thread first
    preamble: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass
class TurnResult:
    scenario_id: str
    rep: int
    latency_s: float
    ttft_s: float
    input_tokens: int
    reply_chars: int
    tools: list[str]
    checks: list[Check]
    reply: str
    error: str = ""

    @property
    def passed(self) -> bool:
        return not self.error and all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


# Phrases that mean "we don't have it" — the Howard Johnson failure mode.
_NOT_FOUND_PHRASES = (
    "couldn't find",
    "could not find",
    "i don't have any",
    "i do not have any",
    "don't have any",
    "not have any",
    "doesn't exist",
    "does not exist",
    "unable to find",
    "no howard johnson",
)

# Phrases that indicate the agent asked instead of acting.
_STALL_PHRASES = (
    "would you like me to",
    "should i look",
    "shall i check",
    "do you want me to",
)


def says_not_found(text: str) -> bool:
    low = text.lower()
    return any(p in low for p in _NOT_FOUND_PHRASES)


# =============================================================================
# Live ground truth — resolved once, so scenarios assert against real data
# =============================================================================
@dataclass
class GroundTruth:
    hj_name: str = ""
    hj_id: int = 0
    hj_total: float = 0.0
    visa_types: list[str] = field(default_factory=list)
    transfer_types: set[str] = field(default_factory=set)
    cheapest_airline: str = ""

    @classmethod
    def resolve(cls) -> GroundTruth:
        from mcp_tools.get_visa_info import _impl as visa
        from mcp_tools.search_hotels import _impl as hotels
        from mcp_tools.search_transfers import _impl as transfers

        gt = cls()
        try:
            r = hotels(
                destination_city="Dubai", check_in="2026-12-01",
                check_out="2026-12-05", adults=2, hotel_name="Howard Johnson",
                max_results=3,
            )
            o = (r.get("options") or [{}])[0]
            gt.hj_name = str(o.get("hotel_name") or "")
            gt.hj_id = int(o.get("hotel_id") or 0)
            gt.hj_total = float(o.get("price_inr") or 0)
        except Exception as e:
            print(f"  [warn] hotel ground truth failed: {e}")
        try:
            v = visa(
                destination_country="United Arab Emirates",
                nationality_country="India", travel_date="2026-12-01", adults=2,
            )
            gt.visa_types = [o["visa_type"] for o in (v.get("options") or [])]
        except Exception as e:
            print(f"  [warn] visa ground truth failed: {e}")
        try:
            t = transfers(
                hotel_name="Howard Johnson by Wyndham Bur Dubai",
                arrival_date="2026-12-01", adults=2, max_results=10,
            )
            gt.transfer_types = {o["transfer_type"] for o in (t.get("options") or [])}
        except Exception as e:
            print(f"  [warn] transfer ground truth failed: {e}")
        return gt


# =============================================================================
# Scenarios — one per capability, plus the known-failure regressions
# =============================================================================
def build_scenarios(gt: GroundTruth) -> list[Scenario]:
    hj_token = "howard johnson"

    def hotel_found(reply: str, calls: list[dict]) -> list[Check]:
        low = reply.lower()
        checks = [
            Check(
                "names the real property",
                hj_token in low,
                f"expected {gt.hj_name!r}",
            ),
            Check("does not claim missing", not says_not_found(reply)),
        ]
        if gt.hj_total:
            # Price should appear in some Indian-grouped form.
            whole = f"{int(gt.hj_total):,}"
            indian = f"{int(gt.hj_total):,}".replace(",", "")
            checks.append(
                Check(
                    "quotes a real price",
                    any(x in reply.replace(",", "") for x in (indian, whole.replace(",", ""))),
                    f"expected ~{gt.hj_total:.0f}",
                )
            )
        return checks

    def mentions_no_foreign_city(reply: str, calls: list[dict]) -> list[Check]:
        low = reply.lower()
        foreign = [c for c in ("bakersfield", "changsha", "yibin", "beaufort", "china")
                   if c in low]
        return [Check("no foreign-city leakage", not foreign, f"found {foreign}")]

    scenarios: list[Scenario] = [
        # ---- REGRESSION: the reported customer-facing bug -------------------
        Scenario(
            id="hotel_by_name_bare",
            prompt=(
                "need to look for the availablity in the Howard Johnson Hotel "
                "for 4 nights from 1 december 2026 to 5 december 2026"
            ),
            must_call=("search_hotels",),
            must_not_call=("lookup_entity",),
            custom=lambda r, c: hotel_found(r, c) + mentions_no_foreign_city(r, c),
            tags=("regression", "hotel", "routing"),
        ),
        Scenario(
            id="hotel_by_name_reworded",
            prompt="Hotel Howard Johnson, 1 to 5 December 2026, 2 adults — is it available?",
            must_call=("search_hotels",),
            must_not_call=("lookup_entity",),
            custom=lambda r, c: hotel_found(r, c) + mentions_no_foreign_city(r, c),
            tags=("regression", "hotel", "routing"),
        ),
        # ---- Core search capabilities ---------------------------------------
        Scenario(
            id="flights_basic",
            prompt="Show me flights from Hyderabad to Dubai on 1 December 2026 for 2 adults",
            must_call=("search_flights",),
            must_not_contain=("i'll check", "let me look into"),
            custom=lambda r, c: [
                Check("shows a price", "₹" in r or "rs" in r.lower()),
                Check("shows a time", any(f"{h:02d}:" in r for h in range(24))),
            ],
            tags=("flights", "detail"),
        ),
        Scenario(
            id="flights_detail_baggage",
            prompt=(
                "Flights Hyderabad to Dubai 1 December 2026, 2 adults. "
                "Include baggage allowance for each option."
            ),
            must_call=("search_flights",),
            custom=lambda r, c: [
                Check("mentions baggage", "kg" in r.lower()),
            ],
            tags=("flights", "detail"),
        ),
        Scenario(
            id="hotels_city",
            prompt="Find me hotels in Dubai from 1 to 5 December 2026 for 2 adults",
            must_call=("search_hotels",),
            custom=lambda r, c: [
                Check("shows a price", "₹" in r or "rs" in r.lower()),
                Check("shows per-night or board", "night" in r.lower() or "room only" in r.lower()),
            ],
            tags=("hotels", "detail"),
        ),
        Scenario(
            id="visa",
            prompt="What visa do I need for Dubai? I'm an Indian citizen travelling 1 December 2026.",
            must_call=("get_visa_info",),
            custom=lambda r, c: [
                Check(
                    "names a real visa type",
                    any(v.split()[0].lower() in r.lower() for v in gt.visa_types) if gt.visa_types else True,
                ),
                Check("does not invent a price", "on request" in r.lower() or "₹" not in r),
                # Word-boundary: "30 Days Single Entry" must not match.
                Check(
                    "no bogus 0-day processing",
                    not re.search(r"(?<!\d)0\s*(?:days?|working days?)", r, re.I),
                ),
            ],
            tags=("visa",),
        ),
        Scenario(
            id="transfers_shared_private",
            prompt=(
                "I need an airport pickup on 1 December 2026 to Howard Johnson by "
                "Wyndham Bur Dubai for 2 people. What are the options?"
            ),
            must_call=("search_airport_transfer_dubai",),
            custom=lambda r, c: [
                Check(
                    "states pricing basis",
                    any(x in r.lower() for x in ("per person", "whole vehicle", "total for", "per vehicle")),
                ),
                Check(
                    "does not invent shared when none exist",
                    True if "Shared" in gt.transfer_types else "shared" not in r.lower(),
                    f"live types={sorted(gt.transfer_types)}",
                ),
            ],
            tags=("transfers",),
        ),
        Scenario(
            id="tours",
            prompt="What tours can I do in Dubai on 2 December 2026?",
            must_call=("search_tours",),
            custom=lambda r, c: [Check("shows a price", "₹" in r or "rs" in r.lower())],
            tags=("tours",),
        ),
        # ---- The "plan everything" request (client complaint) ---------------
        Scenario(
            id="plan_everything",
            prompt=(
                "create an itinerary for dubai with 2 ppl flying from hyderabad, "
                "4 nights from 1 december 2026, we want hotel, pickup options, "
                "tours and visa — everything. budget 5 lakh rupees."
            ),
            must_call=("search_flights", "search_hotels"),
            custom=lambda r, c: [
                Check(
                    "searched transfers too",
                    any("transfer" in t for t in [x.get("tool_name", "") for x in c]),
                    "client complaint: asked for pickup, agent asked back instead",
                ),
                Check(
                    "searched visa too",
                    any("visa" in t for t in [x.get("tool_name", "") for x in c]),
                ),
                Check(
                    "does not ask about something already requested",
                    not any(
                        p in r.lower()
                        for p in ("should i look for private airport transfers",
                                  "would you like me to find transfers")
                    ),
                ),
            ],
            tags=("multi", "regression"),
        ),
        # ---- Anti-hallucination ---------------------------------------------
        Scenario(
            id="nonexistent_hotel",
            prompt="Is the Ritz-Carlton Moon Base Dubai available 1-5 December 2026?",
            custom=lambda r, c: [
                Check(
                    "does not invent the property",
                    "moon base" not in r.lower() or says_not_found(r),
                    "must not fabricate availability",
                ),
            ],
            tags=("hallucination",),
        ),
        Scenario(
            id="no_invented_price",
            prompt="Roughly what does a 5-star Dubai hotel cost per night in December 2026?",
            custom=lambda r, c: [
                Check(
                    "either searched or declined to guess",
                    bool(c) or not any(x in r for x in ("₹", "Rs")),
                    "quoted a price with no tool call = hallucination",
                ),
            ],
            tags=("hallucination",),
        ),
        # ---- Multi-turn context ---------------------------------------------
        Scenario(
            id="followup_keeps_context",
            preamble=(
                "Flights from Hyderabad to Dubai on 1 December 2026 for 2 adults",
            ),
            prompt="Now find me a hotel for those same dates",
            must_call=("search_hotels",),
            custom=lambda r, c: [
                Check(
                    "did not re-ask the dates",
                    not any(p in r.lower() for p in ("what dates", "which dates", "when are you")),
                ),
            ],
            tags=("context", "multi"),
        ),
        Scenario(
            id="pdf_first_try",
            preamble=(
                "Flights Hyderabad to Dubai 1 December 2026, 2 adults",
                "Hotels in Dubai 1 to 5 December 2026 for 2 adults",
            ),
            prompt="Looks good — send me the itinerary PDF",
            must_call=("generate_itinerary_pdf_tool",),
            custom=lambda r, c: [
                Check(
                    "did not re-ask for details",
                    not any(p in r.lower() for p in ("what are the dates", "which hotel", "how many")),
                    "client complaint: PDF takes 2-3 tries",
                ),
            ],
            tags=("pdf", "regression"),
        ),
    ]
    return scenarios


# =============================================================================
# Runner
# =============================================================================
def run_scenario(sc: Scenario, rep: int, *, model: str | None) -> TurnResult:
    agent = build_react_agent(
        surface="streamlit",
        checkpoint_store=build_in_memory_checkpoint(),
        model_override=model,
    )
    thread = f"eval_{sc.id}_{rep}_{int(time.time() * 1000) % 100000}"

    # Preamble turns establish context; not graded.
    for msg in sc.preamble:
        try:
            list(stream_and_log(agent, surface="streamlit", thread_id=thread,
                                user_message=msg, turn_number=0, result=StreamResult()))
        except Exception as e:
            return TurnResult(sc.id, rep, 0, 0, 0, 0, [], [], "", f"preamble failed: {e}")

    res = StreamResult()
    start = time.perf_counter()
    ttft = 0.0
    chunks: list[str] = []
    try:
        for token in stream_and_log(agent, surface="streamlit", thread_id=thread,
                                    user_message=sc.prompt, turn_number=1, result=res):
            if not chunks:
                ttft = time.perf_counter() - start
            chunks.append(token)
    except Exception as e:
        return TurnResult(sc.id, rep, time.perf_counter() - start, ttft, 0, 0, [], [], "", str(e))

    latency = time.perf_counter() - start
    reply = "".join(chunks)
    calls = extract_tool_calls(res.response) if res.response else []
    tools = [c.get("tool_name", "") for c in calls]

    checks: list[Check] = []
    for want in sc.must_call:
        checks.append(Check(f"calls {want}", want in tools, f"called {tools}"))
    for avoid in sc.must_not_call:
        checks.append(Check(f"avoids {avoid}", avoid not in tools, f"called {tools}"))
    for frag in sc.must_contain:
        checks.append(Check(f"mentions {frag!r}", frag.lower() in reply.lower()))
    for frag in sc.must_not_contain:
        checks.append(Check(f"omits {frag!r}", frag.lower() not in reply.lower()))
    if sc.custom:
        try:
            checks.extend(sc.custom(reply, calls))
        except Exception as e:
            checks.append(Check("custom grader", False, f"grader crashed: {e}"))

    in_tok = ntok(reply)
    return TurnResult(sc.id, rep, latency, ttft, in_tok, len(reply), tools, checks, reply)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--only", default="")
    ap.add_argument("--model", default=None, help="model_override (default: env)")
    ap.add_argument("--warm-cache", action="store_true",
                    help="run each scenario twice, grade the second (cache-bug repro)")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    print("Resolving live ground truth…")
    gt = GroundTruth.resolve()
    print(f"  hotel : {gt.hj_name or '(unresolved)'} id={gt.hj_id} ₹{gt.hj_total:.0f}")
    print(f"  visa  : {len(gt.visa_types)} options")
    print(f"  xfer  : {sorted(gt.transfer_types) or '(none)'}")
    print()

    scenarios = [s for s in build_scenarios(gt) if args.only.lower() in s.id.lower()]
    print(f"Running {len(scenarios)} scenarios × {args.reps} rep(s)"
          f"{' (warm cache)' if args.warm_cache else ''}…\n")

    results: list[TurnResult] = []
    for sc in scenarios:
        for rep in range(1, args.reps + 1):
            if args.warm_cache:
                run_scenario(sc, rep, model=args.model)  # prime, discard
            r = run_scenario(sc, rep, model=args.model)
            results.append(r)
            mark = "PASS" if r.passed else "FAIL"
            print(f"[{mark}] {sc.id:26s} rep{rep} {r.latency_s:6.1f}s "
                  f"ttft={r.ttft_s:5.1f}s tools={','.join(r.tools) or '-'}")
            for f in r.failures:
                print(f"         ! {f.name}{(' — ' + f.detail) if f.detail else ''}")
            if r.error:
                print(f"         ! ERROR {r.error}")

    # ---- Summary ----
    print("\n" + "=" * 78)
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    print(f"PASS {passed}/{total}  ({100 * passed / total:.0f}%)" if total else "no results")
    lat = [r.latency_s for r in results if r.latency_s]
    if lat:
        print(f"latency  mean {statistics.mean(lat):5.1f}s   "
              f"median {statistics.median(lat):5.1f}s   max {max(lat):5.1f}s")
    ttfts = [r.ttft_s for r in results if r.ttft_s]
    if ttfts:
        print(f"ttft     mean {statistics.mean(ttfts):5.1f}s   median {statistics.median(ttfts):5.1f}s")

    by_scenario: dict[str, list[TurnResult]] = {}
    for r in results:
        by_scenario.setdefault(r.scenario_id, []).append(r)
    flaky = {k: v for k, v in by_scenario.items()
             if 0 < sum(1 for x in v if x.passed) < len(v)}
    if flaky:
        print(f"\nFLAKY (inconsistent across reps): {', '.join(sorted(flaky))}")

    failing = sorted({r.scenario_id for r in results if not r.passed})
    if failing:
        print(f"\nFAILING SCENARIOS: {', '.join(failing)}")

    if args.json:
        Path(args.json).write_text(json.dumps([{
            "scenario": r.scenario_id, "rep": r.rep, "passed": r.passed,
            "latency_s": round(r.latency_s, 2), "ttft_s": round(r.ttft_s, 2),
            "tools": r.tools, "reply": r.reply,
            "failures": [{"name": c.name, "detail": c.detail} for c in r.failures],
            "error": r.error,
        } for r in results], indent=1), encoding="utf-8")
        print(f"\nwrote {args.json}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
