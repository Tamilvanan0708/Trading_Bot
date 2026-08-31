"""
Signal outcome tracker (forward validation).

Tracks what actually happens AFTER every LONG/SHORT signal: max favorable /
adverse excursion in R, which targets were reached, whether the stop was hit,
time to outcome, and the final R.

This is independent of paper trading: it answers "if this signal had been taken,
what would the outcome have been?" for every generated signal, using only closed
candles observed after the signal timestamp (no look-ahead).

State is persisted back into the ``signals`` table so it survives restarts and
is available to the research engine as real forward data.
"""

from datetime import datetime, timezone

from app.config.settings import Settings, get_settings
from app.core.constants import SignalDirection
from app.core.logging import logger
from app.data.models import Candle
from app.database.repository import Repository
from app.signals.models import SignalPayload


class _TrackedSignal:
    """In-memory forward-outcome state for one signal."""

    __slots__ = (
        "created_at",
        "direction",
        "entry",
        "last_ts",
        "risk",
        "signal_id",
        "stop_loss",
        "tp1",
        "tp2",
        "tp3",
    )

    def __init__(self, signal_id: str, direction: SignalDirection, entry: float,
                 stop_loss: float, tp1: float, tp2: float, tp3: float,
                 created_at: datetime | None = None):
        self.signal_id = signal_id
        self.direction = direction
        self.entry = entry
        self.stop_loss = stop_loss
        self.tp1 = tp1
        self.tp2 = tp2
        self.tp3 = tp3
        self.risk = max(0.01, abs(entry - stop_loss))
        self.created_at = created_at or datetime.now(timezone.utc)
        self.last_ts = None  # last candle timestamp already processed


class SignalOutcomeTracker:
    """Tracks forward outcomes of all generated LONG/SHORT signals."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._tracked: dict[str, _TrackedSignal] = {}

    @staticmethod
    def _aware(dt: datetime | None) -> datetime | None:
        """Normalizes a datetime to timezone-aware UTC (SQLite stores naive)."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    # ------------------------------------------------------------------
    # Registration / restore
    # ------------------------------------------------------------------

    def register(self, signal: SignalPayload) -> None:
        """Register a signal for forward-outcome tracking."""
        if signal.direction not in (SignalDirection.LONG, SignalDirection.SHORT):
            return
        if not signal.stop_loss or signal.entry <= 0:
            return
        if signal.signal_id in self._tracked:
            return
        self._tracked[signal.signal_id] = _TrackedSignal(
            signal_id=signal.signal_id,
            direction=signal.direction,
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            tp1=signal.take_profit_1,
            tp2=signal.take_profit_2,
            tp3=signal.take_profit_3,
            created_at=self._aware(signal.timestamp),
        )

    async def restore(self, repo: Repository) -> int:
        """Load open signals from the database so tracking survives restarts."""
        open_signals = await repo.list_open_signals_for_tracking(limit=500)
        restored = 0
        for row in open_signals:
            try:
                direction = SignalDirection(row.direction)
            except ValueError:
                continue
            if direction not in (SignalDirection.LONG, SignalDirection.SHORT):
                continue
            if not row.stop_loss or row.entry_price <= 0:
                continue
            ts = _TrackedSignal(
                signal_id=row.id,
                direction=direction,
                entry=row.entry_price,
                stop_loss=row.stop_loss,
                tp1=row.take_profit_1,
                tp2=row.take_profit_2,
                tp3=row.take_profit_3,
                created_at=self._aware(row.created_at),
            )
            # Continue from the newest candle at/after the signal timestamp so
            # no candle is double-processed after a restart.
            ts.last_ts = self._aware(row.created_at)
            self._tracked[row.id] = ts
            restored += 1
        if restored:
            logger.info("SignalOutcomeTracker restored %s open signals.", restored)
        return restored

    @property
    def open_count(self) -> int:
        return len(self._tracked)

    # ------------------------------------------------------------------
    # Candle processing
    # ------------------------------------------------------------------

    async def update(self, candles: list[Candle], repo: Repository | None = None) -> int:
        """Process new closed candles against all tracked signals.

        Returns the number of signals whose outcome was finalised in this call.
        """
        if not candles:
            return 0
        # Candles are assumed chronological; find all new candle timestamps.
        new_candles = [c for c in candles if c.timestamp is not None]
        if not new_candles:
            return 0
        # Normalize candle timestamps (SQLite may store naive datetimes).
        for c in new_candles:
            c.timestamp = self._aware(c.timestamp)

        finalized = 0
        for ts in list(self._tracked.keys()):
            sig = self._tracked[ts]
            # Only consider candles strictly after the signal timestamp.
            relevant = [
                c for c in new_candles
                if c.timestamp > sig.created_at and (sig.last_ts is None or c.timestamp > sig.last_ts)
            ]
            if not relevant:
                continue

            result = self._evaluate(sig, relevant)
            if repo is not None and (result["finalized"] or result["mfe_r"] is not None):
                await self._persist(repo, sig, result)

            if result["finalized"]:
                del self._tracked[ts]
                finalized += 1
            else:
                sig.last_ts = relevant[-1].timestamp

        return finalized

    def _evaluate(self, sig: _TrackedSignal, candles: list[Candle]) -> dict:
        """Single-pass SL-priority evaluation of a signal over new candles."""
        mae_r = 0.0
        mfe_r = 0.0
        outcome = None
        exit_price = None
        exit_ts = None
        max_r = 0.0

        for c in candles:
            if sig.direction == SignalDirection.LONG:
                mae = (sig.entry - c.low) / sig.risk
                mfe = (c.high - sig.entry) / sig.risk
            else:
                mae = (c.high - sig.entry) / sig.risk
                mfe = (sig.entry - c.low) / sig.risk
            mae_r = max(mae_r, round(mae, 3))
            mfe_r = max(mfe_r, round(mfe, 3))
            max_r = max(max_r, mfe_r)

            # Same-candle SL priority (conservative).
            if sig.direction == SignalDirection.LONG:
                sl_hit = c.low <= sig.stop_loss
                tp1_hit = c.high >= sig.tp1
                tp2_hit = c.high >= sig.tp2
                tp3_hit = c.high >= sig.tp3
            else:
                sl_hit = c.high >= sig.stop_loss
                tp1_hit = c.low <= sig.tp1
                tp2_hit = c.low <= sig.tp2
                tp3_hit = c.low <= sig.tp3

            if sl_hit:
                outcome = "SL"
                exit_price = sig.stop_loss
                exit_ts = c.timestamp
                break
            if tp3_hit:
                outcome = "TP3"
                exit_price = sig.tp3
                exit_ts = c.timestamp
                break
            if tp2_hit:
                outcome = "TP2"
                exit_price = sig.tp2
                exit_ts = c.timestamp
                break
            if tp1_hit:
                outcome = "TP1"
                exit_price = sig.tp1
                exit_ts = c.timestamp
                break

        final_r = None
        if outcome == "SL":
            final_r = -1.0
        elif outcome and exit_price is not None:
            if sig.direction == SignalDirection.LONG:
                final_r = round((exit_price - sig.entry) / sig.risk, 3)
            else:
                final_r = round((sig.entry - exit_price) / sig.risk, 3)

        hours = None
        if exit_ts is not None:
            hours = round((exit_ts - sig.created_at).total_seconds() / 3600.0, 2)

        return {
            "finalized": outcome is not None,
            "outcome": outcome,
            "mae_r": round(mae_r, 3),
            "mfe_r": round(mfe_r, 3),
            "max_r": round(max_r, 3),
            "final_r": final_r,
            "time_to_outcome_hours": hours,
            "sl_hit": outcome == "SL",
            "tp1_hit": outcome == "TP1",
            "tp2_hit": outcome == "TP2",
            "tp3_hit": outcome == "TP3",
            "outcome_at": exit_ts,
        }

    async def _persist(self, repo: Repository, sig: _TrackedSignal, result: dict) -> None:
        updates: dict = {
            "max_favorable_excursion_r": result["mfe_r"],
            "max_adverse_excursion_r": result["mae_r"],
            "max_r_achieved": result["max_r"],
            "outcome_updated_at": datetime.now(timezone.utc),
        }
        if result["finalized"]:
            updates.update({
                "outcome": result["outcome"],
                "tp1_hit": result["tp1_hit"],
                "tp2_hit": result["tp2_hit"],
                "tp3_hit": result["tp3_hit"],
                "sl_hit": result["sl_hit"],
                "final_r": result["final_r"],
                "time_to_outcome_hours": result["time_to_outcome_hours"],
            })
        else:
            updates["outcome"] = "OPEN"
        await repo.update_signal_outcome(sig.signal_id, updates)
