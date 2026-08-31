"""
Bootstrap confidence intervals for key performance statistics.

Resamples trade outcomes with replacement using a reproducible seed and
reports 95% confidence intervals.  Bootstrap is an approximation and assumes
the sample is representative.
"""

import random


def bootstrap_confidence_intervals(
    values: list[float],
    samples: int = 10000,
    seed: int | None = 42,
    ci: float = 0.95,
) -> dict[str, dict[str, float]]:
    """Bootstrap CIs for win-rate, mean R, and median R.

    Args:
        values: R-multiples per trade.
        samples: number of bootstrap resamples.
        seed: reproducible random seed.
        ci: confidence level (0.95 => 2.5% / 97.5% percentiles).
    """
    if not values:
        return {"win_rate": {"lo": 0.0, "hi": 0.0, "point": 0.0, "n": 0},
                "mean_r": {"lo": 0.0, "hi": 0.0, "point": 0.0, "n": 0},
                "median_r": {"lo": 0.0, "hi": 0.0, "point": 0.0, "n": 0}}

    rng = random.Random(seed)
    n = len(values)
    alpha = (1.0 - ci) / 2.0

    def stats_of(resample: list[float]) -> tuple:
        win_rate = sum(1 for v in resample if v > 0) / len(resample) * 100.0
        mean_r = sum(resample) / len(resample)
        sorted_r = sorted(resample)
        n = len(sorted_r)
        if n % 2 == 0:
            median_r = (sorted_r[n // 2 - 1] + sorted_r[n // 2]) / 2.0
        else:
            median_r = sorted_r[n // 2]
        return win_rate, mean_r, median_r

    win_rates, mean_rs, median_rs = [], [], []
    for _ in range(samples):
        resample = [rng.choice(values) for _ in range(n)]
        wr, mr, med = stats_of(resample)
        win_rates.append(wr)
        mean_rs.append(mr)
        median_rs.append(med)

    def ci_of(dist: list[float]) -> dict[str, float]:
        dist = sorted(dist)
        lo = dist[int(round((len(dist) - 1) * alpha))]
        hi = dist[int(round((len(dist) - 1) * (1 - alpha)))]
        return {"lo": round(lo, 2), "hi": round(hi, 2), "point": round(sum(dist) / len(dist), 2), "n": samples}

    point_wr, point_mean, point_med = stats_of(values)
    return {
        "win_rate": {**ci_of(win_rates), "point": round(point_wr, 2), "n": samples},
        "mean_r": {**ci_of(mean_rs), "point": round(point_mean, 2), "n": samples},
        "median_r": {**ci_of(median_rs), "point": round(point_med, 2), "n": samples},
    }