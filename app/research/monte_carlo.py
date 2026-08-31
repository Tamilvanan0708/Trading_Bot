"""
Monte Carlo simulation using historical trade R-multiples.

Resamples the historical trade sequence (preserving the R distribution,
including the -1.0 stop-loss floor) to estimate equity tail risk.

IMPORTANT: Monte Carlo does NOT predict the future.  It is a robustness /
risk-assessment tool built from the assumption that the historical R
distribution is representative, which may be false.
"""

import random
import statistics


def monte_carlo_simulation(
    rs: list[float],
    initial_balance: float = 10000.0,
    risk_percent: float = 1.0,
    simulations: int = 10000,
    seed: int | None = 42,
) -> dict:
    """Run `simulations` resampled equity paths from the given R values.

    Each trade risks `risk_percent` of the running balance (compounded),
    as in the live risk model.  Trades are resampled WITH replacement so the
    same extreme outcome can recur within a single path.  Returns distribution
    stats.
    """
    if not rs:
        return {
            "simulations": simulations, "median_equity": initial_balance,
            "p5_equity": initial_balance, "p95_equity": initial_balance,
            "median_drawdown_pct": 0.0, "p95_drawdown_pct": 0.0,
            "probability_of_ruin_pct": 0.0, "probability_negative_return_pct": 0.0,
            "longest_loss_streak": 0,
        }

    rng = random.Random(seed)
    final_equities = []
    drawdowns_pct = []
    negative_count = 0
    ruin_count = 0
    longest_streaks = []

    for _ in range(simulations):
        sequence = rng.choices(rs, k=len(rs))  # resample with replacement
        balance = initial_balance
        peak = initial_balance
        max_dd = 0.0
        streak = 0
        max_streak = 0
        for r in sequence:
            risk = balance * (risk_percent / 100.0)
            balance += risk * r
            if balance <= 0:
                ruin_count += 1
                balance = 0.0
                break
            peak = max(peak, balance)
            dd = (peak - balance) / peak * 100.0
            max_dd = max(max_dd, dd)
            if r > 0:
                streak = 0
            else:
                streak += 1
                max_streak = max(max_streak, streak)

        final_equities.append(balance)
        drawdowns_pct.append(max_dd)
        longest_streaks.append(max_streak)
        if balance < initial_balance:
            negative_count += 1

    final_equities.sort()
    drawdowns_pct.sort()
    n = len(final_equities)

    def pct_of(sorted_vals, p):
        k = int(round((len(sorted_vals) - 1) * p))
        return sorted_vals[k]

    return {
        "simulations": simulations,
        "median_equity": round(pct_of(final_equities, 0.5), 2),
        "p5_equity": round(pct_of(final_equities, 0.05), 2),
        "p95_equity": round(pct_of(final_equities, 0.95), 2),
        "median_drawdown_pct": round(pct_of(drawdowns_pct, 0.5), 2),
        "p95_drawdown_pct": round(pct_of(drawdowns_pct, 0.95), 2),
        "probability_of_ruin_pct": round(ruin_count / n * 100.0, 2),
        "probability_negative_return_pct": round(negative_count / n * 100.0, 2),
        "longest_loss_streak": int(statistics.median(longest_streaks)),
    }