"""Heartbeats belong to SEARCHES, not to "the model is thinking".

The bug: the loop started filler at 2.5s on every slow turn, with no idea
whether a tool was running. A conversational turn ("kitne log hain?") calls no
tool and still took ~9s on a cold process, so the caller heard
"Sorry for delay, thoda sa wait aur kriyega" apologising for a search that never
happened.

The gate keys off `latest_http_seq()`, which increments the moment the booking
client issues a request — the planner's tool list only arrives after the turn is
already over, far too late to decide.

These tests replicate the loop's decision rule rather than driving a live
LiveKit session, so they stay fast and hermetic.
"""

from __future__ import annotations

import voice_livekit as VL


def _heartbeats_for(
    *, turn_duration: float, first_http_at: float | None, grace: float | None = None
) -> list[tuple[float, str]]:
    """Replay the loop's gate over a simulated turn on a virtual clock.

    Returns [(elapsed_s, phrase), ...] — exactly what the caller would hear.
    """
    grace = VL._NO_TOOL_GRACE_S if grace is None else grace
    spoken: list[tuple[float, str]] = []
    now = 0.0
    idx = 0
    while True:
        gap = VL._HEARTBEAT_GAPS[min(idx, len(VL._HEARTBEAT_GAPS) - 1)]
        now += gap
        if now >= turn_duration:
            break  # the answer landed first
        searching = first_http_at is not None and now >= first_http_at
        if not searching and now < grace:
            continue  # silent: no search in flight and still inside the grace window
        spoken.append((round(now, 1), VL._heartbeat_phrase(idx)))
        idx += 1
    return spoken


class TestConversationalTurnsStaySilent:
    def test_quick_question_says_nothing(self) -> None:
        """'kitne log hain?' — no tool, answers in ~3s. Used to speak at 2.5s."""
        assert _heartbeats_for(turn_duration=3.2, first_http_at=None) == []

    def test_typical_no_tool_turn_says_nothing(self) -> None:
        assert _heartbeats_for(turn_duration=5.0, first_http_at=None) == []

    def test_slow_no_tool_turn_eventually_reassures(self) -> None:
        """Silence is right for a normal reply, but not forever."""
        spoken = _heartbeats_for(turn_duration=12.0, first_http_at=None)
        assert spoken, "a very slow turn should still reassure the caller"
        assert spoken[0][0] >= VL._NO_TOOL_GRACE_S


class TestSearchTurnsAreCovered:
    def test_search_turn_gets_heartbeats(self) -> None:
        """A real 4-tool search must still be covered — that is the whole point."""
        spoken = _heartbeats_for(turn_duration=22.0, first_http_at=1.2)
        assert len(spoken) >= 4

    def test_first_heartbeat_is_prompt_once_searching(self) -> None:
        spoken = _heartbeats_for(turn_duration=22.0, first_http_at=1.2)
        assert spoken[0][0] == VL._HEARTBEAT_GAPS[0]

    def test_phrases_run_in_order(self) -> None:
        spoken = _heartbeats_for(turn_duration=22.0, first_http_at=1.2)
        assert [p for _, p in spoken][:4] == VL._HEARTBEATS

    def test_fast_search_finishes_before_any_filler(self) -> None:
        """A cached/fast search that beats the first gap should stay silent."""
        assert _heartbeats_for(turn_duration=2.0, first_http_at=0.3) == []


class TestHeartbeatPhrases:
    def test_single_pool(self) -> None:
        """_HEARTBEATS_LATE was removed; phrases #3 and #4 were unreachable
        before because idx>=2 switched to the late pool."""
        assert not hasattr(VL, "_HEARTBEATS_LATE")
        assert not hasattr(VL, "_HEARTBEATS_EARLY")

    def test_walks_in_order(self) -> None:
        assert [VL._heartbeat_phrase(i) for i in range(4)] == VL._HEARTBEATS

    def test_wraps_instead_of_repeating(self) -> None:
        """Sticking on the last phrase is what sounds like a stuck recording."""
        assert VL._heartbeat_phrase(4) == VL._HEARTBEATS[0]
        assert VL._heartbeat_phrase(5) == VL._HEARTBEATS[1]
