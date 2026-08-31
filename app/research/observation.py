"""
Live observation store (OBSERVATION MODE).

Records every live signal WITHOUT opening a trade, so real forward-test
evidence accumulates without risking capital.  Hypothetical outcomes are
tracked as the live price evolves (MFE / MAE / eventual outcome).

Persisted to a JSON file so records survive restarts.
"""

import json
import os
import threading
from datetime import datetime, timezone


class LiveObservationStore:
    """Thread/async-safe JSON-backed store of hypothetical live signals."""

    def __init__(self, path: str = "data/research/observation_log.json"):
        self._path = path
        self._lock = threading.Lock()
        self._records: list[dict] = []
        self._load()

    def _load(self) -> None:
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding="utf-8") as f:
                    data = json.load(f)
                self._records = data.get("records", [])
            except Exception:
                self._records = []

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({"records": self._records}, f, indent=2, default=str)

    def record_signal(self, record: dict) -> None:
        """Store a new hypothetical signal observation."""
        with self._lock:
            record.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
            record["outcome"] = "OPEN"
            record["hypothetical_pnl_r"] = 0.0
            record["mfe_r"] = 0.0
            record["mae_r"] = 0.0
            entry = record.get("entry")
            record["observed_high"] = float(entry) if entry else None
            record["observed_low"] = float(entry) if entry else None
            self._records.append(record)
            self._save()

    def update_price(self, symbol: str, price: float) -> None:
        """Update OPEN observations with current MFE/MAE from the live price.

        Tracks running extremes (observed high/low) so the eventual outcome is
        determined by the path the price took, not just its current value —
        if TP was reached first and then price retraced through SL, the record
        correctly stays a TP (no SL-first survivorship bias).
        """
        with self._lock:
            for r in self._records:
                if r.get("outcome") != "OPEN" or r.get("instrument") != symbol:
                    continue
                entry = r.get("entry")
                sl = r.get("stop_loss")
                tp = r.get("take_profit_2") or r.get("take_profit_1")
                if entry is None or not entry:
                    continue
                risk = abs(entry - sl) if sl else 0.0
                if risk <= 0:
                    risk = 1.0
                direction = r.get("direction")
                # Update running extremes
                r["observed_high"] = max(float(r.get("observed_high") or entry), float(price))
                r["observed_low"] = min(float(r.get("observed_low") or entry), float(price))
                if direction == "LONG":
                    mfe = max(0.0, float(r["observed_high"]) - entry)
                    mae = max(0.0, entry - float(r["observed_low"]))
                else:
                    mfe = max(0.0, entry - float(r["observed_low"]))
                    mae = max(0.0, float(r["observed_high"]) - entry)
                r["mfe_r"] = round(mfe / risk, 3)
                r["mae_r"] = round(mae / risk, 3)
                # Hypothetical outcome: SL and TP both checked against the
                # observed path.  SL-first is conservative and mirrors the
                # backtester's same-candle SL priority.
                sl_hit = sl and ((direction == "LONG" and float(r["observed_low"]) <= sl)
                                 or (direction == "SHORT" and float(r["observed_high"]) >= sl))
                tp_hit = tp and ((direction == "LONG" and float(r["observed_high"]) >= tp)
                                 or (direction == "SHORT" and float(r["observed_low"]) <= tp))
                if sl_hit:
                    r["outcome"] = "SL"
                    r["hypothetical_pnl_r"] = -1.0
                    r["closed_at"] = datetime.now(timezone.utc).isoformat()
                elif tp_hit:
                    r["outcome"] = "TP"
                    r["hypothetical_pnl_r"] = round((tp - entry) / risk if direction == "LONG" else (entry - tp) / risk, 3)
                    r["closed_at"] = datetime.now(timezone.utc).isoformat()
            self._save()

    def summary(self) -> dict:
        """Aggregate observation statistics."""
        with self._lock:
            records = list(self._records)
        total = len(records)
        closed = [r for r in records if r.get("outcome") in ("SL", "TP")]
        longs = [r for r in records if r.get("direction") == "LONG"]
        shorts = [r for r in records if r.get("direction") == "SHORT"]
        wins = [r for r in closed if r.get("hypothetical_pnl_r", 0) > 0]
        projected = sum(1 for r in records if r.get("outcome") == "OPEN")
        return {
            "total_signals": total,
            "long": len(longs),
            "short": len(shorts),
            "no_trade": total - len(longs) - len(shorts),
            "closed": len(closed),
            "open": projected,
            "win_rate_pct": round(len(wins) / len(closed) * 100.0, 2) if closed else 0.0,
            "hypothetical_pnl_r": round(sum(r.get("hypothetical_pnl_r", 0) for r in closed), 2),
            "avg_mfe_r": round(sum(r.get("mfe_r", 0) for r in records) / total, 3) if total else 0.0,
            "avg_mae_r": round(sum(r.get("mae_r", 0) for r in records) / total, 3) if total else 0.0,
        }


_observation_store: LiveObservationStore | None = None


def get_observation_store() -> LiveObservationStore:
    global _observation_store
    if _observation_store is None:
        _observation_store = LiveObservationStore()
    return _observation_store