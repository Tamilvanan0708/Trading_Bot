"""
R-multiple distribution analysis.

R = (exit - entry) / risk for wins, and -1.0 for full stop-loss hits.
Normalising to R makes results comparable regardless of position size.
"""

import math


def percentile_sorted(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile of a sorted list (0-100)."""
    if not values:
        return 0.0
    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    k = (len(vals) - 1) * (pct / 100.0)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return round(vals[lo], 2)
    frac = k - lo
    return round(vals[lo] * (1 - frac) + vals[hi] * frac, 2)


def r_multiple_distribution(rs: list[float]) -> dict:
    """Distribution statistics of R-multiples."""
    if not rs:
        return {
            "count": 0, "mean_r": 0.0, "median_r": 0.0, "std_r": 0.0,
            "p5": 0.0, "p25": 0.0, "p50": 0.0, "p75": 0.0, "p95": 0.0,
            "positive_count": 0, "negative_count": 0, "positive_pct": 0.0,
            "best_r": 0.0, "worst_r": 0.0,
        }
    mean = sum(rs) / len(rs)
    std = math.sqrt(sum((r - mean) ** 2 for r in rs) / len(rs))
    pos = [r for r in rs if r > 0]
    return {
        "count": len(rs),
        "mean_r": round(mean, 2),
        "median_r": percentile_sorted(rs, 50),
        "std_r": round(std, 2),
        "p5": percentile_sorted(rs, 5),
        "p25": percentile_sorted(rs, 25),
        "p50": percentile_sorted(rs, 50),
        "p75": percentile_sorted(rs, 75),
        "p95": percentile_sorted(rs, 95),
        "positive_count": len(pos),
        "negative_count": len(rs) - len(pos),
        "positive_pct": round(len(pos) / len(rs) * 100.0, 2),
        "best_r": round(max(rs), 2),
        "worst_r": round(min(rs), 2),
    }