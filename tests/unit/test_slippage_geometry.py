"""
Regression tests: excessive slippage must never invert SL/TP geometry.

Root-cause: BACKTEST_SLIPPAGE_PCT defaulted to 1% ($40 on gold), which pushed
the entry past the take-profit, guaranteeing losses on every trade.
"""


from app.backtesting.engine import BacktestEngine
from app.config.settings import Settings
from app.core.constants import MarketBias, SignalDirection, SignalQuality, StrategyType
from app.signals.models import SignalPayload


def _signal() -> SignalPayload:
    return SignalPayload(
        instrument="XAUUSD", direction=SignalDirection.LONG,
        strategy=StrategyType.SMC, entry=4000.0, stop_loss=3990.0,
        take_profit_1=4015.0, take_profit_2=4025.0, take_profit_3=4040.0,
        risk_reward=2.5, confidence_score=85.0, signal_quality=SignalQuality.STRONG,
        market_bias=MarketBias.BULLISH, reasons=["test"],
    )


def _uptrend(n=250):
    from datetime import datetime, timedelta, timezone

    from app.data.models import Candle
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    price = 3900.0
    for i in range(n):
        ts = start + timedelta(minutes=15 * i)
        price += 1.0 + (i % 5) * 0.2
        candles.append(Candle(timestamp=ts, open=price - 1.0, high=price + 2.0,
                              low=price - 2.0, close=price + 0.5, volume=10.0))
    return candles


def test_default_slippage_is_realistic():
    """The default slippage must be a small fraction, not 1%."""
    s = Settings()
    assert s.BACKTEST_SLIPPAGE_PCT < 0.001  # well below 0.1%
    assert s.PAPER_SLIPPAGE_PCT < 0.001


def test_one_percent_slippage_inverts_geometry(monkeypatch):
    """With 1% slippage, a valid LONG signal becomes geometrically invalid."""
    engine = BacktestEngine(Settings(BACKTEST_SLIPPAGE_PCT=0.01))
    candles = _uptrend()
    skipped = []

    # Monkeypatch the signal engine to emit a fixed tradable signal, then count skips.
    real_run = engine.run

    def patched_run(candles_15m, **kwargs):
        return real_run(candles_15m, **kwargs)

    monkeypatch.setattr(
        engine.signal_engine, "generate_signal",
        lambda snapshot: _signal(),
    )
    result = engine.run(candles, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)
    # With 1% slippage the entry inflates to ~4040, past TP2 (4025) -> geometry
    # inverted -> trade must be skipped (no trades with broken geometry).
    for t in result.trades:
        if t.direction == SignalDirection.LONG:
            assert t.entry_price < t.take_profit_2, "Entry must stay below TP2"


def test_realistic_slippage_preserves_geometry(monkeypatch):
    """With 0.01% slippage, geometry stays valid and trades execute."""
    engine = BacktestEngine(Settings(BACKTEST_SLIPPAGE_PCT=0.0001))
    candles = _uptrend()
    monkeypatch.setattr(
        engine.signal_engine, "generate_signal",
        lambda snapshot: _signal(),
    )
    result = engine.run(candles, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)
    trades = [t for t in result.trades if t.exit_reason != "END_OF_BACKTEST"]
    assert trades, "Expected tradable trades with realistic slippage"
    for t in trades:
        if t.direction == SignalDirection.LONG:
            assert t.stop_loss < t.entry_price < t.take_profit_1 < t.take_profit_2 < t.take_profit_3


def test_geometry_validation_skips_inverted_trades(monkeypatch):
    """The geometry gate must skip trades whose costs invert the setup."""
    engine = BacktestEngine(Settings(BACKTEST_SLIPPAGE_PCT=0.02))  # extreme
    candles = _uptrend()
    monkeypatch.setattr(
        engine.signal_engine, "generate_signal",
        lambda snapshot: _signal(),
    )
    result = engine.run(candles, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)
    # No executed trade may have inverted geometry.
    for t in result.trades:
        if t.direction == SignalDirection.LONG:
            assert t.stop_loss < t.entry_price < t.take_profit_2