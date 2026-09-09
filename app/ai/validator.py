"""
AI Validation Layer — multi-provider orchestrator with deterministic guardrails.

Validates deterministic signals using:
  1. Provider chain (Groq → Gemini → OpenRouter → Ollama → BAI) when configured.
  2. Deterministic heuristic fallback when AI_PROVIDER=mock (no LLM keys set).
  3. Hard guardrails that the AI layer can never override.
"""

from app.ai.models import AIValidationResult
from app.ai.providers import (
    ProviderResult,
    get_provider_status,
    run_validation_chain,
)
from app.config.settings import Settings, get_settings
from app.core.constants import AIValidationStatus, SignalDirection, SignalQuality
from app.core.logging import logger
from app.signals.models import SignalPayload


class AIValidator:
    """Validates deterministic signals using LLM providers or heuristic fallback."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    async def validate_signal(self, signal: SignalPayload) -> AIValidationResult:
        """Validate a signal payload and return the structured result."""
        if not signal.is_tradable or signal.direction == SignalDirection.NO_TRADE:
            return AIValidationResult(
                status=AIValidationStatus.REJECT,
                confidence=95.0,
                explanation="Deterministic engine flagged setup as NO_TRADE or below minimum confidence threshold.",
                identified_risks=["Insufficient confluence score", "Unresolved timeframe conflicts"],
                missing_confirmations=["Multi-timeframe structural alignment"],
                provider="HEURISTIC",
            )

        context = self._build_context(signal)
        provider_mode = self.settings.AI_PROVIDER

        # Deterministic heuristic mode (no configured LLM providers).
        if provider_mode == "mock" or not self._any_provider_configured():
            result = self._heuristic_validation(signal, context)
            return self._enforce_guardrails(signal, result)

        # Provider chain mode.
        use_providers = self._get_configured_provider_modes(provider_mode)
        if not use_providers:
            result = self._heuristic_validation(signal, context)
            return self._enforce_guardrails(signal, result)

        chain_result = await run_validation_chain(
            self.settings, context, signal.signal_id, mode=provider_mode,
        )

        if chain_result.status == "UNAVAILABLE":
            return self._enforce_guardrails(signal, AIValidationResult(
                status=AIValidationStatus.UNAVAILABLE,
                confidence=0.0,
                explanation=chain_result.reason or "ALL_AI_PROVIDERS_UNAVAILABLE",
                identified_risks=chain_result.risks or [],
                missing_confirmations=chain_result.missing or [],
                provider=chain_result.provider or "NONE",
                model=chain_result.model or "",
                fallback=chain_result.fallback or [],
                raw_response=chain_result.raw_response,
                request_id=chain_result.request_id,
                reason_code=chain_result.reason_code,
                advisory_only=True,
            ))

        return self._enforce_guardrails(signal, AIValidationResult(
            status=self._parse_status(chain_result.status),
            confidence=chain_result.score,
            explanation=chain_result.reason,
            identified_risks=chain_result.risks or [],
            missing_confirmations=chain_result.missing or [],
            provider=chain_result.provider,
            model=chain_result.model,
            fallback=chain_result.fallback or [],
            raw_response=chain_result.raw_response,
            request_id=chain_result.request_id,
            reason_code=chain_result.reason_code,
            advisory_only=True,
        ))

    def _parse_status(self, raw: str) -> AIValidationStatus:
        try:
            return AIValidationStatus(raw)
        except ValueError:
            return AIValidationStatus.CAUTION

    def _build_context(self, signal: SignalPayload) -> dict:
        """Normalized trading context for the AI provider."""
        return {
            "signal_id": signal.signal_id,
            "instrument": signal.instrument,
            "direction": signal.direction.value,
            "strategy": signal.strategy.value,
            "timeframe": signal.timeframe,
            "entry": signal.entry,
            "stop_loss": signal.stop_loss,
            "take_profit_1": signal.take_profit_1,
            "take_profit_2": signal.take_profit_2,
            "take_profit_3": signal.take_profit_3,
            "risk_reward": signal.risk_reward,
            "confidence_score": signal.confidence_score,
            "signal_quality": signal.signal_quality.value,
            "market_bias": signal.market_bias.value,
            "reasons": signal.reasons,
            "detected_structures": signal.detected_structures,
            "fibonacci_levels": signal.fibonacci_levels,
            "liquidity_levels": signal.liquidity_levels,
            "explanation": signal.explanation,
        }

    def _any_provider_configured(self) -> bool:
        """Check if any LLM provider (Groq, Gemini, OpenRouter, Ollama, BAI,
        or the legacy OpenAI-compatible key) has a key/base URL set."""
        s = self.settings
        return bool(
            getattr(s, "GROQ_API_KEY", "")
            or getattr(s, "GEMINI_API_KEY", "")
            or getattr(s, "OPENROUTER_API_KEY", "")
            or getattr(s, "OLLAMA_BASE_URL", "")
            or getattr(s, "BAI_API_KEY", "")
            or getattr(s, "AI_API_KEY", "")
            or getattr(s, "AI_API_BASE_URL", "")
        )

    def _get_configured_provider_modes(self, mode: str) -> list[str]:
        """Return the list of provider modes applicable for the given setting."""
        if mode == "auto":
            return ["auto"]
        if mode == "mock":
            return []
        # Single-provider backward compat: openai→BAI, gemini→Gemini, etc.
        return [mode]

    def _enforce_guardrails(self, signal: SignalPayload, result: AIValidationResult) -> AIValidationResult:
        """Deterministic guardrails that the AI layer can never override."""
        base_risks = list(result.identified_risks)
        new_risks: list = []

        if result.status == AIValidationStatus.UNAVAILABLE:
            return result

        if signal.risk_reward < self.settings.MIN_RISK_REWARD:
            new_risks.append(
                f"Blocked by guardrail: R:R {signal.risk_reward} below minimum {self.settings.MIN_RISK_REWARD}."
            )

        if signal.signal_quality not in (SignalQuality.STRONG, SignalQuality.VERY_STRONG):
            new_risks.append(
                f"Blocked by guardrail: quality {signal.signal_quality.value} below STRONG threshold."
            )

        if new_risks and result.status == AIValidationStatus.APPROVE:
            return result.model_copy(
                update={
                    "status": AIValidationStatus.CAUTION,
                    "identified_risks": new_risks + base_risks,
                }
            )
        if new_risks:
            return result.model_copy(update={"identified_risks": new_risks + base_risks})
        return result

    def _heuristic_validation(self, signal: SignalPayload, context: dict) -> AIValidationResult:
        """Deterministic qualitative validator providing reliable offline reasoning."""
        risks = []
        missing = []
        conf = signal.confidence_score
        min_rr = self.settings.MIN_RISK_REWARD

        if signal.risk_reward < min_rr:
            risks.append(f"Sub-optimal Risk/Reward ratio (1:{signal.risk_reward}). Minimum target is 1:{min_rr}")

        sl_dist = abs(signal.entry - signal.stop_loss)
        if sl_dist > 25.0:
            risks.append(f"Wide stop-loss distance (${sl_dist:.2f}) requires smaller position sizing.")
        elif sl_dist < 1.5:
            risks.append("Tight stop-loss prone to gold market volatility spikes.")

        if signal.signal_quality == SignalQuality.VERY_STRONG:
            status = AIValidationStatus.APPROVE
            conf = min(98.0, conf + 5.0)
            explanation = (
                f"High-probability {signal.direction.value} setup on XAU/USD. "
                f"Strong confluence ({signal.confidence_score}/100) across 4H macro bias, 1H structure, "
                f"and 15M trigger. Projected R:R is 1:{signal.risk_reward}."
            )
        elif signal.signal_quality == SignalQuality.STRONG:
            status = AIValidationStatus.APPROVE
            explanation = (
                f"Solid {signal.direction.value} setup confirmed by {signal.strategy.value}. "
                f"Confluence score is {signal.confidence_score}/100. Trade with standard 1% risk."
            )
        else:
            status = AIValidationStatus.CAUTION
            missing.append("Additional lower-timeframe candle volume confirmation")
            explanation = (
                f"Moderate {signal.direction.value} setup. Some confluence conditions are met, "
                f"but exercise caution due to lower overall structural momentum."
            )

        return AIValidationResult(
            status=status,
            confidence=conf,
            explanation=explanation,
            reasoning=f"{signal.direction.value} setup on {signal.instrument} with {signal.confidence_score}/100 confluence and 1:{signal.risk_reward} R:R.",
            risk_flags=risks if risks else [],
            identified_risks=risks if risks else ["Normal market volatility risk"],
            missing_confirmations=missing,
            raw_response="HEURISTIC_VALIDATOR",
            provider="HEURISTIC",
        )

    validate = validate_signal


_global_ai_validator: AIValidator | None = None


def get_ai_validator() -> AIValidator:
    """Return the global singleton instance of AIValidator."""
    global _global_ai_validator
    if _global_ai_validator is None:
        _global_ai_validator = AIValidator()
    return _global_ai_validator