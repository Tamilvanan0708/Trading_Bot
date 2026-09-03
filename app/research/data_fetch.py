"""
Real XAU/USD historical data fetcher for research.

Pulls real Binance XAUUSDT 15M klines (paginated) and writes them to a JSON
file.  Data is validated and clearly labelled as REAL DATA.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from app.config.settings import Settings, get_settings
from app.core.constants import TimeFrame
from app.core.logging import logger
from app.data.ingestion import validate_candles
from app.data.live.binance_history import BinanceHistoryProvider
from app.data.models import Candle

_DATA_LABEL = "REAL DATA (Binance XAUUSDT futures)"


async def fetch_real_history(
    days: int = 180,
    output_path: str = "data/research/xauusd_15m_real.json",
    settings: Settings = None,
) -> list[Candle]:
    """Paginate Binance REST klines for the last `days` days of 15M data."""
    return await fetch_real_history_tf(
        days=days, timeframe=TimeFrame.M15, output_path=output_path, settings=settings
    )


def _tf_minutes(tf: TimeFrame) -> int:
    return {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}[tf.value]


async def fetch_real_history_tf(
    days: int = 180,
    timeframe: TimeFrame = TimeFrame.M5,
    output_path: str = "data/research/xauusd_5m_real.json",
    settings: Settings = None,
) -> list[Candle]:
    """Paginate Binance REST klines for the last `days` days of the given timeframe."""
    settings = settings or get_settings()
    provider = BinanceHistoryProvider(settings)
    step = _tf_minutes(timeframe)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    limit = 1000  # max per request

    all_candles: list[Candle] = []
    cursor = start
    max_iterations = 500
    iteration = 0
    while cursor < end:
        iteration += 1
        if iteration > max_iterations:
            logger.warning("fetch_real_history_tf: hit max iterations (%s) — stopping", max_iterations)
            break
        batch = await provider.get_ohlcv(
            "XAUUSD", timeframe, limit=limit,
            start_time=cursor, end_time=end,
        )
        if not batch:
            break
        all_candles.extend(batch)
        cursor = batch[-1].timestamp + timedelta(minutes=step)

    # Deduplicate + sort
    seen = {}
    for c in all_candles:
        seen[c.timestamp] = c
    candles = sorted(seen.values(), key=lambda c: c.timestamp)

    vresult = validate_candles(candles, timeframe, strict_gaps=False)
    if not vresult.valid:
        logger.warning("Fetched data has validation issues: %s", vresult.errors[:3])

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump({
            "data_label": _DATA_LABEL,
            "source": "fapi.binance.com",
            "symbol": "XAUUSDT",
            "timeframe": timeframe.value,
            "start": candles[0].timestamp.isoformat() if candles else None,
            "end": candles[-1].timestamp.isoformat() if candles else None,
            "candles": [
                {"timestamp": c.timestamp.isoformat(), "open": c.open, "high": c.high,
                 "low": c.low, "close": c.close, "volume": c.volume}
                for c in candles
            ],
        }, f)
    logger.info("Fetched %s real XAUUSDT %s candles (%s \u2192 %s) to %s",
                len(candles), timeframe.value, candles[0].timestamp, candles[-1].timestamp, output_path)
    return candles


def load_real_history(path: str = "data/research/xauusd_15m_real.json") -> list[Candle]:
    """Load a previously fetched real-history file."""
    with open(path) as f:
        payload = json.load(f)
    label = payload.get("data_label", "UNKNOWN")
    candles = [
        Candle(
            timestamp=datetime.fromisoformat(r["timestamp"]),
            open=float(r["open"]), high=float(r["high"]),
            low=float(r["low"]), close=float(r["close"]),
            volume=float(r.get("volume", 0.0)),
        )
        for r in payload["candles"]
    ]
    logger.info("Loaded %s candles from %s (%s)", len(candles), path, label)
    return candles