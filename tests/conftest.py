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
def _clear_settings_caches() -> None:
    """Make sure @lru_cache'd settings don't leak between tests."""
    from settings import clear_all_caches

    yield
    clear_all_caches()
