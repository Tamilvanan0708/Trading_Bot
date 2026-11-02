import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from app.paper_trading.sync import _build_hybrid_close_msg


def test_build_hybrid_close_sl_msg():
    """Verify that _build_hybrid_close_msg builds the exact Stop Loss message."""
    msg = _build_hybrid_close_msg(
        strategy_name="Fib Retracement (L1)",
        symbol_tf="XAU/USD (15M)",
        direction="SHORT",
        entry_px=3890.61,
        exit_px=3911.47,
        pts=-20.86,
        realized_pnl=-688.38,
        exit_reason="SL_HIT",
        lot_size=0.33,
        opened_at=datetime(2026, 11, 1, 23, 3, 47, tzinfo=timezone.utc),
        closed_at=datetime(2026, 11, 2, 1, 0, 0, tzinfo=timezone.utc),
        paper_trade_id="004c9c25-d031-4deb-89aa-9c599e359019",
        ticket_override=885527208,
    )
    assert "STOP LOSS HIT (-20.86 PTS)" in msg
    assert "Fib Retracement (L1)" in msg
    assert "#885527208" in msg
    assert "SELL / SHORT ▼" in msg
    assert "-$688.38 USD" in msg or "688.38" in msg


def test_build_hybrid_close_sl_short_trade_ticket_886825710():
    """Verify ticket 886825710 (SHORT from 3900.80 to 3908.81) generates a STOP LOSS HIT message even when pts is passed as positive."""
    msg = _build_hybrid_close_msg(
        strategy_name="Fib Retracement (L1)",
        symbol_tf="XAU/USD (5M)",
        direction="SHORT",
        entry_px=3900.80,
        exit_px=3908.81,
        pts=8.01,
        realized_pnl=-400.50,
        exit_reason="SL_HIT",
        lot_size=0.50,
        opened_at=datetime(2026, 11, 2, 3, 3, 0, tzinfo=timezone.utc),
        closed_at=datetime(2026, 11, 2, 4, 10, 0, tzinfo=timezone.utc),
        paper_trade_id="test-trade-886825710",
        ticket_override=886825710,
    )
    assert "STOP LOSS HIT (-8.01 PTS)" in msg
    assert "TARGET HIT" not in msg
    assert "TAKE PROFIT" not in msg
    assert "SELL / SHORT ▼" in msg
    assert "#886825710" in msg
    assert "-$400.50 USD" in msg
    assert "Drawdown" in msg
