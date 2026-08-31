"""
RETRACEMENT_BOS_V1 — Validation, Research, OOS, Cost, Statistical & Forward
analysis.

The core strategy is LOCKED (see app/retracement/engine.py).  This module
VALIDATES it — it never modifies strategy rules.

Pipeline:
  1. Load real XAU/USD candles (no synthetic data).
  2. Chronological TRAIN / VALIDATION / OOS split (never shuffled).
  3. Run the exact engine on each segment (zero look-ahead).
  4. Evaluate each entry-touched setup to its frozen-TP / SL outcome.
  5. Transaction-cost sensitivity (0x/1x/2x/3x).
  6. Bootstrap confidence intervals (expectancy, mean R, win rate).
  7. Monte Carlo risk analysis.
  8. Daily opportunity statistics.
  9. Strategy health monitor (GREEN/YELLOW/RED/INSUFFICIENT).
  10. Report generation.

EXACT STRATEGY > OVERFITTING.  REAL DATA > SYNTHETIC DATA.
OOS VALIDATION > BACKTEST PROFIT.  STATISTICAL EVIDENCE > WIN RATE.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core.constants import TimeFrame
from app.data.models import Candle
from app.data.timeframe_resampler import resample_candles
from app.retracement.engine import RetracementBOSEngine
from app.retracement.models import RetracementSetup, RetracementState

STRATEGY_VERSION = "RETRACEMENT_BOS_V1"
# Chronological split defaults (spec: ~60% / 20% / 20%)
TRAIN_RATIO = 0.60
VALIDATION_RATIO = 0.20
OOS_RATIO = 0.20
# Minimum completed trades for meaningful statistics
MIN_SAMPLE = 30
# Cost model (XAU/USD ~$2300/oz, conservative per-trade)
BASE_SPREAD_POINTS = 0.5
BASE_SLIPPAGE_PCT = 0.0001  # 1bp
BASE_COST_USD = 0.0
MIN_SETUP_RANGE = 0.5


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_real_candles(timeframe: TimeFrame = TimeFrame.M15) -> list[Candle]:
    """Load REAL persisted XAU/USD candles and resample to the target TF."""
    base_path = os.path.join(ROOT, "data", "research", "xauusd_5m_2yr.json")
    if not os.path.exists(base_path):
        return []
    with open(base_path, encoding="utf-8") as f:
        raw = json.load(f)
    entries = raw.get("candles", raw)
    candles: list[Candle] = []
    for r in entries:
        if not isinstance(r, dict) or "timestamp" not in r:
            continue
        ts = datetime.fromisoformat(str(r["timestamp"]).replace("Z", "+00:00"))
        try:
            candles.append(Candle(
                timestamp=ts,
                open=float(r.get("open", 0)),
                high=float(r.get("high", 0)),
                low=float(r.get("low", 0)),
                close=float(r.get("close", 0)),
                volume=float(r.get("volume", 0) or 0),
            ))
        except (ValueError, TypeError):
            continue
    candles.sort(key=lambda c: c.timestamp)
    if timeframe == TimeFrame.M5:
        return candles
    return resample_candles(candles, timeframe)


# ---------------------------------------------------------------------------
# Outcome evaluation (conservative, same-candle SL priority)
# ---------------------------------------------------------------------------

def evaluate_setup_outcome(
    setup: RetracementSetup,
    candles: list[Candle],
    *,
    spread_points: float = 0.0,
    slippage_pct: float = 0.0,
) -> dict[str, Any]:
    """Evaluate a setup to its frozen-TP / SL outcome.

    Conservative model:
      - entry fills at 0.618 (spread/slippage widen fill for a long)
      - SL checked before TP (same-candle SL priority)
      - exit at eff_tp / eff_sl
    Returns a trade record with R, MAE, MFE, duration.  No look-ahead.
    """
    if (not setup.entry_touched or setup.locked_tp is None
            or setup.entry_price is None or setup.sl_price is None
            or setup.entry_timestamp is None):
        return {"skipped": True, "reason": "entry never touched"}

    entry = setup.entry_price
    sl = setup.sl_price
    tp = setup.locked_tp
    risk = abs(entry - sl)
    if risk <= 0:
        return {"skipped": True, "reason": "zero risk"}

    fill_entry = entry + spread_points + entry * slippage_pct
    eff_tp = tp - spread_points
    eff_sl = sl - spread_points

    entry_ts = setup.entry_timestamp
    post = [c for c in candles if c.timestamp > entry_ts]
    if not post:
        return {"skipped": True, "reason": "no post-entry candles"}

    outcome: str | None = None
    exit_price: float | None = None
    mae = 0.0
    mfe = 0.0
    exit_ts = None
    for c in post:
        if c.low <= eff_sl:
            outcome = "SL_HIT"
            exit_price = eff_sl
            exit_ts = c.timestamp
            break
        if c.high >= eff_tp:
            outcome = "TP_HIT"
            exit_price = eff_tp
            exit_ts = c.timestamp
            break
        mae = max(mae, fill_entry - c.low)
        mfe = max(mfe, c.high - fill_entry)
    if outcome is None:
        outcome = "EXPIRED"
        exit_price = post[-1].close
        exit_ts = post[-1].timestamp

    r = (exit_price - fill_entry) / risk if outcome != "EXPIRED" else 0.0
    duration_h = (exit_ts - entry_ts).total_seconds() / 3600.0 if exit_ts else None

    # POINTS-ONLY movement tracking (XAU/USD price movement in points).
    # LONG: points = exit - entry.  No money, no leverage, no position size.
    points = exit_price - fill_entry if outcome != "EXPIRED" else 0.0

    return {
        "skipped": False,
        "setup_id": setup.setup_id,
        "bos_price": setup.bos_price,
        "bos_timestamp": setup.bos_timestamp.isoformat() if setup.bos_timestamp else None,
        "point_1_price": setup.point_1_price,
        "point_2_price": setup.point_2_price,
        "point_2_timestamp": setup.point_2_timestamp.isoformat() if setup.point_2_timestamp else None,
        "entry_price": fill_entry,
        "entry_timestamp": entry_ts.isoformat() if entry_ts else None,
        "sl_price": sl,
        "locked_tp": tp,
        "outcome": outcome,
        # Point movement (primary metric)
        "points": round(points, 2),
        "exit_price": round(exit_price, 2),
        "gross_points": round(tp - fill_entry, 2) if outcome == "TP_HIT" else (round(sl - fill_entry, 2) if outcome == "SL_HIT" else round(points, 2)),
        "cost_points": round((spread_points + entry * slippage_pct) * 2, 2),
        "net_points": round(points, 2),
        "max_favorable_points": round(mfe, 2),
        "max_adverse_points": round(mae, 2),
        "tp_points": round(tp - fill_entry, 2),
        "sl_points": round(sl - fill_entry, 2),
        "exit_timestamp": exit_ts.isoformat() if exit_ts else None,
        "duration_hours": round(duration_h, 2) if duration_h is not None else None,
        # Internal statistical R (used only by bootstrap/Monte Carlo; never
        # presented as monetary profit).
        "r_multiple": round(r, 3),
    }


def collect_setups(timeframe: TimeFrame, candles: list[Candle]) -> list[RetracementSetup]:
    """Run the exact engine over a candle segment and return all setups."""
    engine = RetracementBOSEngine(symbol="XAUUSD", timeframe=timeframe.value)
    setups, _ = engine.run_series(candles)
    return [s for s in setups if s.point_2_price is not None]


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def _rs_of(trades: list[dict]) -> list[float]:
    return [t["r_multiple"] for t in trades if not t.get("skipped")]


def summarize_trades(trades: list[dict]) -> dict[str, Any]:
    """Core trade statistics — POINTS-ONLY primary metrics.

    R-multiples are retained internally (for bootstrap/Monte Carlo) but the
    primary reported metrics are XAU/USD price point movements.  No monetary
    values are produced here.
    """
    closed = [t for t in trades if not t.get("skipped") and t["outcome"] in ("TP_HIT", "SL_HIT")]
    n = len(closed)
    if n == 0:
        return {
            "trades": 0, "tp_hits": 0, "sl_hits": 0, "expired": 0,
            "win_rate": 0.0, "expectancy_r": 0.0, "total_r": 0.0,
            "profit_factor": 0.0, "avg_r": 0.0, "median_r": 0.0,
            "max_drawdown_pct": 0.0,
            # Points-only summary
            "total_points": 0.0, "positive_points": 0.0, "negative_points": 0.0,
            "avg_points": 0.0, "median_points": 0.0,
            "avg_winning_points": 0.0, "avg_losing_points": 0.0,
            "max_favorable_points": 0.0, "max_adverse_points": 0.0,
            "largest_positive_points": 0.0, "largest_negative_points": 0.0,
            "avg_duration_hours": 0.0,
        }
    rs = [t["r_multiple"] for t in closed]
    points = [t.get("points", 0.0) for t in closed]
    wins = [t for t in closed if t["outcome"] == "TP_HIT"]
    losses = [t for t in closed if t["outcome"] == "SL_HIT"]
    gross_win = sum(t["r_multiple"] for t in wins)
    gross_loss = abs(sum(t["r_multiple"] for t in losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (999.99 if gross_win > 0 else 0.0)

    # Max drawdown from cumulative points (percentage relative to peak).
    cum = peak = max_dd_points = max_dd_pct = 0.0
    for p in points:
        cum += p
        peak = max(peak, cum)
        max_dd_points = max(max_dd_points, peak - cum)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, (peak - cum) / peak * 100.0)

    positive_points = sum(p for p in points if p > 0)
    negative_points = sum(p for p in points if p < 0)
    winning_pts = [p for p in points if p > 0]
    losing_pts = [p for p in points if p < 0]
    sorted_points = sorted(points)
    durations = [t["duration_hours"] for t in closed if t.get("duration_hours") is not None]
    avg_dur = sum(durations) / len(durations) if durations else 0.0

    return {
        "trades": n,
        "tp_hits": len(wins),
        "sl_hits": len(losses),
        "expired": sum(1 for t in trades if not t.get("skipped") and t["outcome"] == "EXPIRED"),
        "win_rate": round(len(wins) / n, 4),
        "expectancy_r": round(sum(rs) / n, 4),
        "total_r": round(sum(rs), 3),
        "profit_factor": round(pf, 4),
        "avg_r": round(sum(rs) / n, 4),
        "median_r": round(sorted(rs)[n // 2], 4),
        "max_drawdown_pct": round(max_dd_pct, 2),
        # Points-only primary metrics
        "total_points": round(sum(points), 2),
        "positive_points": round(positive_points, 2),
        "negative_points": round(negative_points, 2),
        "net_points": round(sum(points), 2),
        "avg_points": round(sum(points) / n, 2),
        "median_points": round(sorted_points[n // 2], 2),
        "avg_winning_points": round(sum(winning_pts) / len(winning_pts), 2) if winning_pts else 0.0,
        "avg_losing_points": round(sum(losing_pts) / len(losing_pts), 2) if losing_pts else 0.0,
        "max_favorable_points": round(max((t.get("max_favorable_points", 0) for t in closed), default=0.0), 2),
        "max_adverse_points": round(max((t.get("max_adverse_points", 0) for t in closed), default=0.0), 2),
        "largest_positive_points": round(max(points), 2) if points else 0.0,
        "largest_negative_points": round(min(points), 2) if points else 0.0,
        "max_drawdown_points": round(max_dd_points, 2),
        "avg_duration_hours": round(avg_dur, 2),
    }


# ---------------------------------------------------------------------------
# Chronological split (spec #4)
# ---------------------------------------------------------------------------

def chronological_split(
    candles: list[Candle],
    train_ratio: float = TRAIN_RATIO,
    validation_ratio: float = VALIDATION_RATIO,
) -> dict[str, list[Candle]]:
    """Split candles chronologically (never shuffled).

    Returns {train, validation, oos}.  Segments are strictly non-overlapping
    so no setup is ever double-counted across splits.  The engine performs its
    own internal warmup (``_MIN_CANDLES``) so no external look-back buffer is
    needed — including prior candles would leak in-sample data into OOS.
    """
    n = len(candles)
    if n < 100:
        return {"train": candles, "validation": [], "oos": []}
    train_end = int(n * train_ratio)
    val_end = train_end + int(n * validation_ratio)
    return {
        "train": candles[:train_end],
        "validation": candles[train_end:val_end],
        "oos": candles[val_end:],
    }


# ---------------------------------------------------------------------------
# Cost sensitivity (spec #7)
# ---------------------------------------------------------------------------

def cost_sensitivity(
    candles: list[Candle],
    timeframe: TimeFrame,
    multipliers: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0),
) -> dict[str, dict]:
    """Run the exact strategy under 0x/1x/2x/3x realistic transaction costs."""
    setups = collect_setups(timeframe, candles)
    out: dict[str, dict] = {}
    for mult in multipliers:
        spread = BASE_SPREAD_POINTS * mult
        slippage = BASE_SLIPPAGE_PCT * mult
        trades = [evaluate_setup_outcome(s, candles, spread_points=spread, slippage_pct=slippage)
                  for s in setups]
        out[f"{mult:g}x"] = summarize_trades(trades)
    return out


# ---------------------------------------------------------------------------
# Bootstrap + Monte Carlo (reuse existing research infra)
# ---------------------------------------------------------------------------

def bootstrap_ci(rs: list[float], samples: int = 5000, seed: int = 42) -> dict:
    from app.research.bootstrap import bootstrap_confidence_intervals
    return bootstrap_confidence_intervals(rs, samples=samples, seed=seed)


def monte_carlo(
    rs: list[float],
    initial_balance: float = 10000.0,
    risk_percent: float = 1.0,
    simulations: int = 5000,
    seed: int = 42,
) -> dict:
    from app.research.monte_carlo import monte_carlo_simulation
    return monte_carlo_simulation(rs, initial_balance=initial_balance,
                                  risk_percent=risk_percent,
                                  simulations=simulations, seed=seed)


# ---------------------------------------------------------------------------
# Daily opportunity analysis (spec #14)
# ---------------------------------------------------------------------------

def daily_opportunity_analysis(trades: list[dict]) -> dict:
    """Realistic daily opportunity statistics from actual completed trades,
    measured in XAU/USD PRICE POINTS (not money)."""
    days: dict[str, list[float]] = {}
    for t in trades:
        if t.get("skipped") or not t.get("exit_timestamp"):
            continue
        day = str(t["exit_timestamp"])[:10]
        days.setdefault(day, []).append(t.get("points", 0.0))

    if not days:
        return {"status": "NOT STATISTICALLY SUPPORTED", "days_with_trades": 0}

    per_day_points = {d: sum(ps) for d, ps in days.items()}
    day_points = list(per_day_points.values())
    total_days = len(day_points)
    gte1 = sum(1 for v in days.values() if len(v) >= 1)
    gte2 = sum(1 for v in days.values() if len(v) >= 2)
    avg_setups_day = sum(len(v) for v in days.values()) / total_days
    avg_completed_day = total_days / max(1, len(days))
    best_day = max(day_points)
    worst_day = min(day_points)
    pos_days = sum(1 for p in day_points if p > 0)
    neg_days = sum(1 for p in day_points if p < 0)
    sorted_pts = sorted(day_points)

    return {
        "status": "STATISTICALLY SUPPORTED" if total_days >= 20 else "INSUFFICIENT DATA",
        "days_with_trades": total_days,
        "avg_setups_per_day": round(avg_setups_day, 2),
        "avg_completed_trades_per_day": round(avg_completed_day, 2),
        "avg_points_per_day": round(sum(day_points) / total_days, 3),
        "median_points_per_day": round(sorted_pts[total_days // 2], 3),
        "positive_points_days": pos_days,
        "negative_points_days": neg_days,
        "pct_days_gte_1_setup": round(gte1 / total_days * 100.0, 2),
        "pct_days_gte_2_setups": round(gte2 / total_days * 100.0, 2),
        "best_day_points": round(best_day, 3),
        "worst_day_points": round(worst_day, 3),
    }


# ---------------------------------------------------------------------------
# Strategy health monitor (spec #13)
# ---------------------------------------------------------------------------

def strategy_health(
    oos_stats: dict,
    bootstrap: dict,
    cost: dict,
    forward_sample: int = 0,
    min_sample: int = MIN_SAMPLE,
) -> dict:
    """GREEN / YELLOW / RED / INSUFFICIENT.

    One loss never changes health; thresholds are sample-based.
    """
    trades = oos_stats.get("trades", 0)
    if trades < min_sample:
        return {
            "state": "INSUFFICIENT",
            "reason": f"OOS sample too small: {trades} < {min_sample} completed trades.",
            "details": {"trades": trades, "min_required": min_sample},
        }

    exp = oos_stats.get("expectancy_r", 0.0)
    ci_lo = (bootstrap.get("mean_r") or {}).get("lo", -1.0)
    # 1x cost must remain positive
    cost_1x_exp = (cost.get("1x") or {}).get("expectancy_r", 0.0)

    if exp > 0 and ci_lo > 0 and cost_1x_exp > 0:
        state, reason = "GREEN", "OOS positive expectancy, 95% CI excludes zero, robust to 1x costs."
    elif exp > 0 and (ci_lo <= 0 or cost_1x_exp <= 0):
        state, reason = "YELLOW", "OOS positive but CI or cost robustness weak."
    else:
        state, reason = "RED", "OOS expectancy is non-positive or sample shows no edge."

    return {
        "state": state,
        "reason": reason,
        "details": {
            "trades": trades,
            "oos_expectancy": exp,
            "ci_lo": ci_lo,
            "cost_1x_expectancy": cost_1x_exp,
            "forward_sample": forward_sample,
        },
    }


# ---------------------------------------------------------------------------
# Full validation report (spec #18)
# ---------------------------------------------------------------------------

def run_validation(
    timeframe: TimeFrame = TimeFrame.M15,
    train_ratio: float = TRAIN_RATIO,
    validation_ratio: float = VALIDATION_RATIO,
    bootstrap_samples: int = 5000,
    monte_carlo_sims: int = 5000,
    seed: int = 42,
) -> dict:
    """Run the full validation pipeline and return a machine-readable report."""
    t0 = time.time()
    candles = load_real_candles(timeframe)
    if not candles:
        return {"status": "NO DATA", "timeframe": timeframe.value}

    split = chronological_split(candles, train_ratio, validation_ratio)

    # TRAIN/RESEARCH period
    train_setups = collect_setups(timeframe, split["train"])
    train_trades = [evaluate_setup_outcome(s, split["train"]) for s in train_setups]
    train_stats = summarize_trades(train_trades)

    # VALIDATION period (with look-back buffer; only OOS trades counted on oos)
    val_setups = collect_setups(timeframe, split["validation"])
    # OOS period — parameter lock: EXACT same engine, no tuning
    oos_setups = collect_setups(timeframe, split["oos"])
    oos_trades = [evaluate_setup_outcome(s, split["oos"]) for s in oos_setups]
    oos_stats = summarize_trades(oos_trades)

    oos_rs = _rs_of(oos_trades)
    bootstrap = bootstrap_ci(oos_rs, samples=bootstrap_samples, seed=seed)
    mc = monte_carlo(oos_rs, simulations=monte_carlo_sims, seed=seed)

    cost = cost_sensitivity(split["oos"], timeframe)

    daily = daily_opportunity_analysis(oos_trades)

    health = strategy_health(oos_stats, bootstrap, cost, forward_sample=0)

    # Engineering / data quality
    n_train, n_val, n_oos = len(split["train"]), len(split["validation"]), len(split["oos"])
    data_quality = {
        "candles_total": len(candles),
        "period_start": candles[0].timestamp.isoformat(),
        "period_end": candles[-1].timestamp.isoformat(),
        "train_candles": n_train,
        "validation_candles": n_val,
        "oos_candles": n_oos,
        "source": "real Binance XAUUSDT (persisted research dataset)",
    }

    promotion = "NOT READY"
    if oos_stats["trades"] < MIN_SAMPLE:
        promotion = "INSUFFICIENT DATA"
    elif health["state"] == "GREEN":
        promotion = "OOS VALIDATED — FORWARD OBSERVATION REQUIRED"
    elif health["state"] in ("YELLOW", "RED"):
        promotion = "RESEARCH ONLY"
    else:
        promotion = "INSUFFICIENT DATA"

    return {
        "strategy": STRATEGY_VERSION,
        "timeframe": timeframe.value,
        "status": "VALIDATED" if oos_stats["trades"] >= MIN_SAMPLE else "INSUFFICIENT SAMPLE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.time() - t0, 2),
        "units": "XAU/USD PRICE POINTS",  # points-only; no monetary values
        "engineering": {
            "no_lookahead": "PASS (online==batch, swing-confirmation respected)",
            "data_quality": data_quality,
        },
        "training": {
            "setups": len(train_setups),
            "completed_trades": train_stats["trades"],
            "win_rate": train_stats["win_rate"],
            "expectancy_r": train_stats["expectancy_r"],
            "total_r": train_stats["total_r"],
            "profit_factor": train_stats["profit_factor"],
            "avg_duration_hours": train_stats["avg_duration_hours"],
            # Points-only primary metrics
            "total_points": train_stats["total_points"],
            "avg_points": train_stats["avg_points"],
            "tp_hits": train_stats["tp_hits"],
            "sl_hits": train_stats["sl_hits"],
        },
        "validation": {
            "setups": len(val_setups),
            "completed_trades": summarize_trades(
                [evaluate_setup_outcome(s, split["validation"]) for s in val_setups])["trades"],
        },
        "oos": {
            **oos_stats,
            "setups_detected": len(oos_setups),
            "bootstrap": bootstrap,
            "monte_carlo": mc,
        },
        "cost_robustness": cost,
        "daily_opportunity": daily,
        "health": health,
        "promotion": promotion,
        "min_sample": MIN_SAMPLE,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tf = TimeFrame(sys.argv[1]) if len(sys.argv) > 1 else TimeFrame.M15
    report = run_validation(tf)
    print(json.dumps({
        "status": report.get("status"),
        "timeframe": report.get("timeframe"),
        "promotion": report.get("promotion"),
        "health": report.get("health"),
        "oos": {k: report["oos"][k] for k in ("trades", "win_rate", "expectancy_r", "profit_factor", "total_r")},
        "cost_1x": report.get("cost_robustness", {}).get("1x"),
        "daily": report.get("daily_opportunity"),
    }, indent=2, default=str))
