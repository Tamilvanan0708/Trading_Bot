"""
Historical Data Loader for Strategy Backtesting.

Fetches and caches real historical Gold (XAUUSDT) OHLCV candles from Binance REST API
for any user-specified date range with automatic pagination and local disk caching.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import glob
import json
import os
from typing import Any, List

import httpx

from app.core.constants import TimeFrame
from app.core.logging import logger
from app.data.models import Candle
from app.data.timeframe_resampler import resample_candles

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CACHE_DIR = os.path.join(ROOT_DIR, "data", "research")

BINANCE_REST_BASE_URLS = [
    "https://fapi.binance.com",
    "https://api.binance.com",
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

TF_ENUM_MAP = {
    "5m": TimeFrame.M5,
    "15m": TimeFrame.M15,
    "30m": TimeFrame.M30,
    "1h": TimeFrame.H1,
    "2h": TimeFrame.H2,
    "4h": TimeFrame.H4,
    "1d": TimeFrame.D1,
}


def filter_forex_trading_days(candles: list[Candle]) -> list[Candle]:
    """Filter out weekend hours when real Forex brokers (Exness, IC Markets, MT5) are closed.

    Forex Gold (XAUUSD) trades strictly Monday through Friday:
    - Saturday is 100% closed (UTC 00:00 to 24:00).
    - Sunday daytime is closed (reopens Sunday ~22:00 UTC for Sydney/Asian session).
    - Friday closes ~22:00 UTC (New York close).
    Any synthetic crypto weekend candles are excluded.
    """
    forex_candles = []
    for c in candles:
        ts = c.timestamp if c.timestamp.tzinfo else c.timestamp.replace(tzinfo=timezone.utc)
        wd = ts.weekday()
        hr = ts.hour
        # Exclude Saturday (weekday 5) entirely
        if wd == 5:
            continue
        # Exclude Sunday (weekday 6) entirely
        if wd == 6:
            continue
        # Exclude Friday (weekday 4) after 22:00 UTC market close
        if wd == 4 and hr >= 22:
            continue
        forex_candles.append(c)
    return forex_candles


def get_available_forex_data_range() -> dict[str, Any]:
    """Returns available historical Forex dataset dates and metadata."""
    return {
        "min_date": "2025-12-11",
        "max_date": "2026-09-11",
        "market": "FOREX_5DAY",
        "market_label": "Forex 5-Day (Mon–Fri only)",
        "symbol": "XAUUSD",
        "timeframes": ["5m", "15m", "30m", "1h", "2h", "4h"],
    }


def _load_bundled_master_dataset(tf_str: str) -> list[Candle]:
    """Load candles from permanently committed master datasets in data/research."""
    # 1. Direct match for 15m
    if tf_str == "15m":
        for fname in ("xauusd_15m_real.json", "xauusd_15m_full.json"):
            fpath = os.path.join(CACHE_DIR, fname)
            if os.path.exists(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    entries = raw.get("candles", raw)
                    candles = _parse_candles(entries)
                    if candles:
                        return candles
                except Exception as err:
                    logger.debug("[DATA-LOADER] Master %s read error: %s", fname, err)

    # 2. Direct match for 5m
    if tf_str == "5m":
        fpath = os.path.join(CACHE_DIR, "xauusd_5m_2yr.json")
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                entries = raw.get("candles", raw)
                candles = _parse_candles(entries)
                if candles:
                    return candles
            except Exception as err:
                logger.debug("[DATA-LOADER] Master 5m read error: %s", err)

    # 3. Resample to higher timeframes (30m, 1h, 2h, 4h) from 15m
    target_tf_enum = TF_ENUM_MAP.get(tf_str)
    if target_tf_enum is not None and tf_str in ("30m", "1h", "2h", "4h"):
        base_candles = _load_bundled_master_dataset("15m")
        if base_candles:
            try:
                resampled = resample_candles(base_candles, target_tf_enum)
                if resampled:
                    return resampled
            except Exception as r_err:
                logger.debug("[DATA-LOADER] Resampling to %s failed: %s", tf_str, r_err)

    return []


def _parse_candles(entries: list[dict]) -> list[Candle]:
    """Parse a list of raw candle dicts into sorted Candle models."""
    candles: list[Candle] = []
    for r in entries:
        try:
            ts = datetime.fromisoformat(str(r["timestamp"]))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            candles.append(Candle(
                timestamp=ts,
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=float(r.get("volume", 0.0) or 0.0),
            ))
        except Exception:
            continue
    candles.sort(key=lambda c: c.timestamp)
    return candles


def _save_cache(cache_path: str, symbol: str, tf_str: str, start_dt: datetime, end_dt: datetime, candles: list[Candle]) -> None:
    """Save Candle list to a JSON cache file."""
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


async def fetch_historical_candles(
    symbol: str,
    timeframe: str,
    start_dt: datetime,
    end_dt: datetime,
    use_cache: bool = True,
    filter_forex: bool = True,
) -> list[Candle]:
    """Fetch historical candles for symbol and timeframe between start_dt and end_dt.

    1. Checks for exact cache match.
    2. Searches for wider/master cache files covering [start_dt, end_dt] and slices candles.
    3. Searches committed master files (xauusd_15m_real.json, xauusd_5m_2yr.json) + auto-resampling.
    4. Falls back to resilient Binance REST API pagination with retry & rate-limit backoff.
    5. Applies Forex 5-Day Market Calendar Filter (strips Saturday/Sunday weekend bars).
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    tf_str = timeframe.lower()
    binance_tf = TF_INTERVAL_MAP.get(tf_str, "15m")
    binance_symbol = "XAUUSDT" if symbol.upper() in ("XAUUSD", "XAUUSDT") else symbol.upper()

    # 0. Primary: Fetch directly from MetaTrader 5 (MT5) if available on the system
    try:
        from app.config.settings import get_settings
        settings = get_settings()
        if settings.MT5_ENABLED or settings.LIVE_FEED_PROVIDER == "mt5":
            from app.data.mt5_provider import MT5MarketDataProvider
            from app.core.constants import TimeFrame
            tf_enum = TF_ENUM_MAP.get(tf_str, TimeFrame.M15)
            mt5_symbol = getattr(settings, "MT5_SYMBOL", "") or symbol
            mt5_p = MT5MarketDataProvider(
                symbol=mt5_symbol,
                login=settings.MT5_LOGIN,
                server=settings.MT5_SERVER,
                password=settings.MT5_PASSWORD,
                magic=settings.MT5_MAGIC,
                timezone_offset_minutes=settings.MT5_TZ_OFFSET_MINUTES,
            )
            if not mt5_p.is_connected:
                await mt5_p.connect_async()
            mt5_candles = await mt5_p.get_ohlcv(
                symbol=mt5_symbol,
                timeframe=tf_enum,
                start_time=start_dt,
                end_time=end_dt,
            )
            if mt5_candles and len(mt5_candles) > 0:
                logger.info(
                    "[DATA-LOADER] Successfully loaded %d %s candles directly from MT5 (%s to %s)",
                    len(mt5_candles), tf_str, start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")
                )
                return filter_forex_trading_days(mt5_candles) if filter_forex else mt5_candles
    except Exception as mt5_err:  # noqa: BLE001
        logger.debug("[DATA-LOADER] MT5 fetch unavailable or skipped: %s", mt5_err)

    start_str = start_dt.strftime("%Y%m%d_%H%M")
    end_str = end_dt.strftime("%Y%m%d_%H%M")
    cache_path = os.path.join(CACHE_DIR, f"cache_{binance_symbol.lower()}_{tf_str}_{start_str}_{end_str}.json")

    # 1. Exact local cache match
    if use_cache and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            entries = raw.get("candles", raw)
            candles = _parse_candles(entries)
            if candles:
                logger.info(
                    "[DATA-LOADER] Loaded %d candles from exact cache for %s [%s] (%s to %s)",
                    len(candles), symbol, tf_str, start_str, end_str
                )
                return filter_forex_trading_days(candles) if filter_forex else candles
        except Exception as cache_err:  # noqa: BLE001
            logger.warning("[DATA-LOADER] Cache read failed: %s, checking master candidates", cache_err)

    # 2. Master / Range-sliced cache candidate lookup
    if use_cache:
        pattern = os.path.join(CACHE_DIR, f"cache_{binance_symbol.lower()}_{tf_str}_*.json")
        candidates = sorted(glob.glob(pattern), key=lambda p: os.path.getsize(p), reverse=True)
        for c_path in candidates:
            if c_path == cache_path:
                continue
            try:
                with open(c_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                c_start_str = raw.get("start_dt")
                c_end_str = raw.get("end_dt")
                if not c_start_str or not c_end_str:
                    continue
                c_start = datetime.fromisoformat(c_start_str)
                c_end = datetime.fromisoformat(c_end_str)
                if c_start.tzinfo is None:
                    c_start = c_start.replace(tzinfo=timezone.utc)
                if c_end.tzinfo is None:
                    c_end = c_end.replace(tzinfo=timezone.utc)

                # Check if candidate file contains the requested range
                covers_start = c_start <= start_dt
                covers_end = (c_end >= end_dt) or (c_end.date() >= end_dt.date())
                if covers_start and covers_end:
                    entries = raw.get("candles", [])
                    sliced_entries = []
                    for r in entries:
                        ts = datetime.fromisoformat(str(r["timestamp"]))
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if start_dt <= ts <= end_dt:
                            sliced_entries.append(r)

                    sliced_candles = _parse_candles(sliced_entries)
                    if sliced_candles:
                        logger.info(
                            "[DATA-LOADER] Sliced %d candles from master cache %s for %s [%s] (%s to %s)",
                            len(sliced_candles), os.path.basename(c_path), symbol, tf_str, start_str, end_str
                        )
                        _save_cache(cache_path, symbol, tf_str, start_dt, end_dt, sliced_candles)
                        return filter_forex_trading_days(sliced_candles) if filter_forex else sliced_candles
            except Exception as slice_err:  # noqa: BLE001
                logger.debug("[DATA-LOADER] Skipping candidate %s: %s", c_path, slice_err)
                continue

    # 3. Check bundled master datasets (offline fallback for Render US cloud)
    bundled_candles = _load_bundled_master_dataset(tf_str)
    if bundled_candles:
        sliced_bundled = [c for c in bundled_candles if start_dt <= c.timestamp <= end_dt]
        if sliced_bundled and len(sliced_bundled) >= 50:
            logger.info(
                "[DATA-LOADER] Sliced %d candles from bundled dataset for %s [%s]",
                len(sliced_bundled), symbol, tf_str
            )
            _save_cache(cache_path, symbol, tf_str, start_dt, end_dt, sliced_bundled)
            return filter_forex_trading_days(sliced_bundled) if filter_forex else sliced_bundled

    # 3. Fetch from Binance REST API with pagination and retry backoff
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    current_start = start_ms

    all_raw_klines = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    timeout = httpx.Timeout(20.0, connect=10.0)

    logger.info(
        "[DATA-LOADER] Fetching %s %s candles from Binance API from %s to %s",
        symbol, tf_str, start_dt.isoformat(), end_dt.isoformat()
    )

    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=False) as client:
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
                endpoint = f"{url}/fapi/v1/klines" if "fapi" in url else f"{url}/api/v3/klines"
                for attempt in range(3):
                    try:
                        resp = await client.get(endpoint, params=params)
                        if resp.status_code == 200:
                            break
                        elif resp.status_code == 429:
                            # Rate limit hit: sleep and retry
                            wait_s = 1.5 * (attempt + 1)
                            logger.warning("[DATA-LOADER] Binance rate-limit 429 encountered, waiting %.1fs", wait_s)
                            await asyncio.sleep(wait_s)
                        else:
                            break
                    except Exception as req_err:
                        logger.debug("[DATA-LOADER] Request attempt %d failed on %s: %s", attempt + 1, endpoint, req_err)
                        await asyncio.sleep(0.5 * (attempt + 1))
                if resp is not None and resp.status_code == 200:
                    break

            if resp is None or resp.status_code != 200:
                logger.error("[DATA-LOADER] Failed to fetch klines from Binance for %s: %s", symbol, getattr(resp, "status_code", "ERROR"))
                break

            try:
                klines = resp.json()
            except Exception as json_err:
                logger.error("[DATA-LOADER] Failed to parse JSON response: %s", json_err)
                break

            if not klines or not isinstance(klines, list):
                break

            all_raw_klines.extend(klines)

            last_open_time = int(klines[-1][0])
            if last_open_time <= current_start:
                break
            current_start = last_open_time + 1
            if len(klines) < 1500:
                break
            await asyncio.sleep(0.05)  # cooperative throttle

    # 4. Deduplicate and parse into Candle objects
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

    # 5. Save to local cache
    if candles and use_cache:
        _save_cache(cache_path, symbol, tf_str, start_dt, end_dt, candles)

    return filter_forex_trading_days(candles) if filter_forex else candles

