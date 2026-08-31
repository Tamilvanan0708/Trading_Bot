"""MAE/MFE forensics on real data."""
import sys
import time
from collections import Counter

sys.path.insert(0, "D:/trading_view")

from app.backtesting.engine import BacktestEngine  # noqa: E402
from app.core.constants import SignalDirection  # noqa: E402
from app.research.data_fetch import load_real_history  # noqa: E402

candles = load_real_history()
subset = candles[-1500:]
t0 = time.time()
engine = BacktestEngine()
result = engine.run(subset, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)
print(f"backtest {time.time()-t0:.0f}s trades={len(result.trades)}", file=sys.stderr)

rows = []
for t in result.trades:
    if not t.exit_time:
        continue
    risk = max(0.01, abs(t.entry_price - t.stop_loss))
    mae = mfe = 0.0
    for c in subset:
        if c.timestamp <= t.entry_time:
            continue
        if t.exit_time and c.timestamp > t.exit_time:
            break
        if t.direction == SignalDirection.LONG:
            adv = t.entry_price - c.low
            fav = c.high - t.entry_price
        else:
            adv = c.high - t.entry_price
            fav = t.entry_price - c.low
        mae = max(mae, adv)
        mfe = max(mfe, fav)
    rows.append({"exit": t.exit_reason, "mae_r": mae / risk, "mfe_r": mfe / risk, "pnl_r": t.pnl_r})

with open("data/research/mae_mfe.txt", "w") as f:
    f.write(f"trades: {len(rows)}\n")
    f.write("exits: " + str(dict(Counter(r["exit"] for r in rows))) + "\n")
    for thr in [1.0, 1.5, 2.0, 2.5]:
        n = sum(1 for r in rows if r["mfe_r"] >= thr)
        f.write(f"MFE>={thr}R: {n}/{len(rows)} ({n/max(1,len(rows))*100:.0f}%)\n")
    n = sum(1 for r in rows if r["mae_r"] >= 1.0)
    f.write(f"MAE>=1R (SL touched): {n}/{len(rows)} ({n/max(1,len(rows))*100:.0f}%)\n")
    rt = [r for r in rows if r["mfe_r"] >= 2.5]
    f.write(f"reached TP2(2.5R) at some point: {len(rt)}/{len(rows)}\n")
    both = [r for r in rows if r["mae_r"] >= 1.0 and r["mfe_r"] >= 2.5]
    f.write(f"both SL and TP2 touched: {len(both)}/{len(rows)}\n")
print("done")
