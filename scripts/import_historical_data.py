"""
CLI for importing & validating real XAU/USD historical OHLCV data.

Usage:
  python scripts/import_historical_data.py --csv data/raw/mt5_xauusd_m15.csv --validate --output-dir data/processed
  python scripts/import_historical_data.py --mt5 --symbol XAUUSD --timeframe 15m --bars 5000 --validate
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from app.core.constants import TimeFrame
from app.data.ingestion import (
    build_multi_timeframe_dataset,
    ingest_candles_from_csv,
    validate_candles,
)


def _write_dataset(base_candles, output_dir: str) -> None:
    os.makedirs(output_dir, exist_ok=True)
    dataset = build_multi_timeframe_dataset(base_candles, TimeFrame.M15)
    for tf, candles in dataset.items():
        path = os.path.join(output_dir, f"XAUUSD_{tf.value}.json")
        with open(path, "w") as f:
            json.dump(
                [
                    {
                        "timestamp": c.timestamp.isoformat(),
                        "open": c.open,
                        "high": c.high,
                        "low": c.low,
                        "close": c.close,
                        "volume": c.volume,
                    }
                    for c in candles
                ],
                f,
                indent=2,
            )
        print(f"Wrote {len(candles)} {tf.value} candles -> {path}")


def import_from_mt5(symbol: str, timeframe: str, bars: int) -> list:
    from app.config.settings import get_settings
    from app.data.mt5_provider import MT5MarketDataProvider

    settings = get_settings()
    if not settings.MT5_ENABLED:
        print("MT5_ENABLED=false in .env; cannot connect to MetaTrader 5.")
        return []

    provider = MT5MarketDataProvider(
        symbol=symbol,
        login=settings.MT5_LOGIN,
        server=settings.MT5_SERVER,
        password=settings.MT5_PASSWORD,
        magic=settings.MT5_MAGIC,
        timezone_offset_minutes=settings.MT5_TZ_OFFSET_MINUTES,
    )
    print("Connecting to MetaTrader 5...")
    provider.connect()
    try:
        candles = asyncio.run(provider.get_ohlcv(symbol, TimeFrame(timeframe), limit=bars))
        print(f"Downloaded {len(candles)} {timeframe} candles for {symbol}.")
        return candles
    finally:
        provider.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import & validate XAU/USD historical data.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", type=str, help="Path to CSV file (timestamp,open,high,low,close[,volume]).")
    source.add_argument("--mt5", action="store_true", help="Import from MetaTrader 5.")

    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--timeframe", default="15m", choices=["15m", "30m", "1h", "4h", "1d"])
    parser.add_argument("--bars", type=int, default=5000, help="MT5: number of bars to fetch.")
    parser.add_argument("--validate", action="store_true", help="Run strict validation.")
    parser.add_argument("--strict-gaps", action="store_true", help="Treat missing candles as errors.")
    parser.add_argument("--output-dir", default="data/processed", help="Where to write resampled datasets.")
    args = parser.parse_args()

    if args.csv:
        candles = ingest_candles_from_csv(args.csv, TimeFrame(args.timeframe), validate=args.validate)
    elif args.mt5:
        candles = import_from_mt5(args.symbol, args.timeframe, args.bars)

    if not candles:
        print("No candles imported.")
        sys.exit(1)

    print(f"Imported {len(candles)} candles ({candles[0].timestamp} -> {candles[-1].timestamp}).")

    if args.validate:
        result = validate_candles(candles, TimeFrame(args.timeframe), strict_gaps=args.strict_gaps)
        print("-" * 50)
        print(f"Validation: {'PASS' if result.valid else 'FAIL'}")
        print(f"  Total candles:   {result.total_candles}")
        print(f"  Duplicates:      {result.duplicates}")
        print(f"  Out of order:    {result.out_of_order}")
        print(f"  Gaps:            {result.gaps}")
        print(f"  Invalid OHLC:    {result.invalid_ohlc}")
        print(f"  Warnings:        {len(result.warnings)}")
        for w in result.warnings[:5]:
            print(f"    - {w}")
        for e in result.errors[:5]:
            print(f"  ERROR: {e}")
        if not result.valid:
            sys.exit(2)

    _write_dataset(candles, args.output_dir)


if __name__ == "__main__":
    main()