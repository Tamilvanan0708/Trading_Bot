# Trading Bot Strategies Architecture Guide

This project maintains **strictly 3 independent trading strategies** for XAU/USD gold trading:

---

## 1. 🎯 Fib Retracement
- **Strategy Name**: `Fib Retracement`
- **Internal Identifier**: `FIBONACCI_RETRACEMENT` / `RETRACEMENT_BOS_V1`
- **Core Strategy Files**:
  - `app/retracement/dual_engine.py`: Dual-Direction BOS Retracement Engine (Bullish & Bearish).
  - `app/retracement/multi_tf.py`: Multi-Timeframe Cascading Monitor.
  - `app/retracement/models.py`: Retracement setup, levels, and state models.
- **Key Characteristics**:
  - Pure structural Break of Structure (BOS) on swing fractals.
  - 3-Tranche Scaling System:
    - **L1** @ 0.618 (TP 1.000, SL 0.236)
    - **L2** @ 0.500 (TP 0.618, SL 0.236)
    - **L3** @ 0.382 (TP 0.618, SL 0.236)
  - Immediate Level Freeze on entry touch.

---

## 2. 💎 SMC with Fib
- **Strategy Name**: `SMC with Fib`
- **Internal Identifier**: `SMART_MONEY_CONCEPTS` / `SMC_WITH_FIB`
- **Core Strategy Files**:
  - `app/retracement/smc_fib_engine.py`: Smart Money Concepts + Fibonacci Confluence Engine.
  - `app/retracement/smc_fib_multi_tf.py`: SMC Multi-Timeframe Monitor.
- **Key Characteristics**:
  - Order Blocks (OB), Fair Value Gaps (FVG), and Liquidity Sweeps.
  - Golden Pocket @ 0.680 + Equilibrium @ 0.500.
  - High probability institutional zone confluence.

---

## 3. 📈 Fib Go with Trend
- **Strategy Name**: `Fib Go with Trend`
- **Internal Identifier**: `FIB_GO_WITH_TREND` / `FIB_TREND_V2`
- **Core Strategy Files**:
  - `app/retracement/fib_trend_engine.py`: 9 EMA & 21 EMA Trend Alignment + Rule 8 Breakout Engine.
  - `app/retracement/fib_trend_multi_tf.py`: Multi-Timeframe Monitor across [15M, 30M, 1H, 2H, 4H] with Single Active Trade Lock.
- **Key Characteristics**:
  - Dedicated Timeframes: **`15m`, `30m`, `1h`, `2h`, `4h`** (5m removed).
  - Trend Change (CHoCH) + 9/21 EMA crossover.
  - Swing 1 expansion wave completes.
  - 0.618 Golden Ratio touch (Rule 7).
  - Blue Trigger Line armed at touch candle's extreme (HIGH for Long, LOW for Short).
  - Next candle breakout executes trade (Rule 8).
  - Stop Loss: **`0.236` Fib Level**.
  - Take Profit: **Single Target at `1.618` Extension**.
  - **Single Active Trade Lock**: When one timeframe triggers a trade, all other 4 timeframes go into standby until the trade closes.

---

## Shared Infrastructure:
- `app/paper_trading/sync.py`: Centralized execution and Telegram alert dispatcher routing all 3 strategies.
- `app/api/routes/retracement.py`: FastAPI REST endpoints serving dashboards and charts for each strategy.
- `app/static/terminal/js/app.js`: Modern terminal frontend rendering charts and signal cards for each strategy.
