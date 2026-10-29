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

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.data.live.registry import FeedRegistry
from app.data.live.service import get_live_service
from app.data.models import Tick
from app.core.logging import logger

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
    if buffer is None and not alert.direction:
        raise HTTPException(
            status_code=404,
            detail=f"No live feed registered for symbol '{alert.symbol}'. "
                   f"Register a feed first via FeedRegistry.",
        )
    if buffer is None:
        buffer = await service.registry.register_buffer(alert.symbol)

    tick = Tick(
        symbol=alert.symbol,
        timestamp=alert.timestamp or datetime.now(timezone.utc),
        bid=alert.bid,
        ask=alert.ask,
        last=alert.price,
    )
    await service.on_tick(tick)

    # If the alert includes trade direction (BUY/SELL), trigger MT5 execution and Telegram alert
    order_info = None
    if alert.direction and alert.direction.upper() in ("BUY", "SELL", "LONG", "SHORT"):
        try:
            from app.services.mt5_bridge_manager import get_mt5_bridge_manager
            from app.config.execution_settings import get_execution_settings

            exec_cfg = get_execution_settings()
            dir_norm = "BUY" if alert.direction.upper() in ("BUY", "LONG") else "SELL"
            cur_px = alert.price or alert.ask or alert.bid or 0.0
            sl_px = float(alert.payload.get("sl") or alert.payload.get("stop_loss") or (cur_px - 5.0 if dir_norm == "BUY" else cur_px + 5.0))
            tp_px = float(alert.payload.get("tp") or alert.payload.get("take_profit") or (cur_px + 10.0 if dir_norm == "BUY" else cur_px - 10.0))
            strat_name = alert.strategy or "Breakout Strategy"
            target_symbol = alert.symbol or exec_cfg.mt5_symbol or "XAUUSD-VIP"

            order_info = get_mt5_bridge_manager().enqueue_order({
                "strategy": strat_name,
                "direction": dir_norm,
                "symbol": target_symbol,
                "entry_price": cur_px,
                "stop_loss": sl_px,
                "take_profit_1": tp_px,
                "lot_size": float(alert.payload.get("lots") or getattr(exec_cfg, "base_lot_size", 0.01) or 0.01),
                "comment": f"TV_{strat_name[:8]}_{dir_norm}"[:31],
            })
            logger.info("[WEBHOOK] Enqueued TradingView order to MT5: %s", order_info)

            # Dispatch instant Telegram Alert
            from app.notifications.telegram_service import TelegramService
            tg = TelegramService()
            tg_text = (
                f"🚀 *TRADINGVIEW SIGNAL EXECUTED*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 *Strategy:* {strat_name}\n"
                f"🪙 *Symbol:* {target_symbol}\n"
                f"📈 *Direction:* {dir_norm}\n"
                f"💵 *Price:* ${cur_px:,.2f}\n"
                f"🛑 *Stop Loss:* ${sl_px:,.2f}\n"
                f"🎯 *Take Profit:* ${tp_px:,.2f}\n"
                f"🎫 *MT5 Status:* {order_info.get('status', 'QUEUED')}\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            )
            asyncio.create_task(tg.send_raw_alert(tg_text))
        except Exception as exc:
            logger.error("[WEBHOOK] Error enqueuing TradingView order to MT5: %s", exc)

    return {
        "status": "received",
        "symbol": alert.symbol,
        "buffer_size": await buffer.size(),
        "order": order_info,
    }


@router.get("/health", summary="List all registered feed buffers")
async def webhook_health(request: Request) -> dict[str, Any]:
    registry = FeedRegistry.get_instance()
    health = await registry.health()
    return {"feeds": [h.model_dump(mode="json") for h in health]}