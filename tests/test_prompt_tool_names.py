"""Every tool name cited in a prompt must exist in the registry.

The PDF "takes 2-3 tries" bug was this: the prompt (and the Streamlit button)
told the model to call `generate_itinerary_pdf`, but the registered tool is
`generate_itinerary_pdf_tool`. The model had to guess, and often didn't.

Three more had drifted the same way — `display_options`, `build_trip_schedule`
and `search_transfers` (which never existed under any name; the real tool is
`search_airport_transfer_dubai`). MCP tools strip their `_tool` suffix while the
plain agent tools keep it, so a prompt author has to know which convention each
tool follows. This test removes the guesswork.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_tools import ALL_TOOLS

_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"

# Backticked identifiers starting with a verb we use for tools. Data fields
# (price_per_adult_inr, hotel_name, ...) don't start with these, so they don't
# get swept in.
_TOOL_PREFIXES = (
    "search_", "get_", "list_", "lookup_", "display_", "build_", "apply_",
    "check_", "compose_", "generate_", "compute_", "resolve_", "collect_",
    "price_group", "sum_",
)
_BACKTICKED = re.compile(r"`([a-z][a-z0-9_]*)`")

# Known non-tool identifiers that match a tool prefix by coincidence.
# `check_in`/`check_out` are search_hotels ARGUMENTS, not tools; they only match
# because the prefix list includes "check_" for check_floor_tool.
_NOT_TOOLS = {"price_per_adult_inr", "check_in", "check_out"}


def _registered() -> set[str]:
    return {t.name for t in ALL_TOOLS}


def _cited(text: str) -> set[str]:
    return {
        name
        for name in _BACKTICKED.findall(text)
        if name.startswith(_TOOL_PREFIXES) and name not in _NOT_TOOLS
    }


@pytest.mark.parametrize(
    "prompt_path", sorted(_PROMPTS.glob("*.md")), ids=lambda p: p.name
)
def test_prompt_cites_only_real_tools(prompt_path: Path) -> None:
    unknown = sorted(_cited(prompt_path.read_text(encoding="utf-8")) - _registered())
    assert not unknown, (
        f"{prompt_path.name} references tools that are not registered: {unknown}. "
        f"The model cannot call these. Check the exact name in ALL_TOOLS — "
        f"plain agent tools keep the '_tool' suffix, MCP tools strip it."
    )


def test_streamlit_pdf_button_names_a_real_tool() -> None:
    """The PDF button injects a tool name into the conversation verbatim."""
    app = (
        Path(__file__).resolve().parent.parent / "surfaces" / "streamlit_app.py"
    ).read_text(encoding="utf-8")
    assert "generate_itinerary_pdf_tool" in app
    # The bare name must not appear as a standalone instruction to the model.
    assert "the generate_itinerary_pdf tool" not in app


def test_registry_has_no_duplicate_names() -> None:
    names = [t.name for t in ALL_TOOLS]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate tool names: {sorted(dupes)}"
