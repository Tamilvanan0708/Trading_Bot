"""
Unit tests for the Telegram notification service.
"""

import pytest

from app.ai.models import AIValidationResult
from app.config.settings import Settings
from app.core.constants import (
    AIValidationStatus,
    MarketBias,
    SignalDirection,
    SignalQuality,
    StrategyType,
)
from app.notifications.formatter import format_telegram_signal
from app.notifications.telegram_service import TelegramService
from app.signals.models import SignalPayload


def _signal() -> SignalPayload:
    return SignalPayload(
        instrument="XAUUSD",
        direction=SignalDirection.LONG,
        strategy=StrategyType.CONFLUENCE,
        entry=2650.0,
        stop_loss=2640.0,
        take_profit_1=2665.0,
        take_profit_2=2680.0,
        take_profit_3=2700.0,
        risk_reward=3.0,
        confidence_score=85.0,
        signal_quality=SignalQuality.STRONG,
        market_bias=MarketBias.BULLISH,
        reasons=["Strong 4H bias", "FVG confirmed"],
    )


def test_format_telegram_message_structured():
    signal = _signal()
    ai_val = AIValidationResult(
        status=AIValidationStatus.APPROVE,
        confidence=90.0,
        explanation="Good setup",
        identified_risks=[],
        missing_confirmations=[],
    )
    metadata = {
        "htf_bias_4h": "BULLISH",
        "structure_1h": "BULLISH",
        "setup_tf": "15M",
        "setup_state": "CONFIRMED",
        "trigger_tf": "15M",
        "trigger_state": "CONFIRMED",
        "market_regime": "RANGING",
        "session": "LONDON",
    }
    msg = format_telegram_signal(signal, ai_val, metadata=metadata)
    assert "XAU/USD SIGNAL" in msg
    assert "Direction*: LONG" in msg
    assert "2650.00" in msg
    assert "AI Validation*: APPROVE" in msg
    assert "4H Bias*: BULLISH" in msg
    assert "Regime*: RANGING" in msg
    assert "Session*: LONDON" in msg
    assert "RESEARCH / MANUAL EXECUTION ONLY" in msg
    assert "classified FAILED" in msg  # honest risk warning


@pytest.mark.asyncio
async def test_telegram_skipped_when_disabled():
    svc = TelegramService(Settings(TELEGRAM_ENABLED=False))
    assert await svc.send_signal_alert(_signal()) is False


@pytest.mark.asyncio
async def test_telegram_duplicate_prevention(monkeypatch):
    svc = TelegramService(
        Settings(TELEGRAM_ENABLED=True, TELEGRAM_BOT_TOKEN="tok", TELEGRAM_CHAT_ID="123")
    )
    calls = {"count": 0}

    async def fake_post(self, url, json):
        calls["count"] += 1
        return type("Res", (), {"status_code": 200, "text": "ok"})()

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)
    signal = _signal()

    await svc.send_signal_alert(signal)
    await svc.send_signal_alert(signal)  # duplicate signal_id

    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_telegram_failure_returns_false(monkeypatch):
    svc = TelegramService(
        Settings(TELEGRAM_ENABLED=True, TELEGRAM_BOT_TOKEN="tok", TELEGRAM_CHAT_ID="123")
    )

    async def fake_post(self, url, json):
        raise RuntimeError("network down")

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)
    assert await svc.send_signal_alert(_signal()) is False