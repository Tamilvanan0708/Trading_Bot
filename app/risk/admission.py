"""
Authoritative, explainable trade-admission gate.

A paper trade opens ONLY when every condition passes.  Every rejection is
recorded with a human-readable reason so the system can explain why a trade
did NOT happen.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config.settings import Settings, get_settings
from app.core.constants import SignalDirection, SignalQuality
from app.data.models import DataQualityStatus
from app.database.repository import Repository
from app.market_regime.detector import RegimeAnalysis
from app.news.filter import NewsFilter
from app.paper_trading.limits import LimitDecision, TradingLimits
from app.signals.models import SignalPayload


@dataclass
class AdmissionDecision:
    """Outcome of the trade-admission gate."""
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    missing_confirmations: list[str] = field(default_factory=list)

    def add_pass(self, condition: str) -> None:
        self.reasons.append(f"PASS: {condition}")

    def add_fail(self, condition: str) -> None:
        self.reasons.append(f"FAIL: {condition}")

    @property
    def rejected_reason(self) -> str:
        fails = [r for r in self.reasons if r.startswith("FAIL")]
        return "; ".join(fails) if fails else "No trade required."


class TradeAdmissionGate:
    """Single authoritative gate for opening paper trades."""

    def __init__(
        self,
        settings: Settings | None = None,
        limits: TradingLimits | None = None,
        news_filter: NewsFilter | None = None,
        initial_balance: float | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.limits = limits or TradingLimits(self.settings)
        self.news_filter = news_filter or NewsFilter(self.settings)
        self._initial_balance = initial_balance

    async def evaluate(
        self,
        signal: SignalPayload,
        repo: Repository | None = None,
        snapshot_timestamp: datetime | None = None,
        market_data_fresh: bool = True,
        candle_closed: bool = True,
        regime: RegimeAnalysis | None = None,
        ai_status_ok: bool = True,
        has_conflicting_position: bool = False,
        is_duplicate: bool = False,
        data_quality: DataQualityStatus | None = None,
        account_balance: float | None = None,
        initial_balance: float | None = None,
    ) -> AdmissionDecision:
        """Evaluates all admission conditions and returns an explainable decision."""
        decision = AdmissionDecision(allowed=True)
        snapshot_ts = snapshot_timestamp or datetime.now(timezone.utc)

        # 1. Direction
        if signal.direction == SignalDirection.NO_TRADE:
            decision.allowed = False
            decision.add_fail("Direction is NO_TRADE.")
        else:
            decision.add_pass(f"Direction = {signal.direction.value}")

        # 2. Confluence score >= threshold
        if signal.confidence_score < self.settings.THRESHOLD_STRONG:
            decision.allowed = False
            decision.add_fail(
                f"Confluence score {signal.confidence_score} < STRONG threshold {self.settings.THRESHOLD_STRONG}."
            )
        else:
            decision.add_pass(f"Confluence score {signal.confidence_score} >= {self.settings.THRESHOLD_STRONG}")

        # 3. Signal quality
        if signal.signal_quality not in (SignalQuality.STRONG, SignalQuality.VERY_STRONG):
            decision.allowed = False
            decision.add_fail(f"Signal quality {signal.signal_quality.value} below STRONG.")

        # 4. R:R >= minimum
        if signal.risk_reward < self.settings.MIN_RISK_REWARD:
            decision.allowed = False
            decision.add_fail(f"R:R {signal.risk_reward} < minimum {self.settings.MIN_RISK_REWARD}.")
        else:
            decision.add_pass(f"R:R {signal.risk_reward} >= {self.settings.MIN_RISK_REWARD}")

        # 5-7. Valid entry / SL / TP geometry
        if signal.entry <= 0:
            decision.allowed = False
            decision.add_fail("Invalid (non-positive) entry price.")
        if signal.stop_loss <= 0:
            decision.allowed = False
            decision.add_fail("Invalid (non-positive) stop loss.")
        for tp in (signal.take_profit_1, signal.take_profit_2, signal.take_profit_3):
            if tp <= 0:
                decision.allowed = False
                decision.add_fail("Invalid (non-positive) take-profit.")
                break
        if signal.entry > 0 and signal.stop_loss > 0:
            if signal.direction == SignalDirection.LONG and signal.stop_loss >= signal.entry:
                decision.allowed = False
                decision.add_fail("LONG stop loss must be below entry.")
            if signal.direction == SignalDirection.SHORT and signal.stop_loss <= signal.entry:
                decision.allowed = False
                decision.add_fail("SHORT stop loss must be above entry.")

        # 8. Market data freshness
        if not market_data_fresh:
            decision.allowed = False
            decision.add_fail("Market data is stale.")

        # 9. Candle closed
        if not candle_closed:
            decision.allowed = False
            decision.add_fail("Candle is not fully closed.")

        # 10. AI validation
        if not ai_status_ok:
            decision.allowed = False
            decision.add_fail("AI validation did not approve the setup.")

        # 11. Conflicting active position
        if has_conflicting_position:
            decision.allowed = False
            decision.add_fail("Conflicting active position exists for the same direction.")

        # 12. Duplicate signal
        if is_duplicate:
            decision.allowed = False
            decision.add_fail("Duplicate signal — already processed for this candle.")

        # 13. Market regime filter
        if regime is not None and self.settings.REGIME_FILTER_ENABLED:
            allowed_regimes = [r.strip().upper() for r in self.settings.ALLOWED_REGIMES.split(",") if r.strip()]
            if regime.regime.value not in allowed_regimes:
                decision.allowed = False
                decision.add_fail(f"Market regime {regime.regime.value} not in allowed set {allowed_regimes}.")
            else:
                decision.add_pass(f"Market regime {regime.regime.value} allowed")

        # 14. News blackout
        if self.settings.NEWS_FILTER_ENABLED:
            try:
                blackout = await self.news_filter.in_blackout(snapshot_ts)
            except Exception:
                blackout = False
            if blackout:
                decision.allowed = False
                decision.add_fail("Inside news blackout window — no new trades.")

        # 15. Trading limits (max daily trades / loss / consecutive losses / total drawdown)
        if repo is not None:
            limit: LimitDecision = await self.limits.check_open_allowed(
                repo,
                current_balance=account_balance,
                initial_balance=initial_balance if initial_balance is not None else self._initial_balance,
            )
            if not limit.allowed:
                decision.allowed = False
                decision.add_fail(limit.reason)

        # 16. Data quality gate (hard safety)
        if data_quality is not None and data_quality.degraded:
            decision.allowed = False
            decision.add_fail(
                f"Data Quality Gate: FAILED. Reason: {data_quality.degradation_reason}"
            )

        return decision