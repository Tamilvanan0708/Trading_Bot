"""
Automatic Synchronization of Strategy Setups to Paper Trades.

Whenever an entry is touched in Fib With Retracement or SMC With Fib,
this service guarantees that a 0.01 lot paper trade is opened and managed
with live running PnL and point tracking.
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.data.live.service import get_live_service
from app.database.models import PaperTradeModel
from app.retracement.multi_tf import get_retracement_multi_tf_service
from app.retracement.smc_fib_multi_tf import get_smc_fib_multi_tf_service


async def sync_strategy_paper_trades(db: AsyncSession) -> None:
    """Scan in-memory 5M strategy engine states and automatically open/update paper trades."""
    ls = get_live_service()
    try:
        live_price = await ls.get_latest_price("XAUUSD")
    except Exception:  # noqa: BLE001
        live_price = None

    # 1. Fib With Retracement 5M: Check each tranche layer
    try:
        fib_svc = get_retracement_multi_tf_service("XAUUSD")
        fib_states = await fib_svc.advance(db)
        f5 = fib_states.get("5m")
        if f5 and getattr(f5, "layers", None):
            for l_key, layer in f5.layers.items():
                if layer.get("state") in ("FILLED", "TP_HIT", "ESCAPE_CLOSED"):
                    sig_id = f"FIB_RETR_5M_{l_key}_{int(f5.point_2_price or 0)}"
                    existing = (await db.execute(
                        select(PaperTradeModel).where(PaperTradeModel.signal_id == sig_id)
                    )).scalars().first()

                    entry_px = float(layer.get("entry_price") or 0.0)
                    sl_px = float(layer.get("sl") or f5.sl_price or 0.0)
                    tp_px = float(layer.get("tp") or f5.fib_1_000 or 0.0)

                    if not existing and entry_px > 0:
                        new_trade = PaperTradeModel(
                            id=str(uuid.uuid4()),
                            signal_id=sig_id,
                            symbol="XAUUSD",
                            direction=f5.direction,
                            state="OPEN",
                            lot_size=0.01,
                            risk_amount=round(0.01 * abs(entry_px - sl_px) * 100.0, 2),
                            target_entry=entry_px,
                            actual_entry=entry_px,
                            stop_loss=sl_px,
                            take_profit_1=tp_px,
                            take_profit_2=tp_px,
                            take_profit_3=tp_px,
                            opened_at=datetime.now(timezone.utc),
                            realized_pnl=0.0,
                            realized_r=0.0,
                            state_logs=[{"event": "ENTRY_TOUCHED", "price": entry_px, "layer": l_key}],
                        )
                        db.add(new_trade)
                        await db.commit()
                        logger.info("[PAPER-AUTO] Opened trade %s (%s %s) @ %.2f", sig_id, f5.direction, l_key, entry_px)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PAPER-SYNC] Fib sync error: %s", exc)

    # 2. SMC With Fib 5M: Check single institutional entry
    try:
        smc_svc = get_smc_fib_multi_tf_service("XAUUSD")
        smc_states = await smc_svc.advance(db)
        s5 = smc_states.get("5m") or {}
        if s5.get("is_entry_touched") and s5.get("entry", {}).get("price"):
            p2 = s5.get("point_2", {}).get("price") or 0.0
            sig_id = f"SMC_FIB_5M_{int(p2)}"
            existing = (await db.execute(
                select(PaperTradeModel).where(PaperTradeModel.signal_id == sig_id)
            )).scalars().first()

            entry_px = float(s5.get("entry", {}).get("price") or 0.0)
            sl_px = float(s5.get("sl", {}).get("price") or 0.0)
            tp_px = float(s5.get("tp", {}).get("locked") or s5.get("tp", {}).get("dynamic") or 0.0)
            dir_str = str(s5.get("direction", "LONG")).upper()

            if not existing and entry_px > 0:
                new_trade = PaperTradeModel(
                    id=str(uuid.uuid4()),
                    signal_id=sig_id,
                    symbol="XAUUSD",
                    direction=dir_str,
                    state="OPEN",
                    lot_size=0.01,
                    risk_amount=round(0.01 * abs(entry_px - sl_px) * 100.0, 2),
                    target_entry=entry_px,
                    actual_entry=entry_px,
                    stop_loss=sl_px,
                    take_profit_1=tp_px,
                    take_profit_2=tp_px,
                    take_profit_3=tp_px,
                    opened_at=datetime.now(timezone.utc),
                    realized_pnl=0.0,
                    realized_r=0.0,
                    state_logs=[{"event": "ENTRY_TOUCHED", "price": entry_px, "strategy": "SMC_WITH_FIB"}],
                )
                db.add(new_trade)
                await db.commit()
                logger.info("[PAPER-AUTO] Opened trade %s (SMC %s) @ %.2f", sig_id, dir_str, entry_px)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PAPER-SYNC] SMC sync error: %s", exc)

    # 3. Monitor OPEN trades against live price and resolve TP / SL
    if live_price is not None:
        open_trades = (await db.execute(
            select(PaperTradeModel).where(PaperTradeModel.state == "OPEN")
        )).scalars().all()

        for t in open_trades:
            entry = t.actual_entry or t.target_entry or 0.0
            if entry <= 0:
                continue

            if t.direction == "LONG":
                if t.take_profit_1 and live_price >= t.take_profit_1:
                    t.state = "CLOSED"
                    t.exit_price = t.take_profit_1
                    t.exit_reason = "TP_HIT"
                    t.closed_at = datetime.now(timezone.utc)
                    pts = round(t.take_profit_1 - entry, 2)
                    t.realized_pnl = round(pts * (t.lot_size or 0.01) * 100.0, 2)
                    t.realized_r = round(pts / max(0.1, abs(entry - (t.stop_loss or 0.0))), 2)
                    logger.info("[PAPER-AUTO] Closed LONG trade %s at TP: %.2f (+$%.2f)", t.id, t.exit_price, t.realized_pnl)
                elif t.stop_loss and live_price <= t.stop_loss:
                    t.state = "CLOSED"
                    t.exit_price = t.stop_loss
                    t.exit_reason = "SL_HIT"
                    t.closed_at = datetime.now(timezone.utc)
                    pts = round(t.stop_loss - entry, 2)
                    t.realized_pnl = round(pts * (t.lot_size or 0.01) * 100.0, 2)
                    t.realized_r = round(pts / max(0.1, abs(entry - (t.stop_loss or 0.0))), 2)
                    logger.info("[PAPER-AUTO] Closed LONG trade %s at SL: %.2f ($%.2f)", t.id, t.exit_price, t.realized_pnl)
            elif t.direction == "SHORT":
                if t.take_profit_1 and live_price <= t.take_profit_1:
                    t.state = "CLOSED"
                    t.exit_price = t.take_profit_1
                    t.exit_reason = "TP_HIT"
                    t.closed_at = datetime.now(timezone.utc)
                    pts = round(entry - t.take_profit_1, 2)
                    t.realized_pnl = round(pts * (t.lot_size or 0.01) * 100.0, 2)
                    t.realized_r = round(pts / max(0.1, abs(entry - (t.stop_loss or 0.0))), 2)
                    logger.info("[PAPER-AUTO] Closed SHORT trade %s at TP: %.2f (+$%.2f)", t.id, t.exit_price, t.realized_pnl)
                elif t.stop_loss and live_price >= t.stop_loss:
                    t.state = "CLOSED"
                    t.exit_price = t.stop_loss
                    t.exit_reason = "SL_HIT"
                    t.closed_at = datetime.now(timezone.utc)
                    pts = round(entry - t.stop_loss, 2)
                    t.realized_pnl = round(pts * (t.lot_size or 0.01) * 100.0, 2)
                    t.realized_r = round(pts / max(0.1, abs(entry - (t.stop_loss or 0.0))), 2)
                    logger.info("[PAPER-AUTO] Closed SHORT trade %s at SL: %.2f ($%.2f)", t.id, t.exit_price, t.realized_pnl)

        await db.commit()
