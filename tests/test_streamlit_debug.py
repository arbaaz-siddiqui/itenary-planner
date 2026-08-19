"""Tests for the Streamlit debug-inspector helpers.

These pure helpers back the sidebar API/Tool Inspector that surfaces every tool
call's inputs and outputs (so hallucinations — numbers with no tool behind them —
are visible). We test the logic in isolation: importing the Streamlit module runs
its top-level UI body, so we exec only the helper function definitions.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_APP = Path(__file__).resolve().parent.parent / "surfaces" / "streamlit_app.py"
_HELPERS = {
    "_coerce_output",
    "_summarize_tc",
    "_call_status",
    "_status_code_emoji",
    "_mentions_price",
    "_last_api_turn",
    "_should_render_cards",
}


def _load_helpers() -> dict[str, Callable[..., Any]]:
    tree = ast.parse(_APP.read_text(encoding="utf-8"))
    def _keep(n: ast.stmt) -> bool:
        if isinstance(n, ast.FunctionDef) and n.name in _HELPERS:
            return True
        # Keep ONLY module-level `re.compile(...)` constants the helpers depend
        # on (e.g. PRICE_RE) — not other module globals like the renderer map.
        if (
            isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Attribute)
            and n.value.func.attr == "compile"
        ):
            return True
        # USER_DISPLAY_KEYWORDS — a plain tuple constant _should_render_cards needs.
        return (
            isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Tuple)
            and any(getattr(t, "id", "") == "USER_DISPLAY_KEYWORDS" for t in n.targets)
        )

    body: list[ast.stmt] = [n for n in tree.body if _keep(n)]
    module = ast.Module(body=body, type_ignores=[])
    ns: dict[str, Any] = {"json": json, "re": re, "Any": Any}
    exec(compile(module, str(_APP), "exec"), ns)  # trusted local source
    return ns


@pytest.fixture(scope="module")
def helpers() -> dict[str, Callable[..., Any]]:
    return _load_helpers()


class TestCoerceOutput:
    def test_dict_passthrough(self, helpers: dict[str, Any]) -> None:
        assert helpers["_coerce_output"]({"a": 1}) == {"a": 1}

    def test_json_string_parsed(self, helpers: dict[str, Any]) -> None:
        assert helpers["_coerce_output"]('{"a": 1}') == {"a": 1}

    def test_non_json_string_kept(self, helpers: dict[str, Any]) -> None:
        assert helpers["_coerce_output"]("not json") == "not json"

    def test_none_kept(self, helpers: dict[str, Any]) -> None:
        assert helpers["_coerce_output"](None) is None


class TestCallStatus:
    """The status badge must flag the precursors to hallucination loudly:
    errors (🔴) and empty result sets (🟡)."""

    def test_options_green(self, helpers: dict[str, Any]) -> None:
        emoji, label = helpers["_call_status"]({"output": {"options": [1, 2, 3]}})
        assert emoji == "🟢"
        assert label == "3 options"

    def test_zero_options_flagged_yellow(self, helpers: dict[str, Any]) -> None:
        emoji, label = helpers["_call_status"]({"output": {"options": []}})
        assert emoji == "🟡"
        assert label == "0 options"

    def test_error_flagged_red(self, helpers: dict[str, Any]) -> None:
        emoji, label = helpers["_call_status"](
            {"output": {"error": True, "error_type": "RateUnavailable"}}
        )
        assert emoji == "🔴"
        assert "RateUnavailable" in label

    def test_roe_live_green(self, helpers: dict[str, Any]) -> None:
        emoji, label = helpers["_call_status"](
            {"output": {"source": "live_api", "rate": 26.27, "base_currency": "AED"}}
        )
        assert emoji == "🟢"
        assert label == "live_api"

    def test_roe_fallback_yellow(self, helpers: dict[str, Any]) -> None:
        emoji, _label = helpers["_call_status"](
            {"output": {"source": "manual_fallback", "rate": 23.0, "base_currency": "AED"}}
        )
        assert emoji == "🟡"

    def test_json_string_output_gets_real_status(self, helpers: dict[str, Any]) -> None:
        """A JSON-string output (how LangChain often delivers results) must be
        coerced before judging status, not treated as opaque 'ok'."""
        emoji, label = helpers["_call_status"]({"output": '{"options": [1, 2]}'})
        assert emoji == "🟢"
        assert label == "2 options"


class TestSummarize:
    def test_options_count(self, helpers: dict[str, Any]) -> None:
        assert helpers["_summarize_tc"]({"output": {"options": [1, 2]}}) == "2 options"

    def test_error(self, helpers: dict[str, Any]) -> None:
        out = helpers["_summarize_tc"]({"output": {"error": True, "error_type": "X"}})
        assert out == "error: X"

    def test_roe_compact(self, helpers: dict[str, Any]) -> None:
        out = helpers["_summarize_tc"](
            {"output": {"source": "live_api", "rate": 26.27, "base_currency": "AED"}}
        )
        assert "26.27" in out and "AED" in out


class TestStatusCodeEmoji:
    """The HTTP status badge in the inspector's 'Actual API call' line."""

    def test_2xx_green(self, helpers: dict[str, Any]) -> None:
        assert helpers["_status_code_emoji"](200, None) == "🟢"

    def test_4xx_red(self, helpers: dict[str, Any]) -> None:
        assert helpers["_status_code_emoji"](404, None) == "🔴"

    def test_5xx_red(self, helpers: dict[str, Any]) -> None:
        assert helpers["_status_code_emoji"](503, None) == "🔴"

    def test_error_red_regardless_of_code(self, helpers: dict[str, Any]) -> None:
        assert helpers["_status_code_emoji"](None, "timeout") == "🔴"

    def test_none_white(self, helpers: dict[str, Any]) -> None:
        assert helpers["_status_code_emoji"](None, None) == "⚪"


class TestMentionsPrice:
    """Detects when a reply quotes a concrete ₹ amount — used to flag answers
    that state prices without any pricing tool call that turn."""

    @pytest.mark.parametrize(
        "text",
        ["Trip floor: ₹2,39,969", "₹104934 total", "cheapest is ₹ 1,08,683/adult"],
    )
    def test_detects_prices(self, helpers: dict[str, Any], text: str) -> None:
        assert helpers["_mentions_price"](text) is True

    @pytest.mark.parametrize("text", ["", "no numbers here", "just ₹5", "call me on +91 98"])
    def test_ignores_non_prices(self, helpers: dict[str, Any], text: str) -> None:
        assert helpers["_mentions_price"](text) is False


class TestLastApiTurn:
    """Finds the most recent turn (at/before a given turn) that hit an API, so
    a reuse banner can point to where the quoted numbers actually came from."""

    def _log(self) -> list[dict[str, Any]]:
        return [
            {"turn": 1, "n_api": 0},
            {"turn": 2, "n_api": 3},  # the floor-check that fetched prices
            {"turn": 3, "n_api": 0},  # reused numbers
            {"turn": 4, "n_api": 0},
        ]

    def test_points_to_prior_api_turn(self, helpers: dict[str, Any]) -> None:
        assert helpers["_last_api_turn"](self._log(), 3) == 2
        assert helpers["_last_api_turn"](self._log(), 4) == 2

    def test_none_when_no_prior_api(self, helpers: dict[str, Any]) -> None:
        assert helpers["_last_api_turn"](self._log(), 1) is None

    def test_includes_current_turn(self, helpers: dict[str, Any]) -> None:
        assert helpers["_last_api_turn"](self._log(), 2) == 2


class TestCardTrigger:
    """The card trigger phrase almost never starts the message.

    CARD_TRIGGER_RE was anchored with `^` but compiled WITHOUT re.MULTILINE, so
    it only matched at absolute position 0. The prompt tells the model to write
    "one short section per component" for a plan-everything turn, so the phrase
    lands partway down — and the richest turn in the product rendered no cards.
    """

    def test_phrase_at_start(self, helpers: dict[str, Any]) -> None:
        assert helpers["_should_render_cards"]("", "Here are the top 3 flights:")

    def test_phrase_after_lead_in(self, helpers: dict[str, Any]) -> None:
        text = "Got it — Delhi to Dubai.\n\nHere are the top 3 flights:"
        assert helpers["_should_render_cards"]("", text)

    def test_phrase_after_markdown_heading(self, helpers: dict[str, Any]) -> None:
        text = "**Flights**\nHere are the top 3 flights:"
        assert helpers["_should_render_cards"]("", text)

    def test_second_section_of_multi_component_reply(self, helpers: dict[str, Any]) -> None:
        """The plan-everything turn: several sections, each with its own trigger."""
        text = (
            "Here's the full plan.\n\n"
            "Here are the top 3 flights:\n- ...\n\n"
            "Here are the top 3 hotels:\n- ...\n"
        )
        assert helpers["_should_render_cards"]("", text)

    @pytest.mark.parametrize(
        "kind",
        ["flights", "hotels", "tours", "transfers", "restaurants", "visa options"],
    )
    def test_every_component_kind(self, helpers: dict[str, Any], kind: str) -> None:
        text = f"Sure.\n\nHere are the top 3 {kind}:"
        assert helpers["_should_render_cards"]("", text)

    def test_recommendation_does_not_trigger(self, helpers: dict[str, Any]) -> None:
        """Never render cards when recommending ONE option."""
        text = "I'd go with Emirates — 82,885 rupees, nonstop. Lock it?"
        assert not helpers["_should_render_cards"]("", text)

    def test_user_keyword_still_works(self, helpers: dict[str, Any]) -> None:
        assert helpers["_should_render_cards"]("show me hotels", "Sure thing.")
