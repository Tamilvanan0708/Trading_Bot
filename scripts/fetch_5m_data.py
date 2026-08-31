"""Fetch real XAUUSDT 5M history for research (paginated)."""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.constants import TimeFrame
from app.research.data_fetch import fetch_real_history_tf


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--output", default="data/research/xauusd_5m_2yr.json")
    args = parser.parse_args()
    candles = asyncio.run(fetch_real_history_tf(
        days=args.days, timeframe=TimeFrame.M5, output_path=args.output
    ))
    print(f"Fetched {len(candles)} 5M candles to {args.output}")


if __name__ == "__main__":
    main()
