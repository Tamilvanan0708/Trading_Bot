"""
Unit tests for Vision AI Chart Verification & Real-time Live State Sync.
"""

import asyncio
from datetime import datetime, timezone, timedelta
import pytest
from unittest.mock import AsyncMock, patch

from app.data.models import Candle
from app.retracement.models import RetracementSetup, RetracementState
from app.ai.vision_validator import (
    render_chart_image,
    verify_chart_with_vision,
    _heuristic_chart_check,
    _parse_vision_response,
    VisionVerificationResult,
    VISION_SYSTEM_PROMPT,
)


def _sample_series(n: int = 40) -> list[Candle]:
    t0 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    candles = []
    price = 2600.0
    for i in range(n):
        candles.append(Candle(
            timestamp=t0 + timedelta(minutes=5 * i),
            open=price,
            high=price + 2.0,
            low=price - 1.5,
            close=price + 0.5,
            volume=150.0,
        ))
        price += 0.5
    return candles


def test_render_chart_image_produces_png_bytes():
    """Verify that render_chart_image returns non-empty PNG bytes with all marked levels."""
    candles = _sample_series(40)
    setup = RetracementSetup(
        symbol="XAUUSD",
        timeframe="5m",
        direction="LONG",
        state=RetracementState.TP_DYNAMIC,
        point_1_price=2610.0,
        point_2_price=2600.0,
        bos_price=2610.0,
        current_high_price=2625.0,
        entry_price=2615.45,
        sl_price=2605.90,
        dynamic_tp=2625.0,
    )
    img_bytes = render_chart_image(candles, setup, num_candles=35)
    assert len(img_bytes) > 5000
    # PNG signature check (\x89PNG\r\n\x1a\n)
    assert img_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_heuristic_rejects_compressed_chop():
    """Verify that compressed impulse legs (< 4.5 pts on Gold) are rejected as chop."""
    candles = _sample_series(30)
    setup = RetracementSetup(
        symbol="XAUUSD",
        timeframe="5m",
        direction="LONG",
        state=RetracementState.TP_DYNAMIC,
        point_2_price=2600.0,
        current_high_price=2602.0,  # span is only 2.0 pts
        dynamic_tp=2602.0,
    )
    res = _heuristic_chart_check(candles, setup, "dummy_b64")
    assert res.status == "REJECT"
    assert setup.state == RetracementState.INVALIDATED
    assert "Chop/No BOS" in setup.invalidation_reason
    assert setup.ai_status == "REJECTED"


def test_heuristic_approves_clean_impulse():
    """Verify clean impulse moves are approved with high confidence."""
    candles = _sample_series(40)
    setup = RetracementSetup(
        symbol="XAUUSD",
        timeframe="5m",
        direction="LONG",
        state=RetracementState.TP_DYNAMIC,
        point_2_price=2600.0,
        current_high_price=2625.0,  # 25 pts span
        dynamic_tp=2625.0,
    )
    res = _heuristic_chart_check(candles, setup, "dummy_b64")
    assert res.status == "APPROVED"
    assert res.confidence >= 90.0
    assert setup.state == RetracementState.TP_DYNAMIC
    assert setup.ai_status == "APPROVED"


def test_parse_vision_response():
    """Verify JSON parsing and fallback regex from Gemini Vision."""
    raw_json = '{"status": "APPROVED", "confidence": 95.0, "reason": "Institutional BOS confirmed"}'
    res = _parse_vision_response(raw_json)
    assert res.status == "APPROVED"
    assert res.confidence == 95.0
    assert "Institutional BOS" in res.reason

    raw_fence = '```json\n{"status": "REJECT", "confidence": 85.0, "reason": "Sideways chop detected"}\n```'
    res_fence = _parse_vision_response(raw_fence)
    assert res_fence.status == "REJECT"
    assert "chop" in res_fence.reason.lower()


@pytest.mark.asyncio
async def test_verify_chart_with_vision_mock_fallback():
    """Verify async non-blocking execution with graceful fallback when no API key is set."""
    candles = _sample_series(40)
    setup = RetracementSetup(
        symbol="XAUUSD",
        timeframe="5m",
        direction="LONG",
        state=RetracementState.TP_DYNAMIC,
        point_2_price=2600.0,
        current_high_price=2620.0,
        dynamic_tp=2620.0,
    )
    # With no API key configured, it falls back gracefully to heuristic verification
    result = await verify_chart_with_vision(candles, setup, settings=None)
    assert result.status in ("APPROVED", "REJECT")
    assert len(result.chart_png_base64) > 100
    assert setup.ai_status in ("APPROVED", "REJECTED")


@pytest.mark.asyncio
async def test_verify_chart_with_vision_rejection_invalidates_setup():
    """When Vision AI rejects, setup is marked INVALIDATED with 'Vision AI: Chop/No BOS'."""
    candles = _sample_series(40)
    setup = RetracementSetup(
        symbol="XAUUSD",
        timeframe="5m",
        direction="LONG",
        state=RetracementState.TP_DYNAMIC,
        point_2_price=2600.0,
        current_high_price=2620.0,
        dynamic_tp=2620.0,
    )

    class FakeSettings:
        GEMINI_API_KEY = "test_key_123"
        GEMINI_MODEL = "gemini-2.5-flash"
        AI_PROVIDER = "gemini"

    class FakeResponse:
        status_code = 200
        def json(self):
            return {
                "candidates": [{
                    "content": {
                        "parts": [{
                            "text": '{"status": "REJECT", "confidence": 91.0, "reason": "Chop/No BOS"}'
                        }]
                    }
                }]
            }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = FakeResponse()
        result = await verify_chart_with_vision(candles, setup, settings=FakeSettings())

        assert result.status == "REJECT"
        assert setup.state == RetracementState.INVALIDATED
        assert "Vision AI: Chop/No BOS" in setup.invalidation_reason
        assert setup.ai_status == "REJECT"
