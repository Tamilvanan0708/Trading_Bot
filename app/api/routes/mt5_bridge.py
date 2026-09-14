"""
MT5 Bridge API Routes.
Exposes endpoints for the MQL5 EA (Expert Advisor) to receive trade signals,
report live execution tickets, and maintain heartbeat connectivity with VT Markets.
"""

from __future__ import annotations

import os
from typing import Any
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.config.execution_settings import get_execution_settings, save_execution_settings
from app.services.mt5_bridge_manager import get_mt5_bridge_manager

router = APIRouter(prefix="/api/mt5", tags=["MT5 Bridge"])


class HeartbeatPayload(BaseModel):
    account_login: int | None = None
    server: str | None = None
    balance: float | None = None
    equity: float | None = None
    leverage: int | None = 500
    symbol: str | None = "XAUUSD"


class ExecutionReportPayload(BaseModel):
    order_id: str
    ticket: int
    status: str = "FILLED"
    fill_price: float = 0.0
    retcode: int | None = 10009
    error: str | None = None


class TestTradePayload(BaseModel):
    action: str = Field(default="BUY", description="BUY or SELL")
    lots: float = Field(default=0.01, description="Lot size for test order")
    sl_points: float = Field(default=3.0, description="Stop Loss distance in points")
    tp_points: float = Field(default=5.0, description="Take Profit distance in points")


@router.get("/status")
async def get_bridge_status():
    """Returns live connection status of the MT5 EA Bridge and broker account info."""
    manager = get_mt5_bridge_manager()
    return manager.get_status()


@router.get("/pending-orders")
async def get_pending_orders(symbol: str = "XAUUSD"):
    """
    Called by the MQL5 EA on timer/tick to retrieve and consume pending orders.
    STRICT POLICY: Orders returned here are EXCLUSIVELY from Fib Retracement.
    """
    manager = get_mt5_bridge_manager()
    orders = manager.pop_pending_orders(symbol=symbol)
    return {
        "status": "success",
        "orders_count": len(orders),
        "orders": orders,
    }


@router.post("/heartbeat")
async def receive_heartbeat(payload: HeartbeatPayload, request: Request):
    """Called by the MQL5 EA every 1-3 seconds to verify connection and sync balance."""
    manager = get_mt5_bridge_manager()
    client_ip = request.client.host if request.client else None
    res = manager.record_heartbeat(payload.model_dump(), client_ip=client_ip)
    return res


@router.post("/execution-report")
async def receive_execution_report(payload: ExecutionReportPayload):
    """Called by the MQL5 EA immediately after OrderSend() executes on VT Markets."""
    manager = get_mt5_bridge_manager()
    res = manager.record_execution_report(payload.model_dump())
    return res


@router.post("/test-trade")
async def trigger_test_order(payload: TestTradePayload):
    """
    Triggers a 0.01 lot test order for Fib Retracement to verify MT5 EA connectivity.
    Stops are set to 0.0 to prevent [Invalid stops] rejections on live brokers.
    """
    action = payload.action.upper()
    order_data = {
        "strategy": "Fib Retracement",
        "layer": "TEST",
        "direction": action,
        "entry_price": 0.0,
        "stop_loss": 0.0,
        "take_profit_1": 0.0,
        "sl_pts": 3.0,
        "tp_pts": 5.0,
        "lot_size": payload.lots,
        "is_test": True,
    }

    manager = get_mt5_bridge_manager()
    res = manager.enqueue_order(order_data)
    return res


@router.get("/download-ea")
async def download_mql5_ea():
    """Directly download the XAU_AI_Bridge.mq5 file for dropping into MT5 terminal."""
    ea_path = os.path.abspath("app/static/mql5/XAU_AI_Bridge.mq5")
    if not os.path.exists(ea_path):
        return JSONResponse(status_code=404, content={"error": "EA file not generated yet"})
    return FileResponse(
        path=ea_path,
        filename="XAU_AI_Bridge.mq5",
        media_type="text/plain",
    )
