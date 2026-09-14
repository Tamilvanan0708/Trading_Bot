"""
Binance USD-M Futures historical kline provider.

Fetches REAL XAUUSDT OHLCV history from the Binance public REST API so the
live analysis pipeline is based on real market data rather than synthetic
sample data.  Implements the MarketDataProvider interface.

Includes exponential-backoff retries, HTTP/timeout validation, and OHLCV
candle validation (ordering, duplicates, gaps, invalid values, stale data).
"""

import asyncio
from datetime import datetime, timezone

import httpx

from app.config.settings import Settings, get_settings
from app.core.constants import TimeFrame
from app.core.logging import logger
from app.data.ingestion import validate_candles
from app.data.models import Candle, MultiTimeframeSnapshot
from app.data.provider import MarketDataProvider

BINANCE_REST_BASE_URL = "https://fapi.binance.com"
BINANCE_REST_BASE_URLS = [
    "https://fapi.binance.com",
    "https://fapi1.binance.com",
    "https://fapi2.binance.com",
    "https://fapi3.binance.com",
]

DEFAULT_BINANCE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}

_BINANCE_SEMAPHORE = asyncio.Semaphore(2)
_LAST_BINANCE_CALL_TS = 0.0

# TimeFrame -> Binance kline interval (futures XAUUSDT supported set)
_INTERVAL_MAP = {
    TimeFrame.M5: "5m",
    TimeFrame.M15: "15m",
    TimeFrame.M30: "30m",
    TimeFrame.H1: "1h",
    TimeFrame.H4: "4h",
    TimeFrame.D1: "1d",
}

# Logical symbol -> Binance ticker
_SYMBOL_MAP = {
    "XAUUSD": "XAUUSDT",
    "XAUUSDT": "XAUUSDT",
}


class BinanceHistoryProvider(MarketDataProvider):
    """Real XAUUSDT history from the Binance futures public REST API."""

    def __init__(
        self,
        settings: Settings | None = None,
        base_url: str = BINANCE_REST_BASE_URL,
    ) -> None:
        self.settings = settings or get_settings()
        self._base_url = base_url

    def _binance_symbol(self, symbol: str) -> str:
        return _SYMBOL_MAP.get(symbol.upper(), symbol.upper())

    def _interval(self, timeframe: TimeFrame) -> str:
        try:
            return _INTERVAL_MAP[timeframe]
        except KeyError:
            raise ValueError(f"Unsupported timeframe for Binance history: {timeframe}")

    async def _fetch_klines(self, params: dict) -> list[list]:
        """Fetch klines with exponential-backoff retries and response validation.

        Integrity policy: there is NO substitution fallback.  If the XAUUSDT
        futures endpoint fails, the error propagates so callers can mark data
        quality degraded — never silently replaced with a proxy instrument
        (PAXGUSDT) or stale cached history reported as fresh.
        """
        global _LAST_BINANCE_CALL_TS
        max_retries = self.settings.BINANCE_HISTORY_MAX_RETRIES
        timeout = httpx.Timeout(self.settings.BINANCE_HISTORY_TIMEOUT_SECONDS)
        backoff = self.settings.BINANCE_HISTORY_RETRY_BACKOFF
        last_exc: Exception | None = None

        async with _BINANCE_SEMAPHORE:
            for attempt in range(max_retries + 1):
                url = BINANCE_REST_BASE_URLS[attempt % len(BINANCE_REST_BASE_URLS)]
                try:
                    # Concurrency throttle to prevent startup burst 429 errors
                    now_loop = asyncio.get_event_loop().time()
                    elapsed = now_loop - _LAST_BINANCE_CALL_TS
                    if elapsed < 0.15:
                        await asyncio.sleep(0.15 - elapsed)
                    _LAST_BINANCE_CALL_TS = asyncio.get_event_loop().time()

                    async with httpx.AsyncClient(timeout=timeout) as client:
                        try:
                            res = await client.get(f"{url}/fapi/v1/klines", params=params, headers=DEFAULT_BINANCE_HEADERS)
                        except TypeError:
                            res = await client.get(f"{url}/fapi/v1/klines", params=params)

                        if res.status_code == 429:
                            retry_after = res.headers.get("Retry-After")
                            delay = float(retry_after) if retry_after and retry_after.isdigit() else 3.5
                            logger.warning(
                                "Binance HTTP 429 on %s; backing off %.1fs (attempt %d/%d)",
                                url, delay, attempt + 1, max_retries + 1,
                            )
                            await asyncio.sleep(delay)
                            continue

                        if res.status_code != 200:
                            raise RuntimeError(
                                f"Binance history HTTP {res.status_code}: {res.text[:200]}"
                            )
                        data = res.json()
                        if not isinstance(data, list):
                            raise RuntimeError(f"Binance history returned unexpected payload: {type(data)}")
                        return data
                except Exception as exc:  # noqa: BLE001 - retry all transport/HTTP errors
                    last_exc = exc
                    if attempt >= max_retries:
                        break
                    delay = backoff * (2 ** attempt)
                    logger.warning(
                        "Binance history fetch attempt %d/%d on %s failed (%s); retrying in %.1fs",
                        attempt + 1, max_retries + 1, url, exc, delay,
                    )
                    await asyncio.sleep(delay)

        raise RuntimeError(f"Binance history fetch failed after {max_retries + 1} attempts: {last_exc}")

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: TimeFrame,
        limit: int = 500,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Candle]:
        params = {
            "symbol": self._binance_symbol(symbol),
            "interval": self._interval(timeframe),
            "limit": min(max(limit, 1), 1000),
        }
        if start_time is not None:
            params["startTime"] = int(start_time.timestamp() * 1000)
        if end_time is not None:
            params["endTime"] = int(end_time.timestamp() * 1000)

        # Honest failure: propagate the real fetch error instead of
        # substituting proxy instruments or stale research files.
        rows = await self._fetch_klines(params)
        candles: list[Candle] = []
        for row in rows:
            if len(row) < 6:
                logger.warning("Binance kline row malformed: %s", row[:6])
                continue
            o, h, l, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
            # Normalise OHLC consistency defensively (min/max floor/ceiling)
            candles.append(
                Candle(
                    timestamp=datetime.fromtimestamp(row[0] / 1000.0, tz=timezone.utc),
                    open=o,
                    high=max(h, o, l, c),
                    low=min(l, o, h, c),
                    close=c,
                    volume=float(row[5]),
                )
            )

        return candles

    async def get_latest_price(self, symbol: str) -> float:
        candles = await self.get_ohlcv(symbol, TimeFrame.M15, limit=1)
        if not candles:
            raise ValueError(f"No Binance history available for {symbol}.")
        return candles[-1].close

    async def load_base_15m(self, limit: int = 800) -> tuple[list[Candle], dict]:
        """Load a validated real 15M base series and its validation report."""
        candles = await self.get_ohlcv("XAUUSD", TimeFrame.M15, limit=limit)
        vresult = validate_candles(candles, TimeFrame.M15, strict_gaps=False)
        report = {
            "total": vresult.total_candles,
            "valid": vresult.valid,
            "gaps": vresult.gaps,
            "duplicates": vresult.duplicates,
            "out_of_order": vresult.out_of_order,
            "invalid_ohlc": vresult.invalid_ohlc,
            "errors": vresult.errors[:10],
            "warnings": vresult.warnings[:10],
        }
        logger.info(
            "Loaded %s real Binance 15M candles (%s -> %s). validation=%s",
            len(candles),
            candles[0].timestamp if candles else "n/a",
            candles[-1].timestamp if candles else "n/a",
            "PASS" if vresult.valid else "FAIL",
        )
        return candles, report

    async def get_multi_timeframe_snapshot(
        self,
        symbol: str,
        as_of_time: datetime | None = None,
        m15_limit: int = 400,
    ) -> MultiTimeframeSnapshot:

        m15 = await self.get_ohlcv(symbol, TimeFrame.M15, limit=m15_limit)
        if as_of_time is not None:
            m15 = [c for c in m15 if c.timestamp <= as_of_time]
        if not m15:
            raise ValueError(f"No Binance history available for {symbol}.")

        m30 = await self.get_ohlcv(symbol, TimeFrame.M30, limit=200)
        h1 = await self.get_ohlcv(symbol, TimeFrame.H1, limit=150)
        h4 = await self.get_ohlcv(symbol, TimeFrame.H4, limit=100)

        if as_of_time is not None:
            m30 = [c for c in m30 if c.timestamp <= as_of_time]
            h1 = [c for c in h1 if c.timestamp <= as_of_time]
            h4 = [c for c in h4 if c.timestamp <= as_of_time]

        return MultiTimeframeSnapshot(
            symbol=symbol,
            timestamp=m15[-1].timestamp,
            current_price=m15[-1].close,
            m15=m15,
            m30=m30,
            h1=h1,
            h4=h4,
        )