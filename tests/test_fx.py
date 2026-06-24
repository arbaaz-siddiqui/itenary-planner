"""Tests for fx.py — the live ROE rate map used by all parsers.

Supplier responses are priced in AED; we must convert at the CURRENT ROE, not a
stale constant. These tests cover the live path (mocked), the fallback, caching,
and the rate-map injection — without ever hitting the network.
"""

from __future__ import annotations

from typing import Any

import pytest

import fx


@pytest.fixture(autouse=True)
def _enable_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """fx tests exercise the live path, so undo conftest's hermetic disable."""
    monkeypatch.delenv("FX_DISABLE_LIVE_ROE", raising=False)
    fx.clear_fx_cache()
    yield
    fx.clear_fx_cache()


def _mock_roe(monkeypatch: Any, selling: float | None) -> dict[str, int]:
    """Patch the supplier ROE call; return a counter of how many times it ran."""
    calls = {"n": 0}

    def fake_call(*, target_currency: str = "INR") -> dict:
        calls["n"] += 1
        if selling is None:
            return {"result": {}}
        return {"result": {"sellingROE": selling, "buyingROE": 1.0 / selling}}

    import booking_api

    monkeypatch.setattr(booking_api, "call_currency_roe", fake_call)
    return calls


class TestLiveRate:
    def test_uses_live_selling_roe(self, monkeypatch: Any) -> None:
        _mock_roe(monkeypatch, 26.36)
        rate, source = fx.live_rate_to_inr("AED")
        assert rate == pytest.approx(26.36)
        assert source == "live_api"

    def test_inr_is_identity(self, monkeypatch: Any) -> None:
        _mock_roe(monkeypatch, 26.36)
        assert fx.live_rate_to_inr("INR") == (1.0, "manual_fallback")

    def test_falls_back_when_no_rate(self, monkeypatch: Any) -> None:
        _mock_roe(monkeypatch, None)  # ROE returns nothing usable
        rate, source = fx.live_rate_to_inr("AED")
        assert source == "manual_fallback"
        assert rate > 0  # configured AED rate

    def test_falls_back_when_call_raises(self, monkeypatch: Any) -> None:
        import booking_api

        def boom(*, target_currency: str = "INR") -> dict:
            raise RuntimeError("network down")

        monkeypatch.setattr(booking_api, "call_currency_roe", boom)
        rate, source = fx.live_rate_to_inr("AED")
        assert source == "manual_fallback"
        assert rate > 0

    def test_non_base_currency_uses_configured(self, monkeypatch: Any) -> None:
        """USD has no supplier ROE endpoint we trust → keep configured rate."""
        calls = _mock_roe(monkeypatch, 26.36)
        _rate, source = fx.live_rate_to_inr("USD")
        assert source == "manual_fallback"
        assert calls["n"] == 0  # no ROE call for non-base currency

    def test_disabled_skips_network(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("FX_DISABLE_LIVE_ROE", "1")
        calls = _mock_roe(monkeypatch, 26.36)
        _rate, source = fx.live_rate_to_inr("AED")
        assert source == "manual_fallback"
        assert calls["n"] == 0


class TestCache:
    def test_caches_within_ttl(self, monkeypatch: Any) -> None:
        calls = _mock_roe(monkeypatch, 26.36)
        fx.live_rate_to_inr("AED", _now=1000.0)
        fx.live_rate_to_inr("AED", _now=1000.0 + 60)  # within TTL
        assert calls["n"] == 1  # second call served from cache

    def test_refetches_after_ttl(self, monkeypatch: Any) -> None:
        calls = _mock_roe(monkeypatch, 26.36)
        fx.live_rate_to_inr("AED", _now=1000.0)
        fx.live_rate_to_inr("AED", _now=1000.0 + fx.ROE_CACHE_TTL_SECS + 1)
        assert calls["n"] == 2


class TestRateMap:
    def test_replaces_aed_with_live(self, monkeypatch: Any) -> None:
        _mock_roe(monkeypatch, 26.36)
        rates = fx.live_rate_map()
        assert rates["AED"] == pytest.approx(26.36)
        assert rates["INR"] == 1.0
        # Non-base currencies keep their configured value (not 26.36).
        assert rates["USD"] != pytest.approx(26.36)

    def test_map_falls_back_on_failure(self, monkeypatch: Any) -> None:
        import booking_api

        monkeypatch.setattr(
            booking_api,
            "call_currency_roe",
            lambda **_: (_ for _ in ()).throw(RuntimeError("down")),
        )
        rates = fx.live_rate_map()
        assert rates["AED"] > 0  # configured fallback, no crash


class TestSellingRoeExtraction:
    def test_selling_preferred(self) -> None:
        assert fx._selling_roe_from({"result": {"sellingROE": 26.0, "buyingROE": 0.04}}) == 26.0

    def test_reciprocal_of_buying(self) -> None:
        assert fx._selling_roe_from({"result": {"buyingROE": 0.04}}) == pytest.approx(25.0)

    def test_flat_rate(self) -> None:
        assert fx._selling_roe_from({"rate": 24.5}) == 24.5

    @pytest.mark.parametrize("raw", [{}, {"result": {}}, None, 5, "x"])
    def test_unusable_returns_none(self, raw: Any) -> None:
        assert fx._selling_roe_from(raw) is None
