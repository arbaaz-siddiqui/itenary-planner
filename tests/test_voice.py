"""Tests for the voice surface — prompt wiring, speech formatter, and the
/voice bridge endpoint (with the agent mocked, so no live LLM/network)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent import load_system_prompt
from surfaces import voice_app
from surfaces.voice_app import VoiceTurn, app, format_for_voice, thread_id_for_session


# =============================================================================
# Prompt wiring
# =============================================================================
def test_voice_surface_loads_voice_addendum() -> None:
    prompt = load_system_prompt(surface="voice")
    # The voice addendum's distinctive guidance must be present.
    assert "PHONE CALL" in prompt or "read aloud" in prompt
    assert "Surface: voice" in prompt


def test_other_surfaces_do_not_get_voice_addendum() -> None:
    web = load_system_prompt(surface="streamlit")
    assert "read aloud" not in web


# =============================================================================
# format_for_voice — the TTS safety net
# =============================================================================
class TestFormatForVoice:
    def test_strips_markdown_emphasis(self) -> None:
        out = format_for_voice("The **Rove Downtown** is _great_ and `cheap`.")
        assert "*" not in out and "_" not in out and "`" not in out
        assert "Rove Downtown" in out

    def test_strips_headings_and_bullets(self) -> None:
        out = format_for_voice("## Hotels\n- Rove Downtown\n- Citymax")
        assert "#" not in out
        assert "- " not in out
        assert "Rove Downtown" in out and "Citymax" in out

    def test_removes_urls(self) -> None:
        out = format_for_voice("Download it at https://example.com/itinerary.pdf now.")
        assert "http" not in out
        assert "example.com" not in out

    def test_drops_table_rows(self) -> None:
        text = "Here are options.\n| Hotel | Price |\n| --- | --- |\n| Rove | 14000 |"
        out = format_for_voice(text)
        assert "|" not in out

    def test_collapses_whitespace_to_sentences(self) -> None:
        out = format_for_voice("Line one.\n\nLine two.")
        assert "\n" not in out
        assert "Line one" in out and "Line two" in out

    def test_empty_input(self) -> None:
        assert format_for_voice("") == ""
        assert format_for_voice(None) == ""  # type: ignore[arg-type]

    def test_ampersand_spoken(self) -> None:
        assert "and" in format_for_voice("flights & hotel")


# =============================================================================
# Session / thread resolution
# =============================================================================
class TestSessionResolution:
    def test_prefers_session_id(self) -> None:
        t = VoiceTurn(transcript="hi", session_id="s1", call_id="c1", phone="+91999")
        assert t.resolved_session() == "s1"

    def test_falls_back_to_call_id_then_conversation_then_phone(self) -> None:
        assert VoiceTurn(transcript="hi", call_id="c1").resolved_session() == "c1"
        assert VoiceTurn(transcript="hi", conversation_id="cv1").resolved_session() == "cv1"
        assert VoiceTurn(transcript="hi", phone="+9199").resolved_session() == "+9199"

    def test_anon_when_nothing_provided(self) -> None:
        assert VoiceTurn(transcript="hi").resolved_session() == "anon"

    def test_thread_id_namespaced_and_sanitized(self) -> None:
        assert thread_id_for_session("call abc/123").startswith("voice_")
        # phone-style input is normalized the same way wa threads are
        assert thread_id_for_session("whatsapp:+919876543210") == "voice_919876543210"
        # never collides with the WhatsApp namespace
        assert not thread_id_for_session("919876543210").startswith("wa_")


# =============================================================================
# /voice endpoint — agent mocked
# =============================================================================
@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with the planner agent replaced by a stub, so no LLM runs."""
    captured: dict[str, object] = {}

    class _StubAgent:
        pass

    def _fake_invoke(agent, *, surface, thread_id, user_message, turn_number=0):
        captured["surface"] = surface
        captured["thread_id"] = thread_id
        captured["user_message"] = user_message
        # Shaped like invoke()'s output; deliberately full of markup to prove
        # the endpoint runs it through format_for_voice.
        return {
            "messages": [
                _AIMessage("## Best option\n- **Rove Downtown** ~ 14000. See https://x.com/p.pdf")
            ]
        }

    monkeypatch.setattr(voice_app, "get_voice_agent", lambda: _StubAgent())
    monkeypatch.setattr(voice_app, "invoke_and_log", _fake_invoke)
    voice_app.app.state.captured = captured  # type: ignore[attr-defined]
    return TestClient(app)


class _AIMessage:
    """Minimal stand-in for a LangChain AIMessage (type + content)."""

    def __init__(self, content: str) -> None:
        self.type = "ai"
        self.content = content


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_voice_turn_returns_speakable_reply(client: TestClient) -> None:
    r = client.post("/voice", json={"transcript": "4 nights in Dubai for 2", "call_id": "call_42"})
    assert r.status_code == 200
    body = r.json()
    # The stub returned markup + a URL; the response must be cleaned.
    assert "*" not in body["reply"]
    assert "#" not in body["reply"]
    assert "http" not in body["reply"]
    assert "Rove Downtown" in body["reply"]
    assert body["session_id"] == "call_42"


def test_voice_turn_passes_voice_surface_and_thread(client: TestClient) -> None:
    client.post("/voice", json={"transcript": "hello", "call_id": "abc"})
    captured = client.app.state.captured  # type: ignore[attr-defined]
    assert captured["surface"] == "voice"
    assert captured["thread_id"] == "voice_abc"
    assert captured["user_message"] == "hello"


def test_empty_transcript_asks_to_repeat(client: TestClient) -> None:
    r = client.post("/voice", json={"transcript": "   ", "call_id": "abc"})
    assert r.status_code == 200
    assert "again" in r.json()["reply"].lower()


def test_agent_exception_returns_graceful_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a, **k):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(voice_app, "get_voice_agent", lambda: object())
    monkeypatch.setattr(voice_app, "invoke_and_log", _boom)
    r = TestClient(app).post("/voice", json={"transcript": "hi", "call_id": "x"})
    assert r.status_code == 200
    assert "snag" in r.json()["reply"].lower() or "team" in r.json()["reply"].lower()
