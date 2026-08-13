"""eval_journey — full multi-turn customer journeys, start to PDF.

Plays a real customer over a whole conversation on ONE thread: opening
request, answering the agent's questions, picking options, asking follow-ups,
and finally "send me the PDF". This is what the single-turn eval cannot see —
context carried across turns, re-asking of settled facts, and whether the
journey actually terminates in a document.

Each journey is graded on outcomes a customer would notice:

  * did we reach a PDF at all, and on which attempt
  * did the agent re-ask something already stated ("what dates again?")
  * did every requested component actually get searched
  * did it invent prices/hotels, or claim inventory is missing
  * per-turn and whole-journey latency

Usage:
    python scripts/eval_journey.py                 # all journeys, 1 run each
    python scripts/eval_journey.py --runs 15       # 15 journeys total
    python scripts/eval_journey.py --json out.json
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

# ---------------------------------------------------------------- detectors
RE_ASK_PATTERNS = [
    (r"\bwhat (?:are the |were the )?dates\b", "re-asked dates"),
    (r"\bwhich dates\b", "re-asked dates"),
    (r"\bwhen (?:are|do) you (?:want to |plan to )?travel", "re-asked dates"),
    (r"\bhow many (?:people|adults|travell?ers|guests)\b", "re-asked party size"),
    (r"\bwhere (?:are you|will you be) (?:flying|departing) from\b", "re-asked origin"),
    (r"\bwhat(?:'s| is) your budget\b", "re-asked budget"),
    (r"\bwhich city\b", "re-asked destination"),
]

DEFERRAL_PATTERNS = [
    (r"should i (?:look|search|check) (?:for|into)\b", "asked permission to search"),
    (r"would you like me to (?:look|search|find|check)\b", "asked permission to search"),
    (r"once (?:you|we) (?:pick|choose|decide|confirm)", "deferred work to a later turn"),
    (r"i'?ll (?:pull up|look into|check) .{0,40}(?:once|after|when)", "deferred work"),
]

NOT_FOUND_PATTERNS = [
    r"couldn'?t find", r"could not find", r"do(?:n'?t| not) have any",
    r"does(?:n'?t| not) exist", r"unable to find", r"no .{0,25}available in dubai",
]

PHANTOM_SHARED = r"\bshared\b.{0,40}\b(?:shuttle|transfer|option)|(?:private|shared)\s*(?:car|vehicle)?\s*(?:or|vs\.?)\s*(?:shared|private)"


def _pdf_has_day_plan() -> bool:
    """Does the most recently written PDF contain a Day-by-Day section?

    Decompresses the newest file's content streams and looks for the section
    heading the renderer emits. Checking the tool fired is not enough — the
    reported bug was a PDF that generated fine and contained no itinerary.
    """
    import glob
    import zlib

    files = glob.glob(str(Path(__file__).resolve().parent.parent / "itineraries" / "*.pdf"))
    if not files:
        return False
    newest = max(files, key=lambda p: Path(p).stat().st_mtime)
    raw = Path(newest).read_bytes()
    text = ""
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        try:
            text += zlib.decompress(m.group(1)).decode("latin-1")
        except Exception:
            continue
    return "Day-by-Day" in text


def _hits(text: str, patterns: list[tuple[str, str]]) -> list[str]:
    low = text.lower()
    out: list[str] = []
    for pat, label in patterns:
        if re.search(pat, low) and label not in out:
            out.append(label)
    return out


# ---------------------------------------------------------------- model
@dataclass
class Turn:
    """One customer message and what we expect to be true after it."""

    say: str
    # Tools that should fire on this turn (any subset is fine unless required)
    expect_tools: tuple[str, ...] = ()
    # If set, the turn must produce at least one tool call
    require_any_tool: bool = False
    note: str = ""


@dataclass
class Journey:
    id: str
    turns: list[Turn]
    # Components the customer asked for across the journey.
    wants: tuple[str, ...] = ()
    expects_pdf: bool = True


@dataclass
class TurnLog:
    idx: int
    said: str
    reply: str
    tools: list[str]
    latency_s: float
    problems: list[str] = field(default_factory=list)


@dataclass
class JourneyResult:
    journey_id: str
    run: int
    turns: list[TurnLog]
    pdf_generated: bool
    pdf_attempt: int          # 1-based turn index where the PDF fired, 0 = never
    pdf_asks: int             # how many times we had to ask for it
    total_s: float
    error: str = ""

    @property
    def all_tools(self) -> list[str]:
        return [t for tl in self.turns for t in tl.tools]

    @property
    def problems(self) -> list[str]:
        return [p for tl in self.turns for p in tl.problems]

    @property
    def clean(self) -> bool:
        return not self.error and not self.problems and (
            self.pdf_generated or True
        )


# ---------------------------------------------------------------- journeys
def journeys() -> list[Journey]:
    """Realistic conversations, varied in phrasing and order."""
    return [
        Journey(
            id="J1_full_plan_then_pdf",
            wants=("flights", "hotel", "transfers", "tours", "visa"),
            turns=[
                Turn("Hi, I want to plan a Dubai trip for 2 adults from Hyderabad, "
                     "1 to 5 December 2026. Budget around 5 lakh.",
                     require_any_tool=True),
                Turn("Yes please — I also want airport pickup, some tours, and visa info.",
                     require_any_tool=True),
                Turn("The Emirates flight looks good. Which hotel would you recommend?"),
                Turn("Let's go with that hotel. Send me the itinerary PDF.",
                     expect_tools=("generate_itinerary_pdf_tool",)),
            ],
        ),
        Journey(
            id="J2_named_hotel_first",
            wants=("hotel", "flights", "transfers"),
            turns=[
                Turn("Do you have the Howard Johnson in Dubai for 1-5 December 2026?",
                     expect_tools=("search_hotels",), require_any_tool=True),
                Turn("Great. 2 adults flying from Hyderabad — add flights please.",
                     require_any_tool=True),
                Turn("And an airport pickup to that hotel.", require_any_tool=True),
                Turn("Perfect, send it across as a PDF.",
                     expect_tools=("generate_itinerary_pdf_tool",)),
            ],
        ),
        Journey(
            id="J3_vague_then_specific",
            wants=("hotel", "flights"),
            turns=[
                Turn("thinking about dubai"),
                Turn("December, maybe 1st to 5th. 2 of us, from Hyderabad.",
                     require_any_tool=True),
                Turn("show me hotels", require_any_tool=True),
                Turn("ok make the pdf", expect_tools=("generate_itinerary_pdf_tool",)),
            ],
        ),
        Journey(
            id="J4_detail_probing",
            wants=("flights", "hotel"),
            turns=[
                Turn("Flights Hyderabad to Dubai 1 December 2026, 2 adults",
                     expect_tools=("search_flights",), require_any_tool=True),
                Turn("What's the baggage allowance on the cheapest one?"),
                Turn("Fine. Hotels for 1-5 December, 2 adults, with free cancellation.",
                     require_any_tool=True),
                Turn("Send the itinerary as a PDF please.",
                     expect_tools=("generate_itinerary_pdf_tool",)),
            ],
        ),
        Journey(
            # Replicates the real customer chat of 13 Aug that exposed two bugs:
            # the agent re-asked for a flight time it had itself printed two
            # turns earlier, and it wrote a full 5-day plan in chat then
            # produced a PDF containing no itinerary at all.
            id="J6_real_customer_long",
            wants=("hotel", "flights", "transfers", "tours"),
            turns=[
                Turn("i want to know about the avaialbility of the Hotel Howard Johnson "
                     "for 4 nights form 1 december to 5 december for 2 ppl 1 room",
                     expect_tools=("search_hotels",), require_any_tool=True),
                Turn("i want to book flights for the same dates", require_any_tool=True),
                Turn("do we have pick up and drop options too", require_any_tool=True),
                Turn("do we have any premium pickup?"),
                Turn("ok what about tours available?", require_any_tool=True),
                Turn("i want to plan a detailed iternary for my tour for all days"),
                Turn("i want all the things i dont want to miss anything so plan "
                     "accordingly and also mention the timings too."),
                Turn("i am going with the cheapest Air India flight option"),
                Turn("so lock the iternary and also now tell me what is the total amount?"),
                Turn("also give me the pdf for all the data and what is my total amount?",
                     expect_tools=("generate_itinerary_pdf_tool",)),
            ],
        ),
        Journey(
            id="J5_hinglish",
            wants=("flights", "hotel", "visa"),
            turns=[
                Turn("Bhai Dubai jaana hai, 2 log, 1 se 5 December 2026, Hyderabad se",
                     require_any_tool=True),
                Turn("Hotel bhi dikha do aur visa ka bhi bata do", require_any_tool=True),
                Turn("Theek hai, PDF bhej do",
                     expect_tools=("generate_itinerary_pdf_tool",)),
            ],
        ),
    ]


# ---------------------------------------------------------------- runner
def run_journey(j: Journey, run: int, model: str | None) -> JourneyResult:
    agent = build_react_agent(
        surface="streamlit",
        checkpoint_store=build_in_memory_checkpoint(),
        model_override=model,
    )
    thread = f"journey_{j.id}_{run}_{int(time.time() * 1000) % 100000}"
    logs: list[TurnLog] = []
    pdf_attempt = 0
    pdf_asks = 0
    t_start = time.perf_counter()

    for i, turn in enumerate(j.turns, start=1):
        res = StreamResult()
        t0 = time.perf_counter()
        try:
            reply = "".join(stream_and_log(
                agent, surface="streamlit", thread_id=thread,
                user_message=turn.say, turn_number=i, result=res,
            ))
        except Exception as e:
            return JourneyResult(j.id, run, logs, False, 0, pdf_asks,
                                 time.perf_counter() - t_start, error=f"turn {i}: {e}")
        dt = time.perf_counter() - t0
        calls = extract_tool_calls(res.response) if res.response else []
        tools = [c.get("tool_name", "") for c in calls]

        problems: list[str] = []
        # Re-asking settled facts is the loudest UX failure.
        if i > 1:
            problems += _hits(reply, RE_ASK_PATTERNS)
        # "Should I look for X?" is only a FAILURE when no work was done this
        # turn. After a real search it is an upsell ("want a Desert Safari
        # too?"), which is good selling, not a stall. Grading those as failures
        # would push the agent away from offering anything.
        if not tools:
            problems += _hits(reply, DEFERRAL_PATTERNS)
        for pat in NOT_FOUND_PATTERNS:
            if re.search(pat, reply.lower()):
                problems.append("claimed nothing found")
                break
        if re.search(PHANTOM_SHARED, reply.lower()) and "shared" not in "".join(tools):
            problems.append("offered shared transfer (supplier has none)")
        # Only a failure if the data was never fetched at ALL. Answering from a
        # previous turn's results is correct behaviour (and is what the "ONE
        # search per request" rule asks for) — re-calling would just be slower.
        if turn.require_any_tool and not tools:
            already = {t for tl in logs for t in tl.tools}
            if not already:
                problems.append("no tool called on a turn that needed data")
        for want in turn.expect_tools:
            if want not in tools:
                problems.append(f"missing {want}")
                if want == "generate_itinerary_pdf_tool":
                    pdf_asks += 1

        if "generate_itinerary_pdf_tool" in tools and not pdf_attempt:
            pdf_attempt = i
            # The customer's complaint was not "no PDF" but "the PDF has no
            # itinerary in it" — a price table with the day-by-day plan missing,
            # right after the agent wrote that plan out in chat. Verify the
            # rendered document, not just that the tool fired.
            if not _pdf_has_day_plan():
                problems.append("PDF generated WITHOUT the day-by-day itinerary")

        logs.append(TurnLog(i, turn.say, reply, tools, dt, problems))

    # If the PDF never fired, ask once more the way a real user would.
    if j.expects_pdf and not pdf_attempt:
        res = StreamResult()
        t0 = time.perf_counter()
        try:
            reply = "".join(stream_and_log(
                agent, surface="streamlit", thread_id=thread,
                user_message="Please just generate the PDF now.",
                turn_number=len(j.turns) + 1, result=res,
            ))
            calls = extract_tool_calls(res.response) if res.response else []
            tools = [c.get("tool_name", "") for c in calls]
            if "generate_itinerary_pdf_tool" in tools:
                pdf_attempt = len(j.turns) + 1
            logs.append(TurnLog(len(j.turns) + 1, "Please just generate the PDF now.",
                                reply, tools, time.perf_counter() - t0,
                                [] if tools else ["PDF still not generated on retry"]))
        except Exception as e:
            logs.append(TurnLog(len(j.turns) + 1, "retry", "", [],
                                time.perf_counter() - t0, [f"retry failed: {e}"]))

    # Component coverage across the whole journey.
    tool_blob = ",".join(t for tl in logs for t in tl.tools)
    coverage_gaps = []
    want_tool = {
        "flights": "search_flights", "hotel": "search_hotels",
        "transfers": "transfer", "tours": "search_tours", "visa": "visa",
    }
    for w in j.wants:
        probe = want_tool.get(w, w)
        if probe not in tool_blob:
            coverage_gaps.append(f"never searched {w}")
    if coverage_gaps and logs:
        logs[-1].problems.extend(coverage_gaps)

    return JourneyResult(
        j.id, run, logs, pdf_attempt > 0, pdf_attempt, pdf_asks,
        time.perf_counter() - t_start,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5, help="total journeys to run")
    ap.add_argument("--model", default=None)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    pool = journeys()
    plan = [pool[i % len(pool)] for i in range(args.runs)]
    print(f"Running {len(plan)} customer journeys "
          f"({len({j.id for j in plan})} distinct)…\n")

    results: list[JourneyResult] = []
    for n, j in enumerate(plan, start=1):
        r = run_journey(j, run=(n - 1) // len(pool) + 1, model=args.model)
        results.append(r)
        pdf = f"turn {r.pdf_attempt}" if r.pdf_generated else "NEVER"
        status = "OK  " if r.clean and r.pdf_generated else "PROB"
        print(f"[{status}] {n:2d}/{len(plan)} {r.journey_id:26s} "
              f"{r.total_s:6.1f}s  turns={len(r.turns)}  pdf={pdf}")
        for tl in r.turns:
            for p in tl.problems:
                print(f"          turn {tl.idx}: {p}")
        if r.error:
            print(f"          ERROR {r.error}")

    print("\n" + "=" * 78)
    n = len(results)
    pdf_ok = sum(1 for r in results if r.pdf_generated)
    first_try = sum(1 for r in results if r.pdf_generated
                    and r.pdf_attempt <= len(next(j for j in pool if j.id == r.journey_id).turns))
    clean = sum(1 for r in results if not r.problems and not r.error)
    print(f"journeys            : {n}")
    print(f"PDF generated       : {pdf_ok}/{n}  ({100 * pdf_ok / n:.0f}%)")
    print(f"PDF within scripted : {first_try}/{n}  (no extra nag needed)")
    print(f"fully clean         : {clean}/{n}  ({100 * clean / n:.0f}%)")
    tot = [r.total_s for r in results]
    print(f"journey latency     : mean {statistics.mean(tot):.1f}s  "
          f"median {statistics.median(tot):.1f}s  max {max(tot):.1f}s")
    turn_lat = [tl.latency_s for r in results for tl in r.turns]
    print(f"per-turn latency    : mean {statistics.mean(turn_lat):.1f}s  "
          f"median {statistics.median(turn_lat):.1f}s  max {max(turn_lat):.1f}s")

    tally: dict[str, int] = {}
    for r in results:
        for p in r.problems:
            tally[p] = tally.get(p, 0) + 1
    if tally:
        print("\nproblems by frequency:")
        for p, c in sorted(tally.items(), key=lambda kv: -kv[1]):
            print(f"   {c:3d}x  {p}")
    else:
        print("\nno problems detected")

    if args.json:
        Path(args.json).write_text(json.dumps([{
            "journey": r.journey_id, "run": r.run, "total_s": round(r.total_s, 1),
            "pdf_generated": r.pdf_generated, "pdf_attempt": r.pdf_attempt,
            "problems": r.problems, "error": r.error,
            "turns": [{"idx": t.idx, "said": t.said, "tools": t.tools,
                       "latency_s": round(t.latency_s, 1), "problems": t.problems,
                       "reply": t.reply} for t in r.turns],
        } for r in results], indent=1), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
