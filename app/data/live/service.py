"""
Live market data service: bridges real-time tick feeds to the deterministic
analysis pipeline while guaranteeing look-ahead safety.

Live ticks are aggregated into forming 15M candles. Only candles that have
fully closed are exposed to the analysis pipeline (unless explicitly
configured otherwise).  Historical base candles are merged in so the pipeline
always has sufficient warmup history.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config.settings import Settings, get_settings
from app.core.constants import TimeFrame
from app.core.logging import logger
from app.data.live.binance_history import BinanceHistoryProvider
from app.data.live.binance_provider import BinanceGoldMarketProvider
from app.data.live.ctrader_provider import CTraderMarketProvider
from app.data.live.registry import FeedRegistry
from app.data.models import Candle, DataQualityStatus, MultiTimeframeSnapshot, Tick
from app.data.provider import MarketDataProvider
from app.data.timeframe_resampler import resample_candles
from app.data.websocket_provider import WebSocketMarketFeed


def _bucket_start(ts: datetime, minutes: int) -> datetime:
    """Floor a timestamp to the start of its interval bucket."""
    total = ts.hour * 60 + ts.minute
    remainder = total % minutes
    return ts.replace(second=0, microsecond=0) - timedelta(minutes=remainder)


_TF_DURATION_MINUTES = {
    TimeFrame.M5: 5,
    TimeFrame.M15: 15,
    TimeFrame.M30: 30,
    TimeFrame.H1: 60,
    TimeFrame.H2: 120,
    TimeFrame.H4: 240,
    TimeFrame.D1: 1440,
}

# Timeframes maintained as live forming candles (tick-aggregated).
_TICK_TFS = [TimeFrame.M5, TimeFrame.M15, TimeFrame.M30, TimeFrame.H1, TimeFrame.H4]


def _complete_timeframe_candles(
    candles: list[Candle], timeframe: TimeFrame, last_closed_ts: datetime
) -> list[Candle]:
    """Return only candles whose full timeframe window has elapsed.

    A resampled candle stamped T for timeframe D is confirmed only when
    T + D <= last_closed_ts.  This prevents a partially-formed 4H/1H/30M
    candle (whose 15M sub-candles are not yet all closed) from being
    treated as a confirmed higher-timeframe signal.
    """
    duration = timedelta(minutes=_TF_DURATION_MINUTES[timeframe])
    return [c for c in candles if c.timestamp + duration <= last_closed_ts]


_live_service_instance: Optional["LiveMarketDataService"] = None


def get_live_service() -> "LiveMarketDataService":
    """Returns the shared LiveMarketDataService singleton."""
    global _live_service_instance
    if _live_service_instance is None:
        _live_service_instance = LiveMarketDataService()
    return _live_service_instance


class LiveMarketDataService:
    """Aggregates live ticks into closed candles and serves snapshots."""

    def __init__(
        self,
        settings: Settings | None = None,
        historical_provider: MarketDataProvider | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.registry = FeedRegistry.get_instance()
        self._historical_provider = historical_provider

        self._symbol = self.settings.DEFAULT_SYMBOL
        self._tick_buffer_minutes = 15
        self._lock = asyncio.Lock()
        self._closed_15m: list[Candle] = []
        self._closed_5m: list[Candle] = []
        self._forming_by_tf: dict[TimeFrame, Candle] = {}
        self._last_price: float = 0.0
        self._live_price: float | None = None
        self._feed: WebSocketMarketFeed | None = None
        self._gap_count = 0
        self._dup_count = 0
        self._ooo_count = 0
        self._last_history_error: str | None = None
        # History refresh state
        self._running = False
        self._refresh_task: asyncio.Task | None = None
        self._mt5_task: asyncio.Task | None = None
        self._last_history_refresh_at: datetime | None = None
        self._refresh_status: str = "NONE"  # NONE | RUNNING | SUCCESS | FAILED
        self._refresh_error: str | None = None
        self._last_refresh_attempt_at: datetime | None = None
        self._startup_task: asyncio.Task | None = None
        self._history_fallback = False

    # ------------------------------------------------------------------
    # Backward-compatible accessors
    # ------------------------------------------------------------------

    @property
    def _forming_15m(self) -> Candle | None:
        """The currently-forming 15M candle (kept for compatibility)."""
        return self._forming_by_tf.get(TimeFrame.M15)

    @_forming_15m.setter
    def _forming_15m(self, value: Candle | None) -> None:
        if value is None:
            self._forming_by_tf.pop(TimeFrame.M15, None)
        else:
            self._forming_by_tf[TimeFrame.M15] = value

    @property
    def history_fallback(self) -> bool:
        """True when REST history failed and the service is running on
        stale in-memory candles rather than a freshly fetched base."""
        return self._history_fallback

    @property
    def live_price(self) -> float | None:
        """The latest price received from a real live feed tick."""
        return self._live_price

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Dispatch background startup (history load + feed connect).

        Returns immediately so the web app never blocks on Binance REST /
        WebSocket calls during startup.
        """
        self._running = True
        self._startup_task = asyncio.create_task(
            self._startup_async(), name="live-service-startup"
        )
        logger.info("LiveMarketDataService startup dispatched (non-blocking).")

    async def _startup_async(self) -> None:
        """Background startup: connect feed immediately, then load history in background."""
        self._feed = self._build_feed()
        if self._feed is not None:
            self._feed.set_on_tick_callback(self.on_tick)
            self._feed.set_on_reconnect_callback(self._on_feed_reconnect)
            await self.registry.register_feed(self._feed)
        elif self.settings.LIVE_FEED_PROVIDER in ("tradingview", "mock"):
            await self.registry.register_buffer(self._symbol)
        elif self.settings.LIVE_FEED_PROVIDER == "mt5":
            self._mt5_task = asyncio.create_task(self._start_mt5_polling(), name="mt5-tick-poll")

        try:
            await self._load_historical_base()
        except Exception as exc:  # noqa: BLE001 - non-fatal at startup
            logger.error("Historical base load failed at startup: %s", exc)
        try:
            await self._load_5m_base()
        except Exception as exc:  # noqa: BLE001 - 5m base is best-effort
            logger.debug("5m base load skipped (non-fatal): %s", exc)

        # Start the periodic REST history refresh loop.
        self._refresh_task = asyncio.create_task(
            self._history_refresh_loop(), name="history-refresh"
        )
        logger.info("LiveMarketDataService startup complete (symbol=%s).", self._symbol)

    async def stop(self) -> None:
        self._running = False
        for task_attr in ("_startup_task", "_refresh_task", "_mt5_task"):
            task = getattr(self, task_attr, None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                setattr(self, task_attr, None)
        await self.registry.stop_all()
        logger.info("LiveMarketDataService stopped.")

    async def _on_feed_reconnect(self) -> None:
        """Called by the WebSocket feed after a successful reconnect."""
        logger.info("[HISTORY] WebSocket reconnected; triggering history backfill.")
        await self.request_emergency_refresh()

    # ------------------------------------------------------------------
    # Feed construction
    # ------------------------------------------------------------------

    def _build_feed(self) -> WebSocketMarketFeed | None:
        provider = self.settings.LIVE_FEED_PROVIDER
        try:
            if provider == "binance":
                return BinanceGoldMarketProvider(symbol=self._symbol)
            if provider == "ctrader":
                return CTraderMarketProvider(
                    symbol=self._symbol,
                    symbol_id=self.settings.CTRADER_SYMBOL_ID,
                    account_id=self.settings.CTRADER_ACCOUNT_ID,
                    access_token=self.settings.CTRADER_ACCESS_TOKEN,
                )
            if provider == "tradingview":
                logger.info("TradingView webhook feed: passive (HTTP POSTs only).")
                return None
            if provider == "mt5":
                logger.info("MetaTrader 5 feed selected (Windows-only, polling ticks).")
                return None
            if provider == "mock":
                logger.info("Live feed disabled (LIVE_FEED_PROVIDER=mock).")
                return None
        except ValueError as exc:
            logger.error("Failed to build live feed for provider %s: %s", provider, exc)
        return None

    async def _start_mt5_polling(self) -> None:
        """Poll MT5 ticks and push them through the normal aggregation pipeline."""
        from app.data.mt5_provider import MT5MarketDataProvider

        if not self.settings.MT5_ENABLED:
            logger.info("MT5 provider requested but MT5_ENABLED=false; skipping.")
            return
        try:
            provider = MT5MarketDataProvider(
                symbol=self._symbol,
                login=self.settings.MT5_LOGIN,
                server=self.settings.MT5_SERVER,
                password=self.settings.MT5_PASSWORD,
                magic=self.settings.MT5_MAGIC,
                timezone_offset_minutes=self.settings.MT5_TZ_OFFSET_MINUTES,
            )
            await provider.connect_async()
        except Exception as exc:  # noqa: BLE001
            logger.error("MT5 connection failed: %s", exc)
            return

        while self._running:
            try:
                tick = await provider.get_tick(self._symbol)
                if tick is not None:
                    await self.on_tick(tick)
            except Exception as exc:  # noqa: BLE001
                logger.warning("MT5 poll error: %s", exc)
            await asyncio.sleep(1.0)
        try:
            await provider.disconnect_async()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def _load_local_json_fallback_15m(self) -> list[Candle]:
        """Load real 15M XAUUSD candles from bundled local JSON research files.

        Only called when Binance REST is unavailable (network issues, rate limits).
        These are real historical Binance candles — not synthetic data.
        Returns the most recent 800 candles sorted oldest-first.
        """
        import json
        import os
        root1 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "research"))
        root2 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "research"))
        # Prefer the most complete/recent file
        candidates = [
            os.path.join(root1, "xauusd_15m_full.json"),
            os.path.join(root1, "xauusd_15m_real.json"),
            os.path.join(root2, "xauusd_15m_full.json"),
            os.path.join(root2, "xauusd_15m_real.json"),
        ]
        for path in candidates:
            if not os.path.exists(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
                entries = raw.get("candles", raw) if isinstance(raw, dict) else raw
                candles = []
                for r in entries:
                    if not isinstance(r, dict) or "timestamp" not in r:
                        continue
                    try:
                        ts = datetime.fromisoformat(str(r["timestamp"]).replace("Z", "+00:00"))
                        candles.append(Candle(
                            timestamp=ts,
                            open=float(r.get("open", 0)),
                            high=float(r.get("high", 0)),
                            low=float(r.get("low", 0)),
                            close=float(r.get("close", 0)),
                            volume=float(r.get("volume", 0) or 0),
                        ))
                    except (ValueError, TypeError):
                        continue
                if candles:
                    candles.sort(key=lambda c: c.timestamp)
                    logger.info("[LOCAL JSON] Loaded %d 15M candles from %s", len(candles), os.path.basename(path))
                    return candles[-800:]  # Return the most recent 800
            except Exception as exc:  # noqa: BLE001
                logger.warning("[LOCAL JSON] Failed to load %s: %s", path, exc)
        return []

    def _load_local_json_fallback_5m(self) -> list[Candle]:
        """Load real 5M XAUUSD candles from bundled local JSON research files."""
        import json
        import os
        root1 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "research"))
        root2 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "research"))
        candidates = [
            os.path.join(root1, "xauusd_5m_2yr.json"),
            os.path.join(root2, "xauusd_5m_2yr.json"),
        ]
        for path in candidates:
            if not os.path.exists(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
                entries = raw.get("candles", raw) if isinstance(raw, dict) else raw
                candles = []
                for r in entries:
                    if not isinstance(r, dict) or "timestamp" not in r:
                        continue
                    try:
                        ts = datetime.fromisoformat(str(r["timestamp"]).replace("Z", "+00:00"))
                        candles.append(Candle(
                            timestamp=ts,
                            open=float(r.get("open", 0)),
                            high=float(r.get("high", 0)),
                            low=float(r.get("low", 0)),
                            close=float(r.get("close", 0)),
                            volume=float(r.get("volume", 0) or 0),
                        ))
                    except (ValueError, TypeError):
                        continue
                if candles:
                    candles.sort(key=lambda c: c.timestamp)
                    logger.info("[LOCAL JSON] Loaded %d 5M candles from %s", len(candles), os.path.basename(path))
                    return candles[-400:]
            except Exception as exc:  # noqa: BLE001
                logger.warning("[LOCAL JSON] Failed to load 5m JSON: %s", exc)
        return []

    async def _load_historical_base(self) -> None:
        """Load a REAL base 15M series (Binance REST) with validation.

        LIVE MODE SAFETY: no synthetic or stale-file fallback.  If Binance
        REST fails, the strategy base stays empty and the data-quality gate
        blocks trading until real data is available.
        """
        provider = self._historical_provider
        if provider is None:
            provider = BinanceHistoryProvider(self.settings)

        candles: list[Candle] = []
        try:
            candles, report = await provider.load_base_15m(limit=800)
            self._gap_count = report["gaps"]
            self._dup_count = report["duplicates"]
            self._ooo_count = report["out_of_order"]
            if report["errors"]:
                self._last_history_error = report["errors"][0]
                logger.error(
                    "Historical data validation errors: %s", report["errors"][:3]
                )
        except Exception as exc:  # noqa: BLE001 - non-fatal at startup, but degraded
            self._last_history_error = str(exc)
            self._closed_15m = []
            self._refresh_status = "FAILED"
            self._refresh_error = str(exc)
            self._history_fallback = False
            logger.error(
                "DEGRADED: Binance REST history unavailable (%s). No synthetic/stale "
                "fallback is used — strategy engines show NO_SETUP and the data-quality "
                "gate blocks trading until real data is fetched.",
                exc,
            )
            return

        if not candles:
            self._last_history_error = self._last_history_error or "Binance REST returned no candles"
            self._closed_15m = []
            self._refresh_status = "FAILED"
            self._refresh_error = self._last_history_error
            logger.error("DEGRADED: Binance REST returned no real 15M candles — no fallback applied.")
            return

        # Join historical candles with future live candles:
        #   - Keep only fully-closed candles (timestamp < current bucket start).
        #   - Live WebSocket ticks will form candles at/after the current bucket,
        #     so there is NO overlap and NO duplicate between the two sources.
        now = datetime.now(timezone.utc)
        cutoff = _bucket_start(now, self._tick_buffer_minutes)
        self._closed_15m = []
        for c in candles:
            ts = c.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < cutoff:
                # Store a copy with a guaranteed-aware UTC timestamp
                self._closed_15m.append(c.model_copy(update={"timestamp": ts}))
        self._last_price = candles[-1].close
        self._last_history_refresh_at = datetime.now(timezone.utc)
        self._refresh_status = "SUCCESS"
        self._refresh_error = None
        self._history_fallback = False
        logger.info(
            "Loaded %s closed real historical 15M candles "
            "(gaps=%s dup=%s ooo=%s).",
            len(self._closed_15m), self._gap_count, self._dup_count, self._ooo_count,
        )

    async def _load_5m_base(self) -> None:
        """Load a REAL 5M base series (Binance REST) for the 5m live chart.

        Best-effort: if REST is unavailable the 5m series stays empty and the
        live Market chart falls back to the persisted research cache for 5m.
        Never blocks application startup (runs inside the background task).
        """
        provider = self._historical_provider
        if provider is None:
            provider = BinanceHistoryProvider(self.settings)
        try:
            candles = await provider.get_ohlcv(self._symbol, TimeFrame.M5, limit=400)
        except Exception as exc:  # noqa: BLE001 - non-fatal
            logger.debug("5m base unavailable (non-fatal): %s — trying local JSON fallback.", exc)
            candles = []
        if not candles:
            candles = await asyncio.to_thread(self._load_local_json_fallback_5m)
        if not candles:
            return
        now = datetime.now(timezone.utc)
        cutoff = _bucket_start(now, 5)
        self._closed_5m = []
        for c in candles:
            ts = c.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < cutoff:
                self._closed_5m.append(c.model_copy(update={"timestamp": ts}))
        logger.info("Loaded %s closed real historical 5M candles.", len(self._closed_5m))

    # ------------------------------------------------------------------
    # History refresh (periodic + emergency backfill)
    # ------------------------------------------------------------------

    async def _history_refresh_loop(self) -> None:
        """Periodic REST history refresh loop, runs in a background task.

        The first refresh occurs after the configured interval; subsequent
        refreshes run at the same interval.  The ``request_emergency_refresh``
        method provides immediate, cooldown-gated refresh for disconnect/
        degraded scenarios.
        """
        interval = self.settings.LIVE_HISTORY_REFRESH_INTERVAL_MINUTES * 60
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self.refresh_history()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - never crash the loop
                logger.error("[HISTORY] Refresh loop error: %s", exc)

    async def refresh_history(self) -> dict:
        """Fetch recent real 15M candles from REST and merge into ``_closed_15m``
        without duplicates, without overwriting the forming candle, and without
        touching :attr:`_live_price`.

        Returns a dict with ``status`` (SUCCESS / FAILED) and metadata.
        """
        provider = self._historical_provider
        if provider is None:
            provider = BinanceHistoryProvider(self.settings)

        self._refresh_status = "RUNNING"
        self._last_refresh_attempt_at = datetime.now(timezone.utc)

        lookback = self.settings.LIVE_HISTORY_REFRESH_LOOKBACK_HOURS
        limit = min(max(int(lookback * 4), self.settings.LIVE_HISTORY_MIN_CANDLES), 800)

        try:
            fetched, report = await provider.load_base_15m(limit=limit)
        except Exception as exc:
            # Honest failure: never launder a failed refresh into SUCCESS.
            # In-memory candles are KEPT (display/analysis continuity), but the
            # status reports FAILED and history_fallback flags the staleness so
            # the data-quality gate can block new trades.
            self._refresh_status = "FAILED"
            self._refresh_error = str(exc)
            if self._closed_15m:
                self._history_fallback = True
                logger.warning(
                    "[HISTORY] Refresh FAILED (%s); serving %d stale in-memory candles "
                    "with degraded status. _last_history_refresh_at NOT advanced.",
                    exc, len(self._closed_15m),
                )
            else:
                self._history_fallback = False
                logger.error("[HISTORY] Refresh FAILED and no candles in memory. Reason: %s", exc)
            logger.error("[SAFETY] Paper trading remains BLOCKED while data is degraded")
            return {"status": "FAILED", "error": str(exc)}

        now = datetime.now(timezone.utc)
        cutoff = _bucket_start(now, self._tick_buffer_minutes)
        added = 0

        # Fetch outside the lock; merge inside.
        async with self._lock:
            existing = {c.timestamp: c for c in self._closed_15m}
            for c in fetched:
                ts = c.timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    continue  # forming or future — never insert
                if ts not in existing:
                    existing[ts] = c.model_copy(update={"timestamp": ts})
                    added += 1

            merged = sorted(existing.values(), key=lambda c: c.timestamp)
            self._closed_15m = merged

            # Recompute validation statistics on the merged dataset.
            from app.data.ingestion import validate_candles
            vresult = validate_candles(self._closed_15m, TimeFrame.M15, strict_gaps=False)
            self._gap_count = vresult.gaps
            self._dup_count = vresult.duplicates
            self._ooo_count = vresult.out_of_order
            self._last_history_error = vresult.errors[0] if vresult.errors else None

        self._refresh_status = "SUCCESS"
        self._refresh_error = None
        self._history_fallback = False
        self._last_history_refresh_at = now

        logger.info(
            "[HISTORY] Refresh successful. Added %s new candles. Total %s. "
            "Gaps=%s Dup=%s OOO=%s",
            added, len(self._closed_15m), self._gap_count, self._dup_count, self._ooo_count,
        )
        # Also refresh 5M candles for low-timeframe strategies & chart
        try:
            fetched_5m = await provider.get_ohlcv(self._symbol, TimeFrame.M5, limit=200)
            if fetched_5m:
                cutoff_5m = _bucket_start(now, 5)
                async with self._lock:
                    existing_5m = {c.timestamp: c for c in self._closed_5m}
                    for c in fetched_5m:
                        ts = c.timestamp
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if ts < cutoff_5m and ts not in existing_5m:
                            existing_5m[ts] = c.model_copy(update={"timestamp": ts})
                    self._closed_5m = sorted(existing_5m.values(), key=lambda c: c.timestamp)[-400:]
        except Exception as exc:  # noqa: BLE001
            logger.debug("[HISTORY] 5M background refresh skipped: %s", exc)

        dq = await self.data_quality()
        if dq.degraded:
            logger.info("[HISTORY] Data quality is still DEGRADED: %s", dq.degradation_reason)
        else:
            logger.info("[HISTORY] Data quality: HEALTHY")
        return {"status": "SUCCESS", "added": added, "gaps": self._gap_count}

    async def request_emergency_refresh(self) -> dict:
        """Attempt an immediate refresh, subject to a cooldown.

        The cooldown (default 60 s) prevents REST API spam if the scheduler
        polls every few seconds while degraded.
        """
        now = datetime.now(timezone.utc)
        if self._last_refresh_attempt_at is not None:
            elapsed = (now - self._last_refresh_attempt_at).total_seconds()
            if elapsed < self.settings.LIVE_HISTORY_EMERGENCY_REFRESH_COOLDOWN_SECONDS:
                return {"status": "SKIPPED", "reason": "cooldown"}
        return await self.refresh_history()

    # ------------------------------------------------------------------
    # Tick ingestion (called by feeds / webhook)
    # ------------------------------------------------------------------

    async def on_tick(self, tick: Tick) -> None:
        """Aggregate an incoming tick into the forming candle (all TFs)."""
        price = tick.mid
        if price <= 0:
            return
        self._last_price = price
        await self.registry.push_tick(tick)

        from app.services.status import get_status
        await get_status().mark_tick(price)

        async with self._lock:
            for tf in _TICK_TFS:
                minutes = _TF_DURATION_MINUTES[tf]
                ts = tick.timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                bucket = _bucket_start(ts, minutes)
                forming = self._forming_by_tf.get(tf)
                if forming is None:
                    self._live_price = price
                    self._forming_by_tf[tf] = Candle(
                        timestamp=bucket,
                        open=price,
                        high=price,
                        low=price,
                        close=price,
                        volume=tick.volume,
                    )
                elif bucket > forming.timestamp:
                    # New bucket: finalize the previous candle and start a new one.
                    self._finalize_forming_candle(tf, forming)
                    self._live_price = price
                    self._forming_by_tf[tf] = Candle(
                        timestamp=bucket,
                        open=price,
                        high=price,
                        low=price,
                        close=price,
                        volume=tick.volume,
                    )
                elif bucket == forming.timestamp:
                    # Same bucket: update the forming candle.
                    self._live_price = price
                    f = self._forming_by_tf[tf]
                    f.high = max(f.high, price)
                    f.low = min(f.low, price)
                    f.close = price
                    f.volume += tick.volume
                else:
                    # Stale / out-of-order tick (bucket older than the forming candle).
                    # Dropping prevents corruption of the current candle's OHLC
                    # and prevents a stale price from being treated as live.
                    logger.debug(
                        "Dropping out-of-order tick: bucket %s < forming %s",
                        bucket, forming.timestamp,
                    )

    def _finalize_forming_candle(self, tf: TimeFrame, forming: Candle) -> None:
        """Store a finalized candle into its per-timeframe closed series."""
        if tf == TimeFrame.M15:
            self._closed_15m.append(forming)
            if len(self._closed_15m) > 2000:
                self._closed_15m = self._closed_15m[-1000:]
        elif tf == TimeFrame.M5:
            self._closed_5m.append(forming)
            if len(self._closed_5m) > 400:
                self._closed_5m = self._closed_5m[-400:]
        # 30m/1h/4h closed series are derived by resampling _closed_15m on demand
        # (see get_multi_timeframe_snapshot / get_chart_series), so no separate
        # storage is required for them.

    # ------------------------------------------------------------------
    # Snapshot for analysis pipeline (closed candles only by default)
    # ------------------------------------------------------------------

    async def get_multi_timeframe_snapshot(
        self,
        symbol: str,
        as_of_time: datetime | None = None,
        m15_limit: int = 400,
        include_forming: bool = False,
    ) -> MultiTimeframeSnapshot:
        """Builds a snapshot using only CLOSED 15M candles.

        Args:
            include_forming: If True, includes the currently-forming candle.
                Defaults to False to guarantee zero look-ahead.
        """
        async with self._lock:
            closed = list(self._closed_15m)
            if include_forming and self._forming_15m is not None:
                forming_copy = self._forming_15m.model_copy()
            else:
                forming_copy = None

        if as_of_time is not None:
            closed = [c for c in closed if c.timestamp <= as_of_time]
        if forming_copy is not None and (as_of_time is None or forming_copy.timestamp <= as_of_time):
            closed = closed + [forming_copy]
        if not closed:
            raise ValueError(f"No closed 15M candles available for {symbol}.")

        m15 = closed[-m15_limit:]
        # 5M closed series (live-aggregated) plus the forming 5M candle.
        m5 = self._closed_5m[-m15_limit:]
        if include_forming and self._forming_by_tf.get(TimeFrame.M5) is not None:
            m5 = m5 + [self._forming_by_tf[TimeFrame.M5].model_copy()]
        # The live price comes from the live feed's latest tick, NEVER from the
        # historical/sample base close.  This prevents the $2662 synthetic price
        # from leaking into live monitoring.
        current_price = self._live_price if self._live_price is not None else closed[-1].close
        timestamp = closed[-1].timestamp

        # Higher-timeframe candles must be fully closed relative to the last
        # closed 15M candle — a partially-formed 4H/1H/30M candle is NOT a
        # confirmed signal.
        last_closed_ts = closed[-1].timestamp
        m30 = _complete_timeframe_candles(
            resample_candles(closed, TimeFrame.M30), TimeFrame.M30, last_closed_ts
        )[-200:]
        h1 = _complete_timeframe_candles(
            resample_candles(closed, TimeFrame.H1), TimeFrame.H1, last_closed_ts
        )[-150:]
        h2 = _complete_timeframe_candles(
            resample_candles(closed, TimeFrame.H2), TimeFrame.H2, last_closed_ts
        )[-120:]
        h4 = _complete_timeframe_candles(
            resample_candles(closed, TimeFrame.H4), TimeFrame.H4, last_closed_ts
        )[-100:]

        return MultiTimeframeSnapshot(
            symbol=symbol,
            timestamp=timestamp,
            current_price=current_price,
            m5=m5,
            m15=m15,
            m30=m30,
            h1=h1,
            h2=h2,
            h4=h4,
        )

    # ------------------------------------------------------------------
    # Chart series for display (includes forming candle)
    # ------------------------------------------------------------------

    async def get_chart_series(
        self, symbol: str, tf: TimeFrame, limit: int = 200,
    ) -> dict:
        """Build a candle series suitable for the Live Market chart.

        Returns closed candles (from the authoritative closed series) plus
        the current forming candle if available.  Designed for display only;
        the analysis pipeline always uses ``get_multi_timeframe_snapshot``
        (closed-only, lookahead-safe).
        """
        async with self._lock:
            closed_15 = list(self._closed_15m)
            closed_5 = list(self._closed_5m)
            forming = self._forming_by_tf.get(tf)

        if tf == TimeFrame.M5:
            closed = closed_5[-limit:]
        elif tf == TimeFrame.M15:
            closed = closed_15[-limit:]
        else:
            closed = resample_candles(closed_15, tf)[-limit:]

        series = list(closed)
        if forming is not None:
            forming_copy = forming.model_copy()
            if series and forming_copy.timestamp == series[-1].timestamp:
                # The forming candle replaces the last resampled bucket
                # (which may be partially formed from closed sub-candles).
                series[-1] = forming_copy
            elif not series or forming_copy.timestamp > series[-1].timestamp:
                series.append(forming_copy)
        else:
            forming_copy = None

        current_price = (
            self._live_price
            if self._live_price is not None
            else (series[-1].close if series else None)
        )
        return {
            "closed": closed,
            "forming": forming_copy,
            "candles": series,
            "current_price": current_price,
        }

    async def get_latest_price(self, symbol: str) -> float:
        # Prefer the live feed's latest tick, then the last tick mid, then fallback.
        tick = await self.registry.latest_tick(symbol)
        if tick is not None and tick.mid > 1000.0:
            return tick.mid
        if self._live_price is not None and self._live_price > 1000.0:
            return self._live_price
        if self._last_price > 1000.0:
            return self._last_price
        async with self._lock:
            if self._closed_5m and self._closed_5m[-1].close > 1000.0:
                return self._closed_5m[-1].close
            if self._closed_15m and self._closed_15m[-1].close > 1000.0:
                return self._closed_15m[-1].close
        if self._historical_provider:
            try:
                hist_p = await self._historical_provider.get_latest_price(symbol)
                if hist_p > 1000.0:
                    return hist_p
            except Exception:
                pass
        return 0.0

    async def data_quality(self) -> DataQualityStatus:
        """Computes the current data-quality state of the live pipeline."""
        connected = False
        provider = "none"
        try:
            feeds = await self.health()
            if feeds:
                connected = bool(feeds[0].get("connected", False))
                provider = feeds[0].get("provider", "none")
        except Exception:
            pass

        latest_tick = await self.registry.latest_tick(self._symbol)

        async with self._lock:
            closed = list(self._closed_15m)
            count = len(closed)
            oldest = closed[0].timestamp if closed else None
            newest = closed[-1].timestamp if closed else None

        now = datetime.now(timezone.utc)
        fresh = False
        latest_ts = getattr(latest_tick, "timestamp", None)
        is_live_streaming = connected and latest_ts is not None and (now - latest_ts).total_seconds() < 300
        if newest is not None:
            fresh = ((now - newest) <= timedelta(minutes=self.settings.LIVE_HISTORY_MAX_AGE_MINUTES)) or is_live_streaming

        historical_available = count >= self.settings.LIVE_HISTORY_MIN_CANDLES

        degraded = False
        reasons: list = []
        if not historical_available:
            degraded = True
            reasons.append("Historical data unavailable.")
        if newest is not None and not fresh:
            degraded = True
            reasons.append(
                f"Historical data stale (newest candle {newest.isoformat()})."
            )
        if newest is None:
            degraded = True
            reasons.append("No closed candles available.")
        if self._live_price is None:
            degraded = True
            reasons.append("Live price unavailable.")
        if self._gap_count > self.settings.MAX_CANDLE_GAP_COUNT:
            degraded = True
            reasons.append(f"Candle gaps exceed threshold ({self._gap_count}).")
        if self._dup_count > 0 or self._ooo_count > 0:
            degraded = True
            reasons.append(
                f"Data integrity issue (duplicates={self._dup_count}, out_of_order={self._ooo_count})."
            )
        if not connected:
            degraded = True
            reasons.append("Live feed disconnected.")

        return DataQualityStatus(
            provider=provider,
            connected=connected,
            historical_available=historical_available,
            historical_fresh=fresh,
            candle_count=count,
            oldest_candle=oldest,
            newest_candle=newest,
            gap_count=self._gap_count,
            duplicate_count=self._dup_count,
            out_of_order_count=self._ooo_count,
            last_error=self._last_history_error,
            degraded=degraded,
            degradation_reason="; ".join(reasons) if reasons else "Healthy.",
            live_price=self._live_price,
            last_tick_at=latest_tick.timestamp if latest_tick else None,
            last_history_refresh_at=self._last_history_refresh_at,
            last_history_refresh_status=self._refresh_status,
            last_history_refresh_error=self._refresh_error,
            next_history_refresh_at=self._next_refresh_time(),
        )

    def _next_refresh_time(self) -> datetime | None:
        if self._last_history_refresh_at is None:
            return None
        interval = self.settings.LIVE_HISTORY_REFRESH_INTERVAL_MINUTES * 60
        from datetime import timedelta
        return self._last_history_refresh_at + timedelta(seconds=interval)

    async def health(self) -> list[dict]:
        return [h.model_dump(mode="json") for h in await self.registry.health()]


class LiveMarketDataAdaptor(MarketDataProvider):
    """Adapts the LiveMarketDataService to the MarketDataProvider interface.

    Allows the existing AnalysisPipeline to consume live data without
    any changes to the pipeline itself.
    """

    def __init__(self, service: LiveMarketDataService, symbol: str = "XAUUSD") -> None:
        self._service = service
        self._symbol = symbol

    async def get_latest_price(self, symbol: str) -> float:
        return await self._service.get_latest_price(symbol)

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: TimeFrame,
        limit: int = 200,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Candle]:
        snap = await self._service.get_multi_timeframe_snapshot(symbol, include_forming=False)
        tf_map = {
            TimeFrame.M5: snap.m5,
            TimeFrame.M15: snap.m15,
            TimeFrame.M30: snap.m30,
            TimeFrame.H1: snap.h1,
            TimeFrame.H4: snap.h4,
        }
        candles = tf_map.get(timeframe)
        if candles is None:
            return []
        if start_time is not None:
            candles = [c for c in candles if c.timestamp >= start_time]
        if end_time is not None:
            candles = [c for c in candles if c.timestamp <= end_time]
        return candles[-limit:] if limit > 0 else candles

    async def get_multi_timeframe_snapshot(
        self,
        symbol: str,
        as_of_time: datetime | None = None,
        m15_limit: int = 400,
    ) -> MultiTimeframeSnapshot:
        return await self._service.get_multi_timeframe_snapshot(symbol, include_forming=False)