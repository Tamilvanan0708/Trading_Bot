"""
RETRACEMENT_BOS_V1 — Forward Observation.

Observes ONLY newly arriving market data (no backfilling forward results from
historical candles).  Each detected signal is recorded immutably, and its
lifecycle (WAITING FOR ENTRY -> ENTRY TOUCHED -> TP FROZEN / TRADE ACTIVE ->
TP HIT / SL HIT / INVALIDATED) is tracked.

Forward results are advisory only — the strategy is NOT promoted to
production / real-money.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.retracement.engine import RetracementBOSEngine
from app.retracement.models import RetracementEventType, RetracementSetup, RetracementState

FORWARD_STORE = os.path.join(ROOT, "data", "retracement_dataset", "forward_observation.json")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_store() -> list[dict]:
    if not os.path.exists(FORWARD_STORE):
        return []
    try:
        with open(FORWARD_STORE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_store(records: list[dict]) -> None:
    os.makedirs(os.path.dirname(FORWARD_STORE), exist_ok=True)
    with open(FORWARD_STORE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, default=str)


class ForwardObservation:
    """Append-only forward signal lifecycle tracker.

    ``record_setup`` snapshots a newly detected setup.  ``advance`` updates the
    lifecycle as new candles arrive.  Records are never backfilled from
    historical data — only candles delivered after ``record_setup`` are used.
    """

    def __init__(self, symbol: str = "XAUUSD", timeframe: str = "15m"):
        self.symbol = symbol
        self.timeframe = timeframe
        self.engine = RetracementBOSEngine(symbol=symbol, timeframe=timeframe)
        self._records: list[dict] = _load_store()
        self._active: dict[str, RetracementSetup] = {}

    def record_setup(self, setup: RetracementSetup) -> dict:
        """Snapshot a newly detected signal into the forward store (immutable)."""
        if setup.state in (RetracementState.COMPLETED, RetracementState.INVALIDATED):
            status = "RESOLVED"
        elif setup.entry_touched and setup.tp_locked:
            status = "TP FROZEN / TRADE ACTIVE"
        elif setup.entry_touched:
            status = "ENTRY TOUCHED"
        else:
            status = "WAITING FOR ENTRY"

        rec = {
            "forward_id": str(uuid.uuid4()),
            "setup_id": setup.setup_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "detected_at": utcnow().isoformat(),
            "state": setup.state.value,
            "status": status,
            "bos_price": setup.bos_price,
            "bos_timestamp": setup.bos_timestamp.isoformat() if setup.bos_timestamp else None,
            "point_1_price": setup.point_1_price,
            "point_2_price": setup.point_2_price,
            "point_2_timestamp": setup.point_2_timestamp.isoformat() if setup.point_2_timestamp else None,
            "entry_price": setup.entry_price,
            "sl_price": setup.sl_price,
            "dynamic_tp": setup.dynamic_tp,
            "locked_tp": setup.locked_tp,
            "tp_locked": setup.tp_locked,
            "entry_touched": setup.entry_touched,
            "outcome": setup.outcome,
            "invalidation_reason": setup.invalidation_reason or None,
        }
        self._records.append(rec)
        self._active[setup.setup_id] = setup
        _save_store(self._records)
        return rec

    def advance(self, setup_id: str, candle_ts: datetime) -> dict | None:
        """Advance one active forward setup's lifecycle using a NEW candle only."""
        setup = self._active.get(setup_id)
        if setup is None:
            return None
        # Find the matching forward record (immutable base; only status updates)
        rec = next((r for r in self._records if r["setup_id"] == setup_id), None)
        if rec is None:
            return None
        rec["last_seen"] = candle_ts.isoformat()
        if setup.state in (RetracementState.COMPLETED, RetracementState.INVALIDATED):
            rec["status"] = "RESOLVED"
            rec["outcome"] = setup.outcome
            rec["invalidation_reason"] = setup.invalidation_reason or rec.get("invalidation_reason")
        elif setup.entry_touched and setup.tp_locked:
            rec["status"] = "TP FROZEN / TRADE ACTIVE"
            rec["locked_tp"] = setup.locked_tp
        elif setup.entry_touched:
            rec["status"] = "ENTRY TOUCHED"
        # TP immutability is enforced by the engine; the forward record never
        # overwrites a previously locked TP with a later high.
        _save_store(self._records)
        return rec

    def active_signals(self) -> list[dict]:
        return [r for r in self._records if r["status"] in ("WAITING FOR ENTRY", "ENTRY TOUCHED", "TP FROZEN / TRADE ACTIVE")]

    def all_records(self) -> list[dict]:
        return self._records

    def summary(self) -> dict:
        records = self._records
        n = len(records)
        if n == 0:
            return {"signals": 0, "entries": 0, "tp_hits": 0, "sl_hits": 0,
                    "active": 0, "forward_expectancy": None, "status": "INSUFFICIENT DATA"}
        entries = sum(1 for r in records if r.get("entry_touched"))
        tp_hits = sum(1 for r in records if r.get("outcome") == "TP_HIT")
        sl_hits = sum(1 for r in records if r.get("outcome") == "SL_HIT")
        active = len(self.active_signals())
        # Forward expectancy only when resolved trades provide R
        rs = []
        for r in records:
            if r.get("outcome") == "TP_HIT" and r.get("entry_price") and r.get("sl_price") and r.get("locked_tp"):
                risk = abs(r["entry_price"] - r["sl_price"])
                if risk > 0:
                    rs.append((r["locked_tp"] - r["entry_price"]) / risk)
            elif r.get("outcome") == "SL_HIT" and r.get("entry_price") and r.get("sl_price"):
                risk = abs(r["entry_price"] - r["sl_price"])
                if risk > 0:
                    rs.append((r["sl_price"] - r["entry_price"]) / risk)
        fwd_exp = round(sum(rs) / len(rs), 4) if len(rs) >= MIN_FORWARD_SAMPLE else None
        return {
            "signals": n,
            "entries": entries,
            "tp_hits": tp_hits,
            "sl_hits": sl_hits,
            "active": active,
            "forward_expectancy": fwd_exp,
            "status": "STATISTICALLY SUPPORTED" if len(rs) >= MIN_FORWARD_SAMPLE else "INSUFFICIENT DATA",
        }


MIN_FORWARD_SAMPLE = 30
