"""Pytest fixtures. Factory functions live in tests/factories.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make project root importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def sample_currency_rates() -> dict[str, float]:
    return {
        "INR": 1.0,
        "AED": 23.0,
        "USD": 84.0,
        "EUR": 91.0,
        "GBP": 107.0,
        "SGD": 63.0,
    }


@pytest.fixture
def fake_today() -> str:
    return "2026-05-25"


@pytest.fixture(autouse=True)
def _hermetic_fx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never hit the live ROE network call: force the static
    configured FX rate so conversions are deterministic, and clear the cache.
    A test that wants to exercise the live path can unset this explicitly.
    """
    monkeypatch.setenv("FX_DISABLE_LIVE_ROE", "1")
    import fx

    fx.clear_fx_cache()


@pytest.fixture(autouse=True)
def _offline_city_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_city falls back to the supplier's autocomplete (and the visa
    tool to its country table) for any city not in cities.json. Tests stay
    offline: a test that wants the supplier path sets CITY_SUPPLIER_LOOKUP=1
    and mocks the call."""
    monkeypatch.setenv("CITY_SUPPLIER_LOOKUP", "0")
    import reference_data_loader

    reference_data_loader._SUPPLIER_CITIES.clear()


@pytest.fixture(autouse=True)
def _clear_settings_caches() -> None:
    """Make sure @lru_cache'd settings don't leak between tests."""
    from settings import clear_all_caches

    yield
    clear_all_caches()


@pytest.fixture(autouse=True)
def _clear_conversation_state() -> None:
    """The customer's words live in a ContextVar for the turn; clear them so
    one test's message cannot be read by the next."""
    from rules import set_conversation_text

    set_conversation_text("")
    yield
    set_conversation_text("")
