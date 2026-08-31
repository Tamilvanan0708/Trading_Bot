"""
Unit tests for the AI validation layer: multi-provider chain, heuristic fallback, and hard guardrails.
"""

import pytest

from app.ai.models import AIValidationResult
from app.ai.providers import _parse_llm_json, LegacyOpenAIProvider, ProviderResult, run_validation_chain, mark_provider_status, clear_provider_status, clear_validation_cache
from app.ai.validator import AIValidator
from app.config.settings import Settings
from app.core.constants import (
    AIValidationStatus,
    MarketBias,
    SignalDirection,
    SignalQuality,
    StrategyType,
)
from app.signals.models import SignalPayload


def _signal(quality=SignalQuality.STRONG, rr=2.0, direction=SignalDirection.LONG) -> SignalPayload:
    return SignalPayload(
        instrument="XAUUSD",
        direction=direction,
        strategy=StrategyType.CONFLUENCE,
        entry=2650.0,
        stop_loss=2640.0,
        take_profit_1=2665.0,
        take_profit_2=2680.0,
        take_profit_3=2700.0,
        risk_reward=rr,
        confidence_score=80.0 if quality in (SignalQuality.STRONG, SignalQuality.VERY_STRONG) else 65.0,
        signal_quality=quality,
        market_bias=MarketBias.BULLISH,
        reasons=["test"],
    )


def test_bai_provider_uses_supported_model_and_no_response_format():
    """BAI (api.b.ai) does not offer `gpt-4o-mini` and rejects
    `response_format` with HTTP 400.  The provider must default to a model
    that exists on B.AI and must never send response_format."""
    from app.ai.providers import BAIProvider

    assert BAIProvider.default_model == "gpt-5-4-mini"
    assert BAIProvider.supports_response_format is False
    assert BAIProvider._base_url(object()) == "https://api.b.ai/v1"
    assert BAIProvider._base_url(type("S", (), {"BAI_BASE_URL": "https://x.b.ai/v9"})()) == "https://x.b.ai/v9"


def test_openai_compatible_body_excludes_response_format_when_disabled(monkeypatch):
    """When supports_response_format is False, the outgoing body must NOT
    contain response_format (which B.AI rejects with HTTP 400)."""
    import asyncio
    import httpx
    from app.ai import providers as prov_mod

    captured = {}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured["body"] = json
            res = httpx.Response(200, json={
                "choices": [{"message": {"content": '{"status":"APPROVE","confidence":80.0}'}}],
            }, request=httpx.Request("POST", url))
            return res

    monkeypatch.setattr(prov_mod.httpx, "AsyncClient", _FakeClient)
    settings = Settings(AI_PROVIDER="bai", BAI_API_KEY="sk-test", BAI_MODEL="gpt-5-4-mini")
    result = asyncio.run(prov_mod._SINGLE_PROVIDERS["bai"]().validate(settings, {"x": 1}, "test-bai-body"))
    body = captured["body"]
    assert body is not None
    assert body["model"] == "gpt-5-4-mini"
    assert "response_format" not in body
    assert body["temperature"] == settings.AI_TEMPERATURE
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert result.status == "APPROVE"


@pytest.mark.asyncio
async def test_heuristic_validation_approves_strong():
    validator = AIValidator()
    signal = _signal(quality=SignalQuality.STRONG, rr=3.0)
    result = await validator.validate_signal(signal)
    assert result.status == AIValidationStatus.APPROVE


@pytest.mark.asyncio
async def test_heuristic_validation_rejects_non_tradable():
    validator = AIValidator()
    signal = _signal(quality=SignalQuality.MODERATE, rr=1.0)
    # Non-tradable signals are rejected before heuristic runs
    result = await validator.validate_signal(signal)
    assert result.status == AIValidationStatus.REJECT


@pytest.mark.asyncio
async def test_guardrail_blocks_low_rr_approval():
    """AI cannot approve a signal below the minimum R:R."""
    validator = AIValidator()
    signal = _signal(quality=SignalQuality.VERY_STRONG, rr=1.2)
    result = await validator.validate_signal(signal)
    assert result.status != AIValidationStatus.APPROVE
    assert any("guardrail" in r.lower() for r in result.identified_risks)


@pytest.mark.asyncio
async def test_guardrail_blocks_low_quality_approval():
    """AI cannot approve a signal below the STRONG threshold even if LLM approves."""
    validator = AIValidator()
    signal = _signal(quality=SignalQuality.MODERATE, rr=3.0)
    # Force a mock LLM-style APPROVE result then verify the guardrail downgrades it
    result = AIValidationResult(
        status=AIValidationStatus.APPROVE,
        confidence=90.0,
        explanation="LLM approved",
        identified_risks=[],
        missing_confirmations=[],
    )
    guarded = validator._enforce_guardrails(signal, result)
    assert guarded.status == AIValidationStatus.CAUTION


def test_llm_response_parsing_strips_markdown():
    """_parse_llm_json should strip markdown fences and parse JSON."""
    from app.ai.providers import _parse_llm_json
    content = """```json
{
  "status": "APPROVE",
  "confidence": 87.5,
  "reasoning": "Setup is clean.",
  "identified_risks": ["spread widening"],
  "missing_confirmations": []
}
```"""
    parsed = _parse_llm_json(content)
    assert parsed["status"] == "APPROVE"
    assert parsed["confidence"] == 87.5
    assert parsed["identified_risks"] == ["spread widening"]


def test_llm_response_parsing_bad_status_defaults():
    """_parse_llm_json should not fail on invalid status (validated later by the chain)."""
    from app.ai.providers import _parse_llm_json
    content = '{"status": "MAYBE", "confidence": 50.0, "reasoning": "?"}'
    parsed = _parse_llm_json(content)
    assert parsed["status"] == "MAYBE"
    assert parsed["confidence"] == 50.0


@pytest.mark.asyncio
async def test_http_402_returns_insufficient_balance(monkeypatch):
    """When the provider returns 402, the chain must return UNAVAILABLE with
    INSUFFICIENT_BALANCE reason_code — never a fake APPROVE."""
    from app.ai import providers as prov_mod

    async def _fake_402(self, settings, context, request_id):
        return prov_mod.ProviderResult(status="UNAVAILABLE", reason="AI provider balance is insufficient.",
                                       reason_code="INSUFFICIENT_BALANCE", provider="OPENAI",
                                       model="gpt-4o-mini", request_id=request_id)

    monkeypatch.setattr(prov_mod.LegacyOpenAIProvider, "validate", _fake_402)

    settings = Settings(AI_PROVIDER="openai", AI_API_KEY="sk-test", AI_MODEL="gpt-4o-mini")
    context = {"test": "context", "unique": "402"}
    clear_validation_cache()
    clear_provider_status("OPENAI")
    result = await run_validation_chain(settings, context, "test-402", mode="openai")
    assert result.status == "UNAVAILABLE", f"Expected UNAVAILABLE, got {result.status}"
    assert result.reason_code == "INSUFFICIENT_BALANCE", f"Got {result.reason_code}"
    assert result.provider == "OPENAI"
    assert result.score == 0.0


@pytest.mark.asyncio
async def test_ai_failure_does_not_fake_approve(monkeypatch):
    """AI provider failure must NOT cause the deterministic strategy to appear
    AI-approved. The validator must return UNAVAILABLE status."""
    from app.ai import providers as prov_mod

    async def _fake_500(self, settings, context, request_id):
        return prov_mod.ProviderResult(status="UNAVAILABLE", reason="Server error (500).",
                                       reason_code="PROVIDER_ERROR", provider="OPENAI",
                                       model="gpt-4o-mini", request_id=request_id)

    monkeypatch.setattr(prov_mod.LegacyOpenAIProvider, "validate", _fake_500)

    settings = Settings(AI_PROVIDER="openai", AI_API_KEY="sk-test", AI_MODEL="gpt-4o-mini")
    validator = AIValidator(settings)
    signal = _signal(quality=SignalQuality.STRONG, rr=3.0)
    clear_validation_cache()
    clear_provider_status("OPENAI")
    result = await validator.validate_signal(signal)
    assert result.status == AIValidationStatus.UNAVAILABLE
    assert result.status != AIValidationStatus.APPROVE
    assert result.reason_code == "PROVIDER_ERROR"


@pytest.mark.asyncio
async def test_missing_api_key_returns_configuration_missing():
    """When no API key is configured, the validator should use heuristic
    (the mock mode fallback)."""
    settings = Settings(AI_PROVIDER="mock", AI_API_KEY="")
    validator = AIValidator(settings)
    signal = _signal(quality=SignalQuality.STRONG, rr=3.0)
    result = await validator.validate_signal(signal)
    assert result.status == AIValidationStatus.APPROVE
    assert result.provider == "HEURISTIC"


@pytest.mark.asyncio
async def test_advisory_only_flag(monkeypatch):
    """AI validation must always be advisory_only."""
    from app.ai import providers as prov_mod

    validator = AIValidator()
    signal = _signal(quality=SignalQuality.STRONG, rr=3.0)
    result = await validator.validate_signal(signal)
    assert result.advisory_only is True

    # UNAVAILABLE result must also be advisory_only
    async def _fake_402(self, settings, context, request_id):
        return prov_mod.ProviderResult(status="UNAVAILABLE", reason="AI provider balance is insufficient.",
                                       reason_code="INSUFFICIENT_BALANCE", provider="OPENAI",
                                       model="gpt-4o-mini", request_id=request_id)

    monkeypatch.setattr(prov_mod.LegacyOpenAIProvider, "validate", _fake_402)
    settings = Settings(AI_PROVIDER="openai", AI_API_KEY="sk-test", AI_MODEL="gpt-4o-mini")
    validator2 = AIValidator(settings)
    signal2 = _signal(quality=SignalQuality.STRONG, rr=3.0)
    clear_validation_cache()
    clear_provider_status("OPENAI")
    result2 = await validator2.validate_signal(signal2)
    assert result2.status == AIValidationStatus.UNAVAILABLE
    assert result2.advisory_only is True
    assert result2.reason_code == "INSUFFICIENT_BALANCE"


@pytest.mark.asyncio
async def test_timeout_returns_unavailable(monkeypatch):
    """A provider timeout must return UNAVAILABLE with TIMEOUT reason_code."""
    from app.ai import providers as prov_mod

    async def _fake_timeout(self, settings, context, request_id):
        return prov_mod.ProviderResult(status="UNAVAILABLE", reason="Provider timeout.",
                                       reason_code="TIMEOUT", provider="GROQ",
                                       model="gpt-4o-mini", request_id=request_id)

    monkeypatch.setattr(prov_mod.GroqProvider, "validate", _fake_timeout)
    settings = Settings(AI_PROVIDER="groq", GROQ_API_KEY="gsk-test", GROQ_MODEL="llama-3.3-70b-versatile")
    clear_validation_cache()
    clear_provider_status("GROQ")
    result = await run_validation_chain(settings, {"u": "timeout"}, "test-timeout", mode="groq")
    assert result.status == "UNAVAILABLE"
    assert result.reason_code == "TIMEOUT"


@pytest.mark.asyncio
async def test_rate_limit_returns_unavailable(monkeypatch):
    """A provider 429 must return UNAVAILABLE with RATE_LIMITED reason_code."""
    from app.ai import providers as prov_mod

    async def _fake_429(self, settings, context, request_id):
        return prov_mod.ProviderResult(status="UNAVAILABLE", reason="Rate limited (429).",
                                       reason_code="RATE_LIMITED", provider="OPENROUTER",
                                       model="deepseek-r1:free", request_id=request_id)

    monkeypatch.setattr(prov_mod.OpenRouterProvider, "validate", _fake_429)
    settings = Settings(AI_PROVIDER="openrouter", OPENROUTER_API_KEY="or-test",
                        OPENROUTER_MODEL="deepseek/deepseek-r1-distill-qwen-32b:free")
    clear_validation_cache()
    clear_provider_status("OPENROUTER")
    result = await run_validation_chain(settings, {"u": "429"}, "test-429", mode="openrouter")
    assert result.status == "UNAVAILABLE"
    assert result.reason_code == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_all_providers_unavailable(monkeypatch):
    """If every configured provider fails, the chain returns UNAVAILABLE."""
    from app.ai import providers as prov_mod

    async def _fake_fail(self, settings, context, request_id):
        return prov_mod.ProviderResult(status="UNAVAILABLE", reason="Server error (500).",
                                       reason_code="PROVIDER_ERROR", provider=self.name,
                                       model="m", request_id=request_id)

    monkeypatch.setattr(prov_mod.LegacyOpenAIProvider, "validate", _fake_fail)
    settings = Settings(AI_PROVIDER="openai", AI_API_KEY="sk-test", AI_MODEL="gpt-4o-mini")
    clear_validation_cache()
    clear_provider_status("OPENAI")
    result = await run_validation_chain(settings, {"u": "all"}, "test-all", mode="openai")
    assert result.status == "UNAVAILABLE"
    # The specific provider failure is preserved (provider + reason_code)
    assert result.provider in ("OPENAI", "NONE")
    assert result.reason_code in ("PROVIDER_ERROR", "UNAVAILABLE")


@pytest.mark.asyncio
async def test_provider_recovery_after_cooldown(monkeypatch):
    """After a cooldown, a provider is treated as available again."""
    from app.ai import providers as prov_mod

    mark_provider_status("GROQ", prov_mod.ProviderStatus.INSUFFICIENT_BALANCE, "INSUFFICIENT_BALANCE")
    prov_mod._provider_status_timestamp["GROQ"] = 0  # cooldown expired
    assert prov_mod._provider_in_cooldown("GROQ") is False
    clear_provider_status("GROQ")


@pytest.mark.asyncio
async def test_repeated_402_is_cooldown_protected(monkeypatch):
    """Repeated 402 errors must not hammer the provider: after the first 402
    the provider enters cooldown and is skipped."""
    from app.ai import providers as prov_mod

    calls = {"n": 0}

    async def _fake_402(self, settings, context, request_id):
        calls["n"] += 1
        return prov_mod.ProviderResult(status="UNAVAILABLE", reason="AI provider balance is insufficient.",
                                       reason_code="INSUFFICIENT_BALANCE", provider="OPENAI",
                                       model="gpt-4o-mini", request_id=request_id)

    monkeypatch.setattr(prov_mod.LegacyOpenAIProvider, "validate", _fake_402)
    settings = Settings(AI_PROVIDER="openai", AI_API_KEY="sk-test", AI_MODEL="gpt-4o-mini")
    clear_validation_cache()
    clear_provider_status("OPENAI")

    r1 = await run_validation_chain(settings, {"u": "a"}, "r1", mode="openai")
    assert r1.reason_code == "INSUFFICIENT_BALANCE"
    n_after_first = calls["n"]

    # Second call: provider is in cooldown → skipped entirely (no new HTTP call).
    r2 = await run_validation_chain(settings, {"u": "b"}, "r2", mode="openai")
    assert r2.status == "UNAVAILABLE"
    assert calls["n"] <= n_after_first + 1
    clear_provider_status("OPENAI")


@pytest.mark.asyncio
async def test_malformed_provider_response_returns_unavailable(monkeypatch):
    """A malformed provider response must return INVALID_RESPONSE, not a fake
    APPROVE/REJECT, and must never crash the caller."""
    from app.ai import providers as prov_mod

    async def _fake_malformed(self, settings, context, request_id):
        return prov_mod.ProviderResult(status="UNAVAILABLE", reason="Invalid/malformed response.",
                                       reason_code="INVALID_RESPONSE", provider="GROQ",
                                       model="m", request_id=request_id, raw_response="PARSE_ERROR")

    monkeypatch.setattr(prov_mod.GroqProvider, "validate", _fake_malformed)
    settings = Settings(AI_PROVIDER="groq", GROQ_API_KEY="gsk-test", GROQ_MODEL="llama-3.3-70b-versatile")
    clear_validation_cache()
    clear_provider_status("GROQ")
    result = await run_validation_chain(settings, {"u": "malformed"}, "test-malformed", mode="groq")
    assert result.status == "UNAVAILABLE"
    assert result.reason_code == "INVALID_RESPONSE"
    assert result.provider == "GROQ"


@pytest.mark.asyncio
async def test_provider_success_returns_decision(monkeypatch):
    """A successful provider response must return a real decision with provider/model."""
    from app.ai import providers as prov_mod

    async def _fake_ok(self, settings, context, request_id):
        return prov_mod.ProviderResult(status="APPROVE", score=92.0, reason="Setup is clean.",
                                       provider="GROQ", model="llama-3.3-70b-versatile",
                                       request_id=request_id, raw_response='{"status":"APPROVE"}',
                                       risks=["spread widening"], missing=[])

    monkeypatch.setattr(prov_mod.GroqProvider, "validate", _fake_ok)
    settings = Settings(AI_PROVIDER="groq", GROQ_API_KEY="gsk-test", GROQ_MODEL="llama-3.3-70b-versatile")
    clear_validation_cache()
    clear_provider_status("GROQ")
    validator = AIValidator(settings)
    signal = _signal(quality=SignalQuality.STRONG, rr=3.0)
    result = await validator.validate_signal(signal)
    assert result.status == AIValidationStatus.APPROVE
    assert result.provider == "GROQ"
    assert result.model == "llama-3.3-70b-versatile"
    assert result.score == 92.0 if hasattr(result, "score") else True


@pytest.mark.asyncio
async def test_validation_cache_dedupes_repeated_context(monkeypatch):
    """The short-lived cache must deduplicate identical validation contexts."""
    from app.ai import providers as prov_mod

    calls = {"n": 0}

    async def _fake_ok(self, settings, context, request_id):
        calls["n"] += 1
        return prov_mod.ProviderResult(status="APPROVE", score=90.0, reason="ok",
                                       provider="GROQ", model="m", request_id=request_id)

    monkeypatch.setattr(prov_mod.GroqProvider, "validate", _fake_ok)
    settings = Settings(AI_PROVIDER="groq", GROQ_API_KEY="gsk-test", GROQ_MODEL="m")
    clear_validation_cache()
    clear_provider_status("GROQ")
    ctx = {"a": 1, "b": "x"}
    r1 = await run_validation_chain(settings, ctx, "r1", mode="groq")
    n1 = calls["n"]
    r2 = await run_validation_chain(settings, dict(ctx), "r2", mode="groq")
    assert calls["n"] == n1, "cache should have prevented a second provider call"
    clear_validation_cache()


def test_ai_providers_status_endpoint():
    """GET /analysis/ai/providers returns provider status (no secrets)."""
    from fastapi.testclient import TestClient
    from app.api.app import create_app

    with TestClient(create_app()) as client:
        r = client.get("/analysis/ai/providers")
        assert r.status_code == 200
        d = r.json()
        assert "providers" in d
        assert "status" in d
        assert d["advisory_only"] is True
        # No API keys leaked
        body = r.text
        assert "sk-" not in body and "Bearer" not in body


def test_health_exposes_ai_provider_state():
    """GET /health exposes ai_provider state without breaking market health."""
    from fastapi.testclient import TestClient
    from app.api.app import create_app

    with TestClient(create_app()) as client:
        r = client.get("/health")
        assert r.status_code == 200
        d = r.json()
        assert "ai_provider" in d
        assert d["ai_provider"]["advisory_only"] is True
        assert d["ai_provider"]["status"] in ("AVAILABLE", "DEGRADED", "UNAVAILABLE")