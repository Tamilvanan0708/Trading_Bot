"""
Walk-forward validation engine.

Splits data chronologically into TRAIN / VALIDATION / TEST windows and runs
the existing BacktestEngine on each segment.  No future information leaks
because the window boundaries are strictly increasing.
"""

from collections.abc import Callable
from datetime import datetime, timedelta

from app.backtesting.engine import BacktestEngine
from app.backtesting.models import BacktestResult
from app.data.models import Candle
from app.research.metrics import compute_extended_metrics


class WindowResult:
    """Performance of one walk-forward window (train / val / test)."""

    def __init__(
        self,
        window_idx: int,
        train_start: datetime,
        train_end: datetime,
        val_start: datetime,
        val_end: datetime,
        test_start: datetime | None = None,
        test_end: datetime | None = None,
    ):
        self.window_idx = window_idx
        self.train_start = train_start
        self.train_end = train_end
        self.val_start = val_start
        self.val_end = val_end
        self.test_start = test_start
        self.test_end = test_end
        self.train_result: BacktestResult | None = None
        self.val_result: BacktestResult | None = None
        self.test_result: BacktestResult | None = None
        self.train_metrics: dict = {}
        self.val_metrics: dict = {}
        self.test_metrics: dict = {}

    def to_dict(self) -> dict:
        return {
            "window": self.window_idx,
            "train": f"{self.train_start.date()} → {self.train_end.date()}",
            "val": f"{self.val_start.date()} → {self.val_end.date()}",
            "test": f"{self.test_start.date()} → {self.test_end.date()}" if self.test_start else None,
            "train_metrics": self.train_metrics,
            "val_metrics": self.val_metrics,
            "test_metrics": self.test_metrics,
        }


def split_windows(
    candles: list[Candle],
    train_months: int = 3,
    val_months: int = 1,
    test_months: int = 1,
    step_months: int = 1,
) -> list[tuple[datetime, datetime, datetime, datetime, datetime, datetime]]:
    """Yield chronological (train_start, train_end, val_start, val_end,
    test_start, test_end) tuples.

    Each window: train → val → test, then step forward.
    """
    if not candles:
        return []
    start = candles[0].timestamp
    end = candles[-1].timestamp
    windows = []
    cursor = start
    while True:
        train_end = cursor + timedelta(days=30 * train_months)
        val_end = train_end + timedelta(days=30 * val_months)
        test_end = val_end + timedelta(days=30 * test_months)
        if test_end > end:
            break
        windows.append((
            cursor, train_end,
            train_end, val_end,
            val_end, test_end,
        ))
        cursor += timedelta(days=30 * step_months)
    return windows


def _slice(candles: list[Candle], start: datetime, end: datetime) -> list[Candle]:
    """Return candles with timestamp in [start, end)."""
    return [c for c in candles if start <= c.timestamp < end]


def run_walk_forward(
    candles: list[Candle],
    train_months: int = 3,
    val_months: int = 1,
    test_months: int = 1,
    step_months: int = 1,
    warmup_bars: int = 150,
    initial_balance: float = 10000.0,
    risk_percent: float = 1.0,
    engine_factory: Callable[[], BacktestEngine] | None = None,
    tick_cost: dict = None,
    ai_validator: Callable | None = None,
) -> list[WindowResult]:
    """Run walk-forward validation over the full data set.

    Each window: train on TRAIN, validate on VAL, test on TEST (OOS).
    """
    tc = tick_cost or {}
    windows = split_windows(candles, train_months, val_months, test_months, step_months)
    results = []

    for idx, (tr_s, tr_e, va_s, va_e, te_s, te_e) in enumerate(windows):
        wr = WindowResult(idx, tr_s, tr_e, va_s, va_e, te_s, te_e)
        train_candles = _slice(candles, tr_s, tr_e)
        val_candles = _slice(candles, va_s, va_e)
        test_candles = _slice(candles, te_s, te_e)

        if len(train_candles) < warmup_bars + 10:
            continue

        engine = (engine_factory or BacktestEngine)()
        kw = dict(initial_balance=initial_balance, risk_percent=risk_percent,
                  warmup_bars=warmup_bars, **tc)
        if ai_validator is not None:
            kw["ai_validator"] = ai_validator

        # Train window
        try:
            wr.train_result = engine.run(train_candles, **kw)
            wr.train_metrics = compute_extended_metrics(wr.train_result.trades)
        except ValueError as exc:
            wr.train_metrics = {"error": str(exc)}

        # Validation window (separate engine → no parameter leakage)
        try:
            val_engine = (engine_factory or BacktestEngine)()
            kw2 = dict(initial_balance=initial_balance, risk_percent=risk_percent,
                       warmup_bars=warmup_bars, **tc)
            if ai_validator is not None:
                kw2["ai_validator"] = ai_validator
            wr.val_result = val_engine.run(val_candles, **kw2)
            wr.val_metrics = compute_extended_metrics(wr.val_result.trades)
        except ValueError as exc:
            wr.val_metrics = {"error": str(exc)}

        # Test window (OOS — completely separate)
        try:
            test_engine = (engine_factory or BacktestEngine)()
            kw3 = dict(initial_balance=initial_balance, risk_percent=risk_percent,
                       warmup_bars=warmup_bars, **tc)
            if ai_validator is not None:
                kw3["ai_validator"] = ai_validator
            wr.test_result = test_engine.run(test_candles, **kw3)
            wr.test_metrics = compute_extended_metrics(wr.test_result.trades)
        except ValueError as exc:
            wr.test_metrics = {"error": str(exc)}

        results.append(wr)

    return results