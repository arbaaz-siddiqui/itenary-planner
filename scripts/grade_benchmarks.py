"""Grade ungraded rows in benchmark_results.xlsx using Claude.

Five dimensions per row (0-5):
- tool_accuracy, no_fabrication, multi_turn, style, overall

Run:
    python -m scripts.grade_benchmarks
    python -m scripts.grade_benchmarks --no-grade
    python -m scripts.grade_benchmarks --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from agent import configure_logging, get_logger
from settings import get_llm_settings

BENCHMARK_FILE = Path("benchmark_results.xlsx")

GRADING_PROMPT = """\
You are grading an AI travel agent's response. Score each dimension 0-5
where 0 = terrible and 5 = excellent.

User said:
{user_message}

Assistant replied:
{assistant_text}

Tools called: {tools_called}

Score these dimensions strictly. Return ONLY valid JSON, no preamble:

{{
    "tool_accuracy": <int 0-5>,
    "no_fabrication": <int 0-5>,
    "multi_turn": <int 0-5>,
    "style": <int 0-5>,
    "overall": <int 0-5>,
    "notes": "<brief justification, 1 sentence>"
}}
"""

log = get_logger("scripts.grade_benchmarks")


def grade_row(row: pd.Series) -> dict[str, int | str] | None:
    try:
        from langchain_anthropic import ChatAnthropic

        s = get_llm_settings()
        if not s.anthropic_api_key:
            log.warning("anthropic_key_missing_skip_grading")
            return None
        llm = ChatAnthropic(
            model_name="claude-3-5-sonnet-20241022",
            anthropic_api_key=s.anthropic_api_key,
            temperature=0,
            max_tokens_to_sample=512,
            timeout=60,
            stop=None,
        )
        prompt = GRADING_PROMPT.format(
            user_message=str(row.get("user_message", ""))[:1000],
            assistant_text=str(row.get("assistant_text", ""))[:2000],
            tools_called=str(row.get("tools_called", "")),
        )
        response = llm.invoke(prompt)
        text = response.content if isinstance(response.content, str) else str(response.content)
        text = text.strip().lstrip("```json").rstrip("```").strip()
        return json.loads(text)
    except Exception as e:
        log.warning("grade_row_failed", error=str(e))
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-grade", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    configure_logging(prod=False)

    if not BENCHMARK_FILE.exists():
        print(f"No benchmark file at {BENCHMARK_FILE}", file=sys.stderr)
        return 1

    df = pd.read_excel(BENCHMARK_FILE)
    ungraded_mask = df["grade_overall"].astype(str).str.strip().isin(["", "nan", "None"])
    ungraded = df[ungraded_mask]
    print(f"Total rows: {len(df)}; ungraded: {len(ungraded)}")

    if args.dry_run:
        print("DRY RUN — no changes written")
        return 0

    if not args.no_grade and len(ungraded) > 0:
        for idx, row in ungraded.iterrows():
            print(f"Grading row {idx}…")
            grades = grade_row(row)
            if grades is None:
                continue
            for col in ("tool_accuracy", "no_fabrication", "multi_turn", "style", "overall"):
                key = f"grade_{col}"
                if key in df.columns:
                    df.at[idx, key] = grades.get(col, "")
        df.to_excel(BENCHMARK_FILE, index=False)

    # Refresh summary
    summary: dict = {
        "total_turns": len(df),
        "graded": int((~ungraded_mask).sum()),
    }
    for col in ("tool_accuracy", "no_fabrication", "multi_turn", "style", "overall"):
        key = f"grade_{col}"
        if key in df.columns:
            numeric = pd.to_numeric(df[key], errors="coerce")
            mean_val = numeric.mean(skipna=True)
            summary[f"mean_{col}"] = round(mean_val if pd.notna(mean_val) else 0.0, 2)

    print("\nSummary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
