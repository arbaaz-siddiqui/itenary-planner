"""Every tool ARGUMENT a prompt names must exist on that tool.

`test_prompt_tool_names.py` guards tool NAMES. Nothing guarded arguments, and
they had drifted badly in `system_prompt_voice.md`:

    documented                 real
    origin/destination         origin_city/destination_city
    checkin/checkout           check_in/check_out
    date                       travel_date
    hotel_id                   hotel_ids   (a list)
    nationality/destination    destination_country/nationality_country
                               + travel_date, which was not mentioned at all

A model following the prompt emits the documented name, the call fails
validation, and on a phone call the caller just hears silence. This test pins
the prompts to the real signatures.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_tools import ALL_TOOLS

_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"

# A tool row in a prompt table names the tool in backticks, then its arguments
# in backticks in the same row. We only inspect rows that name a real tool.
_BACKTICKED = re.compile(r"`\*?([a-z][a-z0-9_]*)`")

# Backticked words in a tool row that are values/notes, not argument names.
_NOT_ARGS = {
    "true",
    "false",
    "none",
    "null",
    # Result fields a prompt may cite while describing what to pass onward.
    "latitude",
    "longitude",
    "hotel_lat",
    "hotel_lng",
    "per_person_inr",
    "pricing_note",
    "transfer_type",
    "fare_lines",
    "pricing_available",
    "cabin_class_text",
    "amenities_matched",
    "budget_scope",
    "all_inclusive",
    "excludes_flights",
    "excludes_flights_and_hotel",
    "title",
    "start",
    "detail",
    "kind",
    "summary",
    "rate",
    "visa",
    "components",
    "day_plans",
    "hotel_name",  # both an arg and a result field; validated where required
}


def _tools_by_name() -> dict[str, object]:
    return {t.name: t for t in ALL_TOOLS}


def _arg_names(tool: object) -> set[str]:
    schema = tool.args_schema.model_json_schema() if tool.args_schema else {}
    return set(schema.get("properties", {}))


def _required_args(tool: object) -> set[str]:
    schema = tool.args_schema.model_json_schema() if tool.args_schema else {}
    return set(schema.get("required", []))


def _tool_rows(text: str) -> list[tuple[str, list[str]]]:
    """Markdown table rows that name exactly one registered tool.

    Returns (tool_name, other_backticked_tokens_in_that_row).
    """
    known = _tools_by_name()
    rows: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        tokens = _BACKTICKED.findall(line)
        named = [t for t in tokens if t in known]
        if len(named) != 1:
            continue  # zero tools, or a row comparing several — skip
        tool = named[0]
        others = [t for t in tokens if t != tool and t not in known]
        rows.append((tool, others))
    return rows


@pytest.mark.parametrize(
    "prompt_path", sorted(_PROMPTS.glob("*.md")), ids=lambda p: p.name
)
def test_documented_args_exist_on_the_tool(prompt_path: Path) -> None:
    known = _tools_by_name()
    bad: list[str] = []
    for tool_name, tokens in _tool_rows(prompt_path.read_text(encoding="utf-8")):
        valid = _arg_names(known[tool_name])
        for tok in tokens:
            if tok in _NOT_ARGS or tok in valid:
                continue
            # Only flag snake_case tokens — prose words aren't arguments.
            if "_" in tok:
                bad.append(f"{tool_name}: `{tok}` is not an argument (has: {sorted(valid)})")
    assert not bad, f"{prompt_path.name} documents arguments that do not exist:\n" + "\n".join(bad)


@pytest.mark.parametrize(
    "prompt_path", sorted(_PROMPTS.glob("*.md")), ids=lambda p: p.name
)
def test_required_args_are_documented(prompt_path: Path) -> None:
    """If a prompt row documents a tool's arguments at all, it must name every
    REQUIRED one — a silently-omitted required arg is exactly the get_visa_info
    `travel_date` bug (call rejected, caller hears nothing)."""
    known = _tools_by_name()
    bad: list[str] = []
    for tool_name, tokens in _tool_rows(prompt_path.read_text(encoding="utf-8")):
        valid = _arg_names(known[tool_name])
        documented = {t for t in tokens if t in valid}
        if not documented:
            continue  # row doesn't describe arguments; nothing to check
        missing = _required_args(known[tool_name]) - documented
        if missing:
            bad.append(f"{tool_name}: required arg(s) not documented: {sorted(missing)}")
    assert not bad, (
        f"{prompt_path.name} documents some args but omits required ones:\n" + "\n".join(bad)
    )


# Wrong arg names that contain NO underscore slip past the snake_case heuristic
# above (`nationality`, `destination`, `checkin`, `date`). Those are precisely
# the names the voice prompt used to carry, so pin the real ones explicitly.
_CRITICAL_ARGS = {
    "get_visa_info": {"destination_country", "nationality_country", "travel_date"},
    "search_flights": {"origin_city", "destination_city", "departure_date"},
    "search_hotels": {"destination_city", "check_in", "check_out"},
    "search_tours": {"destination_city", "travel_date"},
    "get_hotel_description": {"hotel_ids"},
    "search_airport_transfer_dubai": {"arrival_date"},
}


@pytest.mark.parametrize("tool_name,expected", sorted(_CRITICAL_ARGS.items()))
def test_critical_arg_names_unchanged(tool_name: str, expected: set[str]) -> None:
    """Pin the real signatures the prompts are written against.

    If a tool renames an argument, this fails and tells you to update the
    prompts — instead of the model silently emitting a name that no longer
    exists and the call failing at runtime.
    """
    tool = _tools_by_name()[tool_name]
    assert expected <= _arg_names(tool), (
        f"{tool_name} no longer accepts {sorted(expected - _arg_names(tool))}. "
        f"Update prompts/*.md to match the new signature."
    )


@pytest.mark.parametrize(
    "prompt_path", sorted(_PROMPTS.glob("*.md")), ids=lambda p: p.name
)
def test_no_known_wrong_arg_spellings(prompt_path: Path) -> None:
    """Ban the exact wrong spellings that shipped in the voice prompt.

    These have no underscore (or belong to another tool), so the generic
    heuristics can't catch them; a literal blocklist can.
    """
    text = prompt_path.read_text(encoding="utf-8")
    banned = {
        "`checkin`": "use `check_in`",
        "`checkout`": "use `check_out`",
        "`hotel_id`": "get_hotel_description takes `hotel_ids` (a list)",
        "`nationality_id`": "get_visa_info takes `nationality_country`",
    }
    hits = [f"{spelling} — {fix}" for spelling, fix in banned.items() if spelling in text]
    assert not hits, f"{prompt_path.name} uses a wrong argument spelling:\n" + "\n".join(hits)
