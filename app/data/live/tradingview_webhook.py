"""
TradingView Webhook receiver for real-time alert-based tick ingestion.

TradingView alerts are configured to POST a JSON payload to
``POST /webhook/tradingview``.  The payload is parsed into a :class:`Tick`
and pushed into the shared :class:`FeedRegistry`.

Expected payload fields (user-defined in TradingView alert message):
  - ``symbol`` (str) — required
  - ``bid`` / ``ask`` / ``price`` (float) — at least one required
  - ``timeframe``, ``strategy``, ``direction`` (str) — optional metadata
  - Any additional fields are available in ``payload``.
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.data.live.registry import FeedRegistry
from app.data.live.service import get_live_service
from app.data.models import Tick

router = APIRouter(prefix="/webhook", tags=["Live Webhooks"])


class TradingViewAlert(BaseModel):
    symbol: str = Field(min_length=1)
    bid: float | None = Field(default=None, gt=0)
    ask: float | None = Field(default=None, gt=0)
    price: float | None = Field(default=None, gt=0)
    timestamp: datetime | None = None
    timeframe: str | None = None
    strategy: str | None = None
    direction: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/tradingview", summary="Receive TradingView alert")
async def tradingview_alert_handler(alert: TradingViewAlert, request: Request) -> dict[str, Any]:
    service = get_live_service()
    buffer = await service.registry.get_buffer(alert.symbol)
    if buffer is None:
        raise HTTPException(
            status_code=404,
            detail=f"No live feed registered for symbol '{alert.symbol}'. "
                   f"Register a feed first via FeedRegistry.",
        )

    tick = Tick(
        symbol=alert.symbol,
        timestamp=alert.timestamp or datetime.now(timezone.utc),
        bid=alert.bid,
        ask=alert.ask,
        last=alert.price,
    )
    await service.on_tick(tick)
    return {
        "status": "received",
        "symbol": alert.symbol,
        "buffer_size": await buffer.size(),
    }


@router.get("/health", summary="List all registered feed buffers")
async def webhook_health(request: Request) -> dict[str, Any]:
    registry = FeedRegistry.get_instance()
    health = await registry.health()
    return {"feeds": [h.model_dump(mode="json") for h in health]}