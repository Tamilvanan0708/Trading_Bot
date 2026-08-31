"""
Synthetic XAU/USD Historical Data Generator.
Produces realistic multi-timeframe compatible 15-minute OHLCV data.
"""

import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd


def generate_synthetic_xauusd(
    num_bars: int = 1500,
    start_price: float = 2650.0,
    start_time: datetime = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc),
    output_path: str = "data/raw/xauusd_15m_sample.csv",
) -> pd.DataFrame:
    """
    Generates synthetic realistic XAU/USD 15-minute price series with trend swings,
    Fibonacci retracements, and session volume spikes.
    """
    np.random.seed(42)

    timestamps = [start_time + timedelta(minutes=15 * i) for i in range(num_bars)]
    prices = [start_price]

    # Generate trending waves with pullbacks
    trend = 0.05
    for i in range(1, num_bars):
        # Change trend regime periodically
        if i % 120 == 0:
            trend = np.random.choice([0.12, -0.12, 0.08, -0.08, 0.0])

        # Gold volatility ~ $1.5 to $4.0 per 15m bar
        noise = np.random.normal(0, 1.2)
        change = trend + noise
        new_price = max(1800.0, prices[-1] + change)
        prices.append(new_price)

    data = []
    for i, ts in enumerate(timestamps):
        base_p = prices[i]
        bar_volatility = np.random.uniform(1.0, 3.5)

        # Higher volatility in London/NY overlaps (12:00 - 17:00 UTC)
        if 12 <= ts.hour <= 17:
            bar_volatility *= 1.6

        is_up = np.random.random() > 0.48
        if is_up:
            open_p = base_p - np.random.uniform(0.1, bar_volatility * 0.4)
            close_p = base_p + np.random.uniform(0.1, bar_volatility * 0.6)
        else:
            open_p = base_p + np.random.uniform(0.1, bar_volatility * 0.4)
            close_p = base_p - np.random.uniform(0.1, bar_volatility * 0.6)

        high_p = max(open_p, close_p) + np.random.uniform(0.2, bar_volatility * 0.5)
        low_p = min(open_p, close_p) - np.random.uniform(0.2, bar_volatility * 0.5)
        vol = int(np.random.uniform(500, 3000) * (2.0 if 12 <= ts.hour <= 17 else 1.0))

        data.append(
            {
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open": round(open_p, 2),
                "high": round(high_p, 2),
                "low": round(low_p, 2),
                "close": round(close_p, 2),
                "volume": vol,
            }
        )

    df = pd.DataFrame(data)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    df.to_csv(output_path, index=False)
    print(f"Generated {len(df)} synthetic XAU/USD 15M bars at {output_path}")
    return df


if __name__ == "__main__":
    generate_synthetic_xauusd()
