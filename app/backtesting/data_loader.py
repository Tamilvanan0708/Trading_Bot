"""
Historical Data Loader for Strategy Backtesting.

Fetches and caches real historical Gold (XAUUSDT) OHLCV candles from Binance REST API
for any user-specified date range with automatic pagination and local disk caching.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from typing import List

import httpx

from app.core.logging import logger
from app.data.models import Candle

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CACHE_DIR = os.path.join(ROOT_DIR, "data", "research")

BINANCE_REST_BASE_URLS = [
    "https://fapi.binance.com",
    "https://fapi1.binance.com",
    "https://fapi2.binance.com",
    "https://fapi3.binance.com",
]

TF_INTERVAL_MAP = {
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "1d": "1d",
}


async def fetch_historical_candles(
    symbol: str,
    timeframe: str,
    start_dt: datetime,
    end_dt: datetime,
    use_cache: bool = True,
) -> list[Candle]:
    """Fetch historical candles for symbol and timeframe between start_dt and end_dt.

    Results are cached to disk so subsequent runs for the same range return instantaneously.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    tf_str = timeframe.lower()
    binance_tf = TF_INTERVAL_MAP.get(tf_str, "15m")
    binance_symbol = "XAUUSDT" if symbol.upper() in ("XAUUSD", "XAUUSDT") else symbol.upper()

    # Normalize datetimes to UTC
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    start_str = start_dt.strftime("%Y%m%d_%H%M")
    end_str = end_dt.strftime("%Y%m%d_%H%M")
    cache_path = os.path.join(CACHE_DIR, f"cache_{binance_symbol.lower()}_{tf_str}_{start_str}_{end_str}.json")

    # 1. Check local cache
    if use_cache and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            entries = raw.get("candles", raw)
            candles = []
            for r in entries:
                ts = datetime.fromisoformat(str(r["timestamp"]))
                candles.append(Candle(
                    timestamp=ts,
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    volume=float(r.get("volume", 0.0) or 0.0),
                ))
            candles.sort(key=lambda c: c.timestamp)
            if candles:
                logger.info(
                    "[DATA-LOADER] Loaded %d candles from cache for %s [%s] (%s to %s)",
                    len(candles), symbol, tf_str, start_str, end_str
                )
                return candles
        except Exception as cache_err:  # noqa: BLE001
            logger.warning("[DATA-LOADER] Cache read failed: %s, fetching fresh data", cache_err)

    # 2. Fetch from Binance REST API with pagination
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    current_start = start_ms

    all_raw_klines = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    timeout = httpx.Timeout(10.0, connect=5.0)

    logger.info(
        "[DATA-LOADER] Fetching %s %s candles from Binance API from %s to %s",
        symbol, tf_str, start_dt.isoformat(), end_dt.isoformat()
    )

    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        while current_start < end_ms:
            params = {
                "symbol": binance_symbol,
                "interval": binance_tf,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": 1500,
            }

            resp = None
            for url in BINANCE_REST_BASE_URLS:
                try:
                    resp = await client.get(f"{url}/fapi/v1/klines", params=params)
                    if resp.status_code == 200:
                        break
                except Exception:
                    continue

            if resp is None or resp.status_code != 200:
                logger.error("[DATA-LOADER] Failed to fetch klines from Binance for %s: %s", symbol, getattr(resp, "status_code", "ERROR"))
                break

            klines = resp.json()
            if not klines or not isinstance(klines, list):
                break

            all_raw_klines.extend(klines)

            last_open_time = int(klines[-1][0])
            if last_open_time <= current_start or len(klines) < 1500:
                break
            current_start = last_open_time + 1
            await asyncio.sleep(0.05)  # cooperative throttle

    # 3. Deduplicate and parse into Candle objects
    seen_ts = set()
    candles: list[Candle] = []

    for k in all_raw_klines:
        try:
            ts_ms = int(k[0])
            if ts_ms in seen_ts:
                continue
            seen_ts.add(ts_ms)

            ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            o = float(k[1])
            h = float(k[2])
            l = float(k[3])
            c = float(k[4])
            v = float(k[5]) if len(k) > 5 else 0.0

            # Guard against impossible candle geometry
            h = max(h, o, c)
            l = min(l, o, c)

            if o > 0 and h >= l and l > 0:
                candles.append(Candle(
                    timestamp=ts,
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                    volume=v,
                ))
        except Exception:
            continue

    candles.sort(key=lambda c: c.timestamp)

    # 4. Save to local cache
    if candles and use_cache:
        try:
            cache_payload = {
                "symbol": symbol,
                "timeframe": tf_str,
                "start_dt": start_dt.isoformat(),
                "end_dt": end_dt.isoformat(),
                "count": len(candles),
                "candles": [
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
            }
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_payload, f)
            logger.info("[DATA-LOADER] Cached %d candles to %s", len(candles), cache_path)
        except Exception as save_err:  # noqa: BLE001
            logger.warning("[DATA-LOADER] Failed to write cache: %s", save_err)

    return candles
