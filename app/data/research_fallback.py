"""
Research-data historical fallback for the Live Market chart.

Loads REAL Binance XAUUSDT candles persisted in the research dataset
(data/research/xauusd_5m_2yr.json) and resamples them to the requested
timeframe using the deterministic IncrementalResampler.  This is read-only
REAL historical data — never fabricated — and provides a reliable chart
source when the live WebSocket and/or REST are temporarily unreachable.

This fallback is used ONLY for display (the Live Market chart).  It never
enters strategy/backtest/observation logic, which continue to use the live
service's closed-candle pipeline.
"""

import json
import os
import threading
from datetime import datetime, timezone

from app.core.constants import TimeFrame
from app.data.models import Candle
from app.data.timeframe_resampler import resample_candles

def _resolve_path(filename: str) -> str:
    candidates = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "research", filename)),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "research", filename)),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]

_FALLBACK_5M = _resolve_path("xauusd_5m_2yr.json")
_FALLBACK_15M = _resolve_path("xauusd_15m_full.json")

# In-memory cache: the 5M dataset is ~10MB JSON and is re-read/re-parsed on
# every request (the overview loads it once per timeframe).  Cache the parsed
# base series once so a full dashboard refresh is milliseconds, not seconds.
_cache_lock = threading.Lock()
_cache: dict[str, list[Candle]] = {}
_series_cache: dict[str, list[Candle]] = {}


def _cached_load(path: str) -> list[Candle] | None:
    with _cache_lock:
        if path in _cache:
            return _cache[path]
    candles = _load_json(path)
    if candles is not None:
        with _cache_lock:
            _cache[path] = candles
    return candles


def _cached_series(symbol: str, timeframe: TimeFrame) -> list[Candle] | None:
    """Returns the (cached) resampled series for a symbol+timeframe."""
    key = f"{symbol}:{timeframe.value}"
    with _cache_lock:
        if key in _series_cache:
            return _series_cache[key]
    return None


def _load_json(path: str) -> list[Candle] | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        candles = []
        for c in raw.get("candles", []):
            try:
                candles.append(Candle(
                    timestamp=datetime.fromisoformat(c["timestamp"]),
                    open=float(c["open"]), high=float(c["high"]),
                    low=float(c["low"]), close=float(c["close"]),
                    volume=float(c.get("volume") or 0.0),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        candles.sort(key=lambda c: c.timestamp)
        return candles if candles else None
    except Exception:  # noqa: BLE001
        return None


def _available(path: str) -> bool:
    return os.path.exists(path)


def load_research_fallback_candles(symbol: str, timeframe: TimeFrame, limit: int = 200) -> list[Candle]:
    """Returns real persisted historical candles for a timeframe.

    Loads the real 5M research series and resamples to the requested
    timeframe.  Returns an empty list if no persisted data is available.

    The persisted research datasets are XAUUSD-only; requesting any other
    symbol returns an empty list rather than silently serving wrong data.
    """
    if symbol.upper() not in ("XAUUSD", "XAUUSDT"):
        return []
    key = f"{symbol}:{timeframe.value}"
    cached = _cached_series(symbol, timeframe)
    if cached is not None:
        return cached[-limit:]

    # Prefer the 5M dataset (most granular, resamples to all TFs consistently).
    base5 = _cached_load(_FALLBACK_5M)
    if not base5:
        # Fall back to the 15M dataset for 15m/30m/1h/4h.
        base15 = _cached_load(_FALLBACK_15M)
        if not base15:
            return []
        if timeframe == TimeFrame.M15:
            series = base15
        else:
            series = resample_candles(base15, timeframe)
    else:
        if timeframe == TimeFrame.M5:
            series = base5
        else:
            series = resample_candles(base5, timeframe)

    if not series:
        return []
    # Cache the FULL resampled series so a later call with a smaller limit can
    # never truncate what a larger-limit caller already cached.  The limit is
    # applied on the returned slice only.
    with _cache_lock:
        if key not in _series_cache:
            _series_cache[key] = list(series)
    return series[-limit:]


def research_fallback_available() -> dict:
    """Whether persisted real historical candles are available."""
    return {
        "5m": _available(_FALLBACK_5M),
        "15m": _available(_FALLBACK_5M) or _available(_FALLBACK_15M),
    }
