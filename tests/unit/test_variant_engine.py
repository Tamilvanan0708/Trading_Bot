"""
Tests for the VariantSignalEngine candidate wrapper.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.constants import MarketBias, SignalDirection, SignalQuality, StrategyType
from app.data.models import MultiTimeframeSnapshot
from app.research.variant_engine import VariantSignalEngine
from app.signals.models import SignalPayload


def _snapshot(price=4000.0):
    from app.data.models import Candle
    ts = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    candles = [Candle(timestamp=ts + timedelta(minutes=15 * i), open=4000.0, high=4001.0, low=3999.0, close=4000.0, volume=10.0) for i in range(20)]
    return MultiTimeframeSnapshot(symbol="XAUUSD", timestamp=ts, current_price=price, m15=candles, m30=candles, h1=candles, h4=candles)


def _base_signal():
    return SignalPayload(
        instrument="XAUUSD", direction=SignalDirection.LONG, strategy=StrategyType.SMC,
        entry=4000.0, stop_loss=3990.0, take_profit_1=4015.0, take_profit_2=4025.0,
        take_profit_3=4040.0, risk_reward=2.5, confidence_score=85.0,
        signal_quality=SignalQuality.STRONG, market_bias=MarketBias.BULLISH, reasons=["test"],
    )


class _FakeBase:
    def __init__(self, signal):
        self._signal = signal
        self._atr = 10.0

    def generate_signal(self, snapshot):
        return self._signal

    def _compute_atr(self, snapshot):
        return self._atr


def test_variant_sets_tp_1_25r():
    sig = _base_signal()  # risk = 10
    variant = VariantSignalEngine(_FakeBase(sig), tp_r=1.25)
    out = variant.generate_signal(_snapshot())
    assert out.take_profit_2 == pytest.approx(4000.0 + 1.25 * 10, abs=0.01)
    assert out.stop_loss == 3990.0  # base SL unchanged


def test_variant_sets_atr_sl():
    sig = _base_signal()
    variant = VariantSignalEngine(_FakeBase(sig), sl_atr=1.0)
    out = variant.generate_signal(_snapshot())
    assert out.stop_loss == pytest.approx(4000.0 - 10.0, abs=0.01)  # 1 ATR = 10


def test_variant_clamps_geometry():
    sig = _base_signal()
    variant = VariantSignalEngine(_FakeBase(sig), tp_r=0.5)
    out = variant.generate_signal(_snapshot())
    # TP clamped to >= entry for LONG
    assert out.take_profit_2 >= out.entry
    assert out.stop_loss <= out.entry


def test_variant_no_trade_confidence_filter():
    sig = _base_signal()
    variant = VariantSignalEngine(_FakeBase(sig), min_confidence=90.0)
    out = variant.generate_signal(_snapshot())
    assert out.direction == SignalDirection.NO_TRADE


def test_variant_no_trade_regime_filter():
    sig = _base_signal()
    class _Det:
        def analyze(self, candles):
            class R:
                regime = type("Regime", (), {"value": "RANGING"})()
            return R()
    variant = VariantSignalEngine(_FakeBase(sig), only_regime="HIGH_VOLATILITY", regime_detector=_Det())
    out = variant.generate_signal(_snapshot())
    assert out.direction == SignalDirection.NO_TRADE