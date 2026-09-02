"""
Complete Trading & Analysis Pipeline Orchestrator.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.validator import AIValidator
from app.config.settings import Settings, get_settings
from app.confluence.engine import ConfluenceEngine
from app.core.constants import TimeFrame
from app.core.logging import logger
from app.data.provider import MarketDataProvider
from app.database.repository import Repository
from app.fibonacci.calculator import FibonacciEngine
from app.market_structure.detector import MarketStructureDetector
from app.notifications.telegram_service import TelegramService
from app.paper_trading.service import PaperTradingService
from app.risk.manager import RiskManager
from app.risk.models import RiskCalculationRequest
from app.signals.engine import SignalEngine
from app.smc.detector import SMCEngine


class AnalysisPipeline:
    """
    End-to-End Orchestrator:
    Data -> Structure -> SMC -> Fib -> Confluence -> Signal -> Risk -> AI Validation
    -> Paper Trading -> Alert -> DB
    """

    def __init__(self, data_provider: MarketDataProvider, settings: Settings | None = None):
        self.data_provider = data_provider
        self.settings = settings or get_settings()
        self._paper_restored = False
        self.ms_detector = MarketStructureDetector(atr_period=self.settings.ATR_PERIOD)
        self.fib_engine = FibonacciEngine()
        self.smc_engine = SMCEngine()
        self.confluence_engine = ConfluenceEngine(self.settings)
        self.signal_engine = SignalEngine(self.settings)
        self.risk_manager = RiskManager(self.settings)
        self.ai_validator = AIValidator(self.settings)
        self.telegram_service = TelegramService(self.settings)
        self.paper_service = PaperTradingService(
            initial_balance=self.settings.ACCOUNT_BALANCE, settings=self.settings
        )

    async def run_full_analysis(
        self,
        symbol: str = "XAUUSD",
        db_session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        """Runs the complete analysis cycle across all engines."""
        # 1. Fetch Multi-Timeframe Snapshot
        snapshot = await self.data_provider.get_multi_timeframe_snapshot(symbol)

        # 2. Timeframe Structural Analysis
        h4_struct = self.ms_detector.analyze(snapshot.h4, TimeFrame.H4)
        h1_struct = self.ms_detector.analyze(snapshot.h1, TimeFrame.H1)
        m30_fib = self.fib_engine.evaluate_setup(snapshot.m30, trend_bias=h4_struct.trend)
        m30_smc = self.smc_engine.analyze(snapshot.m30, TimeFrame.M30)
        m15_struct = self.ms_detector.analyze(snapshot.m15, TimeFrame.M15)

        # 3. Confluence & Signal Generation
        confluence = self.confluence_engine.evaluate(snapshot)
        signal = self.signal_engine.generate_signal(snapshot)

        # 4. Risk & Position Sizing
        pos_size = None
        if signal.is_tradable:
            risk_req = RiskCalculationRequest(
                account_balance=self.settings.ACCOUNT_BALANCE,
                risk_percent=self.settings.RISK_PERCENT,
                entry_price=signal.entry,
                stop_loss=signal.stop_loss,
                take_profit_1=signal.take_profit_1,
                take_profit_2=signal.take_profit_2,
                take_profit_3=signal.take_profit_3,
                direction=signal.direction,
                contract_size=self.settings.LOT_CONTRACT_SIZE,
            )
            pos_size = self.risk_manager.calculate_position_size(risk_req)

        # 5. AI Validation
        ai_validation = await self.ai_validator.validate_signal(signal)

        # 6. Paper Trading (persistence + position management)
        repo = Repository(db_session) if db_session else None
        if repo is not None and not self._paper_restored:
            await self.paper_service.restore_from_db(repo)
            self._paper_restored = True

        # 6a. Monitor open positions against the latest closed candle
        if snapshot.m15 and self.paper_service.get_active_positions():
            await self.paper_service.on_candle(snapshot.m15[-1], repo=repo)

        # 7. Persistence: Legacy pipeline signals are disabled in favor of dedicated Layered Strategies (SMC With Fib & Fib With Retracement)
        # Only persist if explicitly enabled via settings.ENABLE_LEGACY_SIGNALS (default False)
        if db_session and getattr(self.settings, "ENABLE_LEGACY_SIGNALS", False):
            sig_dict = {
                "id": signal.signal_id,
                "symbol": signal.instrument,
                "strategy": signal.strategy.value,
                "strategy_version": signal.strategy_version,
                "direction": signal.direction.value,
                "timeframe": signal.timeframe,
                "entry_price": signal.entry,
                "stop_loss": signal.stop_loss,
                "take_profit_1": signal.take_profit_1,
                "take_profit_2": signal.take_profit_2,
                "take_profit_3": signal.take_profit_3,
                "risk_reward": signal.risk_reward,
                "confidence_score": signal.confidence_score,
                "signal_quality": signal.signal_quality.value,
                "market_bias": signal.market_bias.value,
                "reasons": signal.reasons,
                "invalidation_conditions": signal.invalidation_conditions,
                "metadata_payload": {
                    **signal.detected_structures,
                    "explanation": signal.explanation,
                },
            }
            await repo.save_signal(sig_dict)

            ai_dict = {
                "signal_id": signal.signal_id,
                "status": ai_validation.status.value,
                "confidence": ai_validation.confidence,
                "explanation": ai_validation.explanation,
                "identified_risks": ai_validation.identified_risks,
                "missing_confirmations": ai_validation.missing_confirmations,
                "raw_response": ai_validation.raw_response,
                "provider": getattr(ai_validation, "provider", "HEURISTIC"),
                "model": getattr(ai_validation, "model", ""),
                "reason_code": getattr(ai_validation, "reason_code", ""),
            }
            await repo.save_ai_validation(ai_dict)

        # Log cycle
        logger.info(
            f"[{snapshot.timestamp.strftime('%Y-%m-%d %H:%M')}] {symbol} | "
            f"4H Bias: {h4_struct.trend.value} | 1H: {h1_struct.trend.value} | "
            f"Confluence: {confluence.total_score} | Signal: {signal.direction.value} | AI: {ai_validation.status.value}"
        )

        return {
            "symbol": symbol,
            "timestamp": snapshot.timestamp,
            "current_price": snapshot.current_price,
            "market_bias": {
                "4h": h4_struct.model_dump(),
                "1h": h1_struct.model_dump(),
                "15m": m15_struct.model_dump(),
            },
            "fibonacci_setup": m30_fib.model_dump() if m30_fib else None,
            "smc_analysis": m30_smc.model_dump(),
            "confluence": confluence.model_dump(),
            "signal": signal.model_dump(),
            "position_sizing": pos_size.model_dump() if pos_size else None,
            "ai_validation": ai_validation.model_dump(),
            "explanation": signal.explanation,
        }
