"""
Side-by-side forward-observation service for research candidates.

On every closed candle this service evaluates ALL forward candidates
INDEPENDENTLY, persists their signals with versioned strategy IDs, tracks
outcomes, deduplicates, and provides comparison data.

The production strategy is NEVER modified by this service.
"""

from datetime import timedelta
from typing import Any

from app.config.settings import Settings, get_settings
from app.core.logging import logger
from app.data.models import MultiTimeframeSnapshot
from app.database.repository import Repository
from app.market_regime.detector import MarketRegimeDetector
from app.research.candidates import (
    ALL_FORWARD_CANDIDATES,
    ForwardCandidate,
    evaluate_candidate,
)
from app.research.outcome_tracker import SignalOutcomeTracker
from app.research.session import _session_for


class CandidateObservationService:
    """Evaluates all forward candidates on each closed candle, persists
    signals with versioned strategy IDs, deduplicates, and tracks outcomes.

    Completely independent of the production strategy.  ``OBSERVATION_MODE``
    must be ``true`` for this service to function (it reuses the same
    observation infrastructure).
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.outcome_tracker: SignalOutcomeTracker | None = None
        self._last_signal_ids: dict[str, str] = {}

    async def restore(self, repo: Repository) -> None:
        """Restore open candidate signals for outcome tracking."""
        if self.outcome_tracker is None:
            self.outcome_tracker = SignalOutcomeTracker(self.settings)
        restored = await self.outcome_tracker.restore(repo)
        if restored:
            logger.info("CandidateObservationService restored %s signals.", restored)

    async def process_candle(
        self, snapshot: MultiTimeframeSnapshot, repo: Repository,
    ) -> dict[str, dict[str, Any]]:
        """Evaluate all candidates on one closed candle.

        Returns ``{candidate_version: result_dict}`` for dashboard / reporting.
        """
        from app.services.status import get_status

        results = {}
        for cand in ALL_FORWARD_CANDIDATES:
            result = await self._evaluate_one(cand, snapshot, repo)
            results[cand.version] = result
            if result.get("signal_id"):
                await get_status().mark_signal(result["signal_id"])
        return results

    async def _evaluate_one(
        self, cand: ForwardCandidate, snapshot: MultiTimeframeSnapshot, repo: Repository,
    ) -> dict[str, Any]:
        """Evaluate one candidate; record signal + outcome if setup exists."""
        # Dedup
        if await self._is_duplicate(cand, snapshot, repo):
            return {"candidate": cand.version, "status": "DUPLICATE_SKIPPED"}

        sig = evaluate_candidate(snapshot, cand)
        if sig is None:
            return {"candidate": cand.version, "status": "NO_SETUP"}

        # Persist via the existing repository method
        sig_dict = {
            "id": sig.signal_id,
            "symbol": sig.instrument,
            "strategy": sig.strategy.value,
            "strategy_version": sig.strategy_version,
            "direction": sig.direction.value,
            "timeframe": sig.timeframe,
            "entry_price": sig.entry,
            "stop_loss": sig.stop_loss,
            "take_profit_1": sig.take_profit_1,
            "take_profit_2": sig.take_profit_2,
            "take_profit_3": sig.take_profit_3,
            "risk_reward": sig.risk_reward,
            "confidence_score": sig.confidence_score,
            "signal_quality": sig.signal_quality.value,
            "market_bias": sig.market_bias.value,
            "reasons": sig.reasons,
            "invalidation_conditions": sig.invalidation_conditions,
            "metadata_payload": {
                "strategy_version": sig.strategy_version,
                "confluence_score": sig.confidence_score,
                "candidate_name": cand.name,
                "setup_id": f"{cand.version}_{snapshot.timestamp.isoformat()}",
            },
        }
        try:
            await repo.save_signal(sig_dict)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Candidate %s persist failed: %s", cand.version, exc)
            return {"candidate": cand.version, "status": "PERSIST_FAILED", "error": str(exc)}

        # Stamp regime + session + register for outcome tracking
        try:
            regime_detector = MarketRegimeDetector(atr_period=self.settings.ATR_PERIOD)
            regime = regime_detector.analyze(snapshot.m15).regime.value
            session_name = _session_for(snapshot.timestamp.hour) if snapshot.timestamp else "UNKNOWN"

            await repo.update_signal_outcome(sig.signal_id, {
                "regime": regime,
                "session": session_name,
                "strategy_version": cand.version,
                "outcome": "OPEN",
            })

            if self.outcome_tracker is None:
                self.outcome_tracker = SignalOutcomeTracker(self.settings)
            self.outcome_tracker.register(sig)
            if self.outcome_tracker.open_count:
                base_series = snapshot.get_series(cand.mtf.base)
                if base_series:
                    await self.outcome_tracker.update(base_series, repo=repo)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Candidate %s outcome tracking failed: %s", cand.version, exc)

        self._last_signal_ids[cand.version] = sig.signal_id
        logger.info(
            "[CANDIDATE] %s %s entry=%.2f SL=%.2f conf=%.1f/%s regime=%s session=%s",
            cand.version, sig.direction.value, sig.entry, sig.stop_loss,
            sig.confidence_score, cand.conf_threshold, regime, session_name,
        )
        return {
            "candidate": cand.version,
            "status": "SIGNAL",
            "signal_id": sig.signal_id,
            "direction": sig.direction.value,
            "entry": sig.entry,
            "stop_loss": sig.stop_loss,
            "take_profit_1": sig.take_profit_1,
            "take_profit_2": sig.take_profit_2,
            "take_profit_3": sig.take_profit_3,
            "confidence_score": sig.confidence_score,
            "regime": regime,
            "session": session_name,
        }

    async def _is_duplicate(
        self, cand: ForwardCandidate, snapshot: MultiTimeframeSnapshot, repo: Repository,
    ) -> bool:
        """True if a signal already exists for this candidate + candle + direction."""
        if not snapshot.timestamp:
            return False
        from sqlalchemy import select

        from app.database.models import SignalModel

        ts = snapshot.timestamp
        margin_minutes = 2 if cand.mtf.base.value == "5m" else 10
        lo = ts - timedelta(minutes=margin_minutes)
        hi = ts + timedelta(minutes=margin_minutes)
        stmt = (
            select(SignalModel)
            .where(SignalModel.strategy_version == cand.version)
            .where(SignalModel.created_at >= lo)
            .where(SignalModel.created_at <= hi)
            .limit(1)
        )
        res = await repo.session.execute(stmt)
        return res.scalar_one_or_none() is not None