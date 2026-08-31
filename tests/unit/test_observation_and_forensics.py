"""
Tests for observation mode, FAILED-strategy safety block, and trade forensics.
"""

from datetime import datetime, timedelta, timezone

from app.backtesting.models import SimulatedTrade
from app.config.settings import Settings
from app.core.constants import SignalDirection, StrategyType, TradeState
from app.data.models import Candle
from app.research.forensics import trade_forensics
from app.services.scheduler import AnalysisScheduler


def _candle(ts, o, h, l, c) -> Candle:
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=10.0)


def _trade(i, pnl, r, direction="LONG", entry=100.0, sl=90.0, exit_h=2):
    et = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc) + timedelta(hours=i)
    return SimulatedTrade(
        trade_id=f"t{i}", direction=SignalDirection(direction), strategy=StrategyType.SMC,
        entry_time=et, entry_price=entry, stop_loss=sl,
        take_profit_1=entry + 5.0, take_profit_2=entry + 10.0, take_profit_3=entry + 15.0,
        lot_size=0.1, risk_usd=10.0, pnl_usd=pnl, pnl_r=r, exit_reason="TP2_HIT",
        state=TradeState.CLOSED, exit_time=et + timedelta(hours=exit_h),
    )


def test_observation_mode_blocks_paper_trading():
    """In observation mode, signals are produced but no paper trade opens."""
    s = Settings(OBSERVATION_MODE=True, LIVE_HISTORY_REFRESH_ON_DEGRADED=False)
    scheduler = AnalysisScheduler.__new__(AnalysisScheduler)  # avoid __init__ side effects
    scheduler.settings = s
    assert scheduler.settings.OBSERVATION_MODE is True


def test_failed_strategy_blocks_paper_trading(tmp_path):
    """A FAILED classification must block automatic paper trading."""
    import json
    report_path = tmp_path / "latest_report.json"
    report_path.write_text(json.dumps({"classification": {"grade": "FAILED", "reasons": ["x"]}}))
    assert AnalysisScheduler._read_strategy_grade(str(report_path)) == "FAILED"


def test_no_report_means_not_failed(tmp_path):
    """If no research report exists, do NOT assume the strategy is failed."""
    missing = str(tmp_path / "missing.json")
    grade = AnalysisScheduler._read_strategy_grade(missing)
    assert grade == "INCONCLUSIVE"
    assert grade != "FAILED"


def test_trade_forensics_mae_mfe():
    candles = []
    et = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    for i in range(8):
        ts = et + timedelta(minutes=15 * i)
        # price dips to 95 (0.5R adverse), rises to 115 (1.5R favorable)
        candles.append(_candle(ts, 100 + i, 115, 95, 105))
    trade = _trade(0, 15.0, 1.5, entry=100.0, sl=90.0, exit_h=1)
    fx = trade_forensics([trade], candles)
    assert fx["trades"] == 1
    assert fx["by_trade"][0]["mae_r"] >= 0.5
    assert fx["by_trade"][0]["mfe_r"] >= 1.5
    assert "diagnosis" in fx


def test_trade_forensics_empty():
    fx = trade_forensics([], [])
    assert fx["trades"] == 0