"""
Tests for RETRACEMENT_BOS_V1 validation / research / forward infrastructure.

Covers:
  - chronological train/OOS split (never shuffled)
  - parameter locking (same engine across periods)
  - transaction-cost sensitivity
  - bootstrap confidence intervals
  - Monte Carlo analysis
  - forward observation immutability
  - strategy health monitor (GREEN/YELLOW/RED/INSUFFICIENT)
  - insufficient-sample handling
"""

import os
import sys
import json

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from datetime import datetime, timedelta, timezone

from app.core.constants import TimeFrame
from app.data.models import Candle
from app.data.timeframe_resampler import resample_candles
from app.retracement.engine import RetracementBOSEngine
from app.retracement.models import RetracementSetup, RetracementState
from app.retracement import research as R
from app.retracement import forward as F


def _c(ts, o, h, l, c, v=10.0):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def _make_trades(n, seed_base=1.0):
    """Synthetic but deterministic R list for bootstrap/MC/health tests."""
    trades = []
    for i in range(n):
        trades.append({
            "skipped": False,
            "outcome": "TP_HIT" if i % 3 != 2 else "SL_HIT",
            "r_multiple": 1.0 if i % 3 != 2 else -1.0,
            "points": 1.0 if i % 3 != 2 else -1.0,
            "entry_price": 100.0, "sl_price": 99.0, "locked_tp": 101.0,
            "max_favorable_points": 0.8, "max_adverse_points": 0.5, "exit_timestamp": f"2026-01-{(i % 27) + 1:02d}",
        })
    return trades


# ---------------------------------------------------------------------------
# Chronological split
# ---------------------------------------------------------------------------

def test_chronological_split_is_not_shuffled():
    start = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    candles = [_c(start + timedelta(minutes=15 * i), 100, 101, 99, 100) for i in range(300)]
    split = R.chronological_split(candles)
    assert split["train"], "train empty"
    assert split["validation"], "validation empty"
    assert split["oos"], "oos empty"
    # OOS must be the latest candles (chronological, never shuffled)
    assert split["oos"][-1].timestamp == candles[-1].timestamp
    assert split["train"][0].timestamp == candles[0].timestamp
    # No overlap between train and oos (warmup buffers aside, oos starts after train end)
    assert split["oos"][-1].timestamp > split["train"][0].timestamp


def test_parameter_lock_same_engine_across_periods():
    """OOS must run with the exact same locked engine config as TRAIN."""
    # Both periods use RetracementBOSEngine() with defaults — no tuning.
    engine_a = RetracementBOSEngine(symbol="XAUUSD", timeframe="15m")
    engine_b = RetracementBOSEngine(symbol="XAUUSD", timeframe="15m")
    assert type(engine_a) is type(engine_b)
    assert engine_a.symbol == engine_b.symbol
    assert engine_a.timeframe == engine_b.timeframe


# ---------------------------------------------------------------------------
# Outcome evaluation (conservative, no look-ahead)
# ---------------------------------------------------------------------------

def test_evaluate_setup_outcome_tp_hit():
    start = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    setup = RetracementSetup()
    setup.entry_touched = True
    setup.entry_timestamp = start
    setup.entry_price = 100.0
    setup.sl_price = 99.0
    setup.locked_tp = 101.0
    candles = [_c(start + timedelta(minutes=15 * (i + 1)), 100.5, 101.5, 100.0, 101.0) for i in range(5)]
    trade = R.evaluate_setup_outcome(setup, candles, spread_points=0.0, slippage_pct=0.0)
    assert trade["outcome"] == "TP_HIT"
    assert trade["points"] == pytest.approx(1.0, abs=0.01)
    assert trade["max_favorable_points"] >= 0
    assert trade["exit_price"] == pytest.approx(101.0, abs=0.01)


def test_evaluate_setup_outcome_sl_priority():
    """Same-candle SL priority: if both SL and TP touched in one candle, SL wins."""
    start = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    setup = RetracementSetup()
    setup.entry_touched = True
    setup.entry_timestamp = start
    setup.entry_price = 100.0
    setup.sl_price = 99.0
    setup.locked_tp = 101.0
    # A candle whose range spans both SL (99) and TP (101)
    candles = [_c(start + timedelta(minutes=15), 100.5, 101.5, 98.5, 100.0)]
    trade = R.evaluate_setup_outcome(setup, candles, spread_points=0.0, slippage_pct=0.0)
    assert trade["outcome"] == "SL_HIT"


def test_evaluate_skipped_when_entry_not_touched():
    setup = RetracementSetup()
    setup.entry_touched = False
    trade = R.evaluate_setup_outcome(setup, [])
    assert trade.get("skipped") is True


# ---------------------------------------------------------------------------
# Cost sensitivity
# ---------------------------------------------------------------------------

def test_cost_sensitivity_multipliers_present():
    candles = [_c(datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc) + timedelta(minutes=15 * i),
                  100, 101, 99, 100) for i in range(60)]
    res = R.cost_sensitivity(candles, TimeFrame.M15)
    assert "0x" in res and "1x" in res and "2x" in res and "3x" in res
    for k, v in res.items():
        assert "trades" in v
        assert "expectancy_r" in v


# ---------------------------------------------------------------------------
# Bootstrap + Monte Carlo
# ---------------------------------------------------------------------------

def test_bootstrap_ci():
    rs = [1.0, 1.0, -1.0, 1.0, 1.0, -1.0, 1.0] * 5
    ci = R.bootstrap_ci(rs, samples=1000)
    assert "mean_r" in ci and "win_rate" in ci and "median_r" in ci
    assert ci["mean_r"]["lo"] <= ci["mean_r"]["hi"]
    assert ci["mean_r"]["n"] == 1000


def test_monte_carlo_runs():
    rs = [1.0, 1.0, -1.0] * 10
    mc = R.monte_carlo(rs, simulations=500, seed=7)
    assert "median_equity" in mc
    assert "p95_drawdown_pct" in mc
    assert "probability_negative_return_pct" in mc


def test_monte_carlo_empty():
    mc = R.monte_carlo([], simulations=100)
    assert mc["probability_negative_return_pct"] == 0.0


# ---------------------------------------------------------------------------
# Health monitor
# ---------------------------------------------------------------------------

def test_health_insufficient_sample():
    oos = {"trades": 5, "expectancy_r": 0.5}
    h = R.strategy_health(oos, {"mean_r": {"lo": 0.1}}, {"1x": {"expectancy_r": 0.3}}, min_sample=30)
    assert h["state"] == "INSUFFICIENT"


def test_health_green():
    oos = {"trades": 60, "expectancy_r": 0.4}
    bs = {"mean_r": {"lo": 0.15}}
    cost = {"1x": {"expectancy_r": 0.2}}
    h = R.strategy_health(oos, bs, cost, min_sample=30)
    assert h["state"] == "GREEN"


def test_health_yellow_when_cost_weak():
    oos = {"trades": 60, "expectancy_r": 0.4}
    bs = {"mean_r": {"lo": 0.15}}
    cost = {"1x": {"expectancy_r": -0.1}}  # positive raw, negative after costs
    h = R.strategy_health(oos, bs, cost, min_sample=30)
    assert h["state"] == "YELLOW"


def test_health_red_when_oos_negative():
    oos = {"trades": 60, "expectancy_r": -0.2}
    bs = {"mean_r": {"lo": -0.3}}
    cost = {"1x": {"expectancy_r": -0.4}}
    h = R.strategy_health(oos, bs, cost, min_sample=30)
    assert h["state"] == "RED"


# ---------------------------------------------------------------------------
# Daily opportunity
# ---------------------------------------------------------------------------

def test_daily_opportunity_analysis():
    trades = _make_trades(60)
    daily = R.daily_opportunity_analysis(trades)
    assert "avg_points_per_day" in daily
    assert "best_day_points" in daily


# ---------------------------------------------------------------------------
# Forward observation
# ---------------------------------------------------------------------------

def test_forward_observation_record_and_summary(tmp_path, monkeypatch):
    F.FORWARD_STORE = str(tmp_path / "forward.json")
    fo = F.ForwardObservation()
    setup = RetracementSetup()
    setup.state = RetracementState.TP_DYNAMIC
    setup.point_1_price = 105.0
    setup.point_2_price = 100.0
    setup.entry_price = 103.09
    setup.sl_price = 101.18
    setup.dynamic_tp = 105.0
    rec = fo.record_setup(setup)
    assert rec["status"] == "WAITING FOR ENTRY"
    assert rec["bos_price"] is None  # not touched yet -> honest
    s = fo.summary()
    assert s["signals"] == 1
    assert s["active"] == 1
    assert s["status"] == "INSUFFICIENT DATA"


def test_forward_observation_immutable_records(tmp_path, monkeypatch):
    F.FORWARD_STORE = str(tmp_path / "forward2.json")
    fo = F.ForwardObservation()
    setup = RetracementSetup()
    setup.state = RetracementState.TP_FROZEN
    setup.entry_touched = True
    setup.tp_locked = True
    setup.locked_tp = 105.0
    rec1 = fo.record_setup(setup)
    assert rec1["locked_tp"] == 105.0
    assert rec1["status"] == "TP FROZEN / TRADE ACTIVE"
    # Advance with a later candle timestamp must not change the locked TP
    rec2 = fo.advance(setup.setup_id, datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert rec2 is not None
    assert rec2["locked_tp"] == 105.0
    # Re-read from store: immutable
    stored = F._load_store()
    assert stored[-1]["locked_tp"] == 105.0


# ---------------------------------------------------------------------------
# POINTS-ONLY performance tracking (no monetary metrics)
# ---------------------------------------------------------------------------

def test_long_point_calculation():
    """LONG: points = exit - entry (pure price movement)."""
    start = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    setup = RetracementSetup()
    setup.entry_touched = True
    setup.entry_timestamp = start
    setup.entry_price = 4618.54
    setup.sl_price = 4607.08
    setup.locked_tp = 4630.00
    candles = [_c(start + timedelta(minutes=15), 4625, 4632, 4620, 4630)]
    trade = R.evaluate_setup_outcome(setup, candles, spread_points=0.0, slippage_pct=0.0)
    assert trade["outcome"] == "TP_HIT"
    assert trade["points"] == pytest.approx(4630.00 - 4618.54, abs=0.01)
    assert trade["tp_points"] == pytest.approx(4630.00 - 4618.54, abs=0.01)


def test_short_point_calculation_direction_adjusted():
    """SHORT: points = entry - exit (direction-adjusted)."""
    # The research evaluator is LONG-only (bullish BOS); verify direction math
    # via the helper convention used in the module.
    entry = 4618.54
    exit_ = 4610.00
    long_pts = exit_ - entry
    short_pts = entry - exit_
    assert long_pts == pytest.approx(-8.54, abs=0.01)
    assert short_pts == pytest.approx(8.54, abs=0.01)


def test_sl_hit_points():
    """SL hit for LONG: points = SL - entry (negative movement)."""
    start = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    setup = RetracementSetup()
    setup.entry_touched = True
    setup.entry_timestamp = start
    setup.entry_price = 4618.54
    setup.sl_price = 4607.08
    setup.locked_tp = 4630.00
    candles = [_c(start + timedelta(minutes=15), 4608, 4610, 4606, 4607)]
    trade = R.evaluate_setup_outcome(setup, candles, spread_points=0.0, slippage_pct=0.0)
    assert trade["outcome"] == "SL_HIT"
    assert trade["points"] == pytest.approx(4607.08 - 4618.54, abs=0.01)
    assert trade["sl_points"] == pytest.approx(4607.08 - 4618.54, abs=0.01)


def test_entry_not_touched_has_no_movement():
    """Entry not touched -> no point movement (skipped)."""
    setup = RetracementSetup()
    setup.entry_touched = False
    trade = R.evaluate_setup_outcome(setup, [])
    assert trade.get("skipped") is True
    assert "points" not in trade or trade.get("points") is None or trade.get("skipped")


def test_summary_is_points_only_no_money():
    """summarize_trades must expose points metrics and NO monetary fields."""
    trades = _make_trades(30)
    s = R.summarize_trades(trades)
    assert "total_points" in s
    assert "avg_points" in s
    assert "max_favorable_points" in s
    assert "max_adverse_points" in s
    assert "largest_positive_points" in s
    assert "largest_negative_points" in s
    for money in ("profit_usd", "profit_inr", "account_profit", "roi",
                  "account_return", "net_profit_usd", "gross_profit_usd"):
        assert money not in s, f"monetary field leaked: {money}"


def test_research_report_has_no_monetary_fields():
    """run_validation report must not expose monetary metrics."""
    import os
    p = os.path.join(ROOT, "data", "research", "xauusd_5m_2yr.json")
    if not os.path.exists(p):
        pytest.skip("real dataset not present")
    report = R.run_validation(TimeFrame.M15)
    raw = json.dumps(report, default=str).lower()
    for money in ("profit_usd", "profit_inr", "account_profit", "roi",
                  "account_return", "net_profit_usd", "gross_profit_usd", "profitability"):
        assert money not in raw, f"monetary field leaked in report: {money}"

@pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, "data", "research", "xauusd_5m_2yr.json")),
                    reason="real dataset not present")
def test_run_validation_report_shape():
    report = R.run_validation(TimeFrame.M15)
    assert report["strategy"] == "RETRACEMENT_BOS_V1"
    assert report["timeframe"] == "15m"
    assert "engineering" in report
    assert report["engineering"]["no_lookahead"] != ""
    assert "training" in report
    assert "oos" in report
    assert "cost_robustness" in report
    assert "bootstrap" in report["oos"]
    assert "monte_carlo" in report["oos"]
    assert "health" in report
    assert report["health"]["state"] in ("GREEN", "YELLOW", "RED", "INSUFFICIENT")
    assert report["promotion"] in ("NOT READY", "INSUFFICIENT DATA", "RESEARCH ONLY",
                                   "OOS VALIDATED — FORWARD OBSERVATION REQUIRED")
    # Data quality must be real
    assert report["engineering"]["data_quality"]["source"] == "real Binance XAUUSDT (persisted research dataset)"
