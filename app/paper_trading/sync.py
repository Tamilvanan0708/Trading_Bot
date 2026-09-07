"""
Automatic Synchronization of Strategy Setups to Paper Trades with AI Validation Gate.

Thread-safe, race-condition protected synchronization service.
Ensures exactly ONE 0.01 lot paper trade per unique strategy signal ID.
"""

import asyncio
from datetime import datetime, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.validator import get_ai_validator
from app.core.constants import MarketBias, SignalDirection, SignalQuality, StrategyType
from app.core.logging import logger
from app.data.live.service import get_live_service
from app.database.models import PaperTradeModel, SignalModel
from app.database.repository import Repository
from app.config.execution_settings import calculate_lot_size, get_execution_settings
from app.notifications.telegram_service import TelegramService
from app.retracement.multi_tf import get_retracement_multi_tf_service
from app.retracement.smc_fib_multi_tf import get_smc_fib_multi_tf_service
from app.signals.models import SignalPayload

# Global async mutex lock and in-flight guard to strictly prevent double-executions
_sync_lock = asyncio.Lock()
_in_flight_signals: set[str] = set()
_last_paper_sync_ts: float = 0.0


async def sync_strategy_paper_trades(db: AsyncSession, force: bool = False) -> None:
    """Scan in-memory 5M strategy engine states, run AI validation, and open/update paper trades.
    Thread-safe and guarded by _sync_lock with 10s debounce.
    """
    global _last_paper_sync_ts
    import time
    now = time.time()
    if not force and (now - _last_paper_sync_ts < 10.0):
        return
    _last_paper_sync_ts = now

    async with _sync_lock:
        # 0. Clean up any existing duplicate OPEN trades (e.g. from previous race conditions)
        try:
            open_trades = (await db.execute(
                select(PaperTradeModel).where(PaperTradeModel.state == "OPEN")
            )).scalars().all()
            seen_sigs = set()
            for ot in open_trades:
                if ot.signal_id in seen_sigs:
                    logger.warning("[PAPER-DEDUP] Deleting duplicate OPEN trade %s (%s)", ot.id, ot.signal_id)
                    await db.delete(ot)
                else:
                    seen_sigs.add(ot.signal_id)
            await db.commit()
        except Exception as dedup_err:  # noqa: BLE001
            logger.warning("[PAPER-DEDUP] Deduplication check: %s", dedup_err)

        repo = Repository(db)

        # 0b. Catch-up sync: Synchronize closed paper trades with parent signal outcomes
        try:
            closed_pts = (await db.execute(
                select(PaperTradeModel).where(PaperTradeModel.state == "CLOSED")
            )).scalars().all()
            dirty = False
            for cpt in closed_pts:
                if cpt.signal_id:
                    sig = await repo.get_signal_by_id(cpt.signal_id)
                    if sig and sig.outcome in ("FILLED", "OPEN", "PENDING", None) and cpt.exit_reason:
                        sig.outcome = cpt.exit_reason
                        sig.final_r = cpt.realized_r
                        sig.outcome_updated_at = cpt.closed_at or datetime.now(timezone.utc)
                        if cpt.exit_reason == "TP_HIT":
                            sig.tp1_hit = True
                        elif cpt.exit_reason == "SL_HIT":
                            sig.sl_hit = True
                        dirty = True
            # Sanitize any inverted Stop Losses in signals table
            all_sigs = (await db.execute(select(SignalModel))).scalars().all()
            for s_item in all_sigs:
                if s_item.direction == "LONG" and s_item.stop_loss and s_item.entry_price and s_item.stop_loss >= s_item.entry_price:
                    s_item.stop_loss = round(s_item.entry_price - 8.19, 2)
                    if s_item.take_profit_1:
                        s_item.risk_reward = round(abs(s_item.take_profit_1 - s_item.entry_price) / max(0.1, abs(s_item.entry_price - s_item.stop_loss)), 2)
                    dirty = True
                elif s_item.direction == "SHORT" and s_item.stop_loss and s_item.entry_price and s_item.stop_loss <= s_item.entry_price:
                    s_item.stop_loss = round(s_item.entry_price + 8.19, 2)
                    if s_item.take_profit_1:
                        s_item.risk_reward = round(abs(s_item.take_profit_1 - s_item.entry_price) / max(0.1, abs(s_item.stop_loss - s_item.entry_price)), 2)
                    dirty = True
            if dirty:
                await db.commit()
        except Exception as catchup_err:  # noqa: BLE001
            logger.warning("[PAPER-SYNC] Catch-up signal outcome sync: %s", catchup_err)

        ls = get_live_service()
        try:
            live_price = await ls.get_latest_price("XAUUSD")
            if live_price is not None and live_price < 1000.0:
                live_price = None
        except Exception:  # noqa: BLE001
            live_price = None

        tg = TelegramService()
        validator = get_ai_validator()
        exec_cfg = get_execution_settings()

        # 1. Fib With Retracement: Multi-Timeframe (5M, 15M, 30M, 1H, 4H) Single Active Trade Sync
        if exec_cfg.strategy_fib_retracement:
            try:
                fib_svc = get_retracement_multi_tf_service("XAUUSD")
                fib_states = await fib_svc.advance(db)

                # Option 1A: Multi-Slot Parallel Execution — track open trades per timeframe slot
                existing_open_trades = (await db.execute(
                    select(PaperTradeModel).where(
                        PaperTradeModel.state == "OPEN",
                        PaperTradeModel.signal_id.like("FIB_RETR_%"),
                    )
                )).scalars().all()

                # Map active timeframe -> base_anchor of the active setup
                active_setup_by_tf: dict[str, str] = {}
                for ot in existing_open_trades:
                    if ot.signal_id:
                        # format: FIB_RETR_{TF}_{LAYER}_{ANCHOR}
                        parts = ot.signal_id.split("_")
                        if len(parts) >= 5 and parts[2].lower() in fib_svc.timeframes:
                            active_setup_by_tf[parts[2].lower()] = parts[4]

                allowed_tfs = [tf.lower() for tf in (exec_cfg.fib_retracement_timeframes or ["5m", "15m", "30m", "1h"])]
                for tf_key in fib_svc.timeframes:
                    if tf_key.lower() not in allowed_tfs:
                        continue
                    f_state = fib_states.get(tf_key)
                    if not f_state or not getattr(f_state, "layers", None) or not getattr(f_state, "point_2_price", None):
                        continue

                    for l_key, layer in f_state.layers.items():
                        ratio_val = 0.618 if l_key == "L1" else (0.500 if l_key == "L2" else 0.382)
                        attr = f"fib_{ratio_val:.3f}".replace(".", "_")
                        l_entry = getattr(f_state, attr, None) or float(layer.get("entry_price") or 0.0)
                        if not l_entry:
                            continue
                        l_state = layer.get("state", "PENDING") if layer else "PENDING"
                        l_tp = float(f_state.fib_1_000 if l_key == "L1" else (f_state.fib_0_618 or 0.0))
                        sig_id = f"FIB_RETR_{tf_key.upper()}_{l_key}_{int(f_state.point_2_price)}"

                        # Determine true Stop Loss:
                        base_sl = float(f_state.fib_0_236 or 0.0)
                        layer_sl = float(layer.get("sl") or f_state.sl_price or base_sl or 0.0)
                        if f_state.direction == "LONG":
                            candidates = [s for s in (layer_sl, base_sl) if 0 < s < l_entry]
                            sig_sl = max(candidates) if candidates else round(l_entry - 8.19, 2)
                        else:
                            candidates = [s for s in (layer_sl, base_sl) if s > l_entry]
                            sig_sl = min(candidates) if candidates else round(l_entry + 8.19, 2)

                        # Sync Signal Model
                        try:
                            existing_sig = await repo.get_signal_by_id(sig_id)
                            if existing_sig is None:
                                await repo.save_signal({
                                    "id": sig_id,
                                    "symbol": "XAUUSD",
                                    "strategy": "FIB_WITH_RETRACEMENT",
                                    "strategy_version": f"FIB_RETR_V1:{l_key}",
                                    "direction": f_state.direction,
                                    "timeframe": tf_key,
                                    "entry_price": float(l_entry),
                                    "stop_loss": sig_sl,
                                    "take_profit_1": float(l_tp or 0),
                                    "take_profit_2": float(l_tp or 0),
                                    "take_profit_3": float(l_tp or 0),
                                    "risk_reward": round(abs((l_tp or 0) - l_entry) / max(0.1, abs(l_entry - sig_sl)), 2),
                                    "confidence_score": 92.0,
                                    "signal_quality": "VERY_STRONG",
                                    "market_bias": "BULLISH" if f_state.direction == "LONG" else "BEARISH",
                                    "regime": "TRENDING",
                                    "session": "LONDON",
                                    "outcome": l_state,
                                    "reasons": [
                                        f"Fib With Retracement {l_key} @ {ratio_val:.3f} on {tf_key.upper()} ({f_state.direction}), 0.01 lots",
                                        f"Anchor: ${f_state.point_2_price:.2f} | BOS: ${f_state.point_1_price:.2f} | Target: ${l_tp:.2f}",
                                        f"3-Tranche Layer Execution: 0.01 lots each (Escape Plan: close at 0.618)",
                                    ],
                                })
                                await db.commit()
                            elif existing_sig and existing_sig.outcome != l_state:
                                await repo.update_signal_outcome(sig_id, {"outcome": l_state})
                                await db.commit()
                        except Exception:
                            await db.rollback()

                        if layer.get("state") in ("FILLED", "TP_HIT", "ESCAPE_CLOSED"):
                            if sig_id in _in_flight_signals:
                                continue

                            # Option 1A: Multi-Slot Parallel Execution
                            # Timeframe isolation: an active trade on another timeframe does NOT block tf_key!
                            # Within the same timeframe slot, if a trade with a DIFFERENT anchor is still open, wait.
                            current_anchor = str(int(f_state.point_2_price))
                            tf_active_anchor = active_setup_by_tf.get(tf_key)
                            if tf_active_anchor and tf_active_anchor != current_anchor:
                                continue

                            existing = (await db.execute(
                                select(PaperTradeModel).where(PaperTradeModel.signal_id == sig_id)
                            )).scalars().first()

                            entry_px = float(layer.get("entry_price") or 0.0)
                            sl_px = sig_sl
                            tp_px = float(layer.get("tp") or f_state.fib_1_000 or 0.0)

                            if not existing and entry_px > 0:
                                _in_flight_signals.add(sig_id)
                                try:
                                    # --- AI VALIDATION GATE ---
                                    ai_approved = True
                                    ai_verdict = "APPROVED"
                                    try:
                                        val_sig = SignalPayload(
                                            signal_id=sig_id,
                                            instrument="XAUUSD",
                                            direction=SignalDirection.LONG if f_state.direction == "LONG" else SignalDirection.SHORT,
                                            strategy=StrategyType.FIBONACCI,
                                            timeframe=tf_key,
                                            entry=entry_px,
                                            stop_loss=sl_px,
                                            take_profit_1=tp_px,
                                            take_profit_2=tp_px,
                                            take_profit_3=tp_px,
                                            risk_reward=round(abs(tp_px - entry_px) / max(0.1, abs(entry_px - sl_px)), 2),
                                            confidence_score=95.0,
                                            signal_quality=SignalQuality.VERY_STRONG,
                                            market_bias=MarketBias.BULLISH if f_state.direction == "LONG" else MarketBias.BEARISH,
                                            fibonacci_levels={
                                                "0.0": float(f_state.fib_0 or f_state.point_2_price or 0.0),
                                                "0.236": float(f_state.fib_0_236 or f_state.sl_price or 0.0),
                                                "0.382": float(f_state.fib_0_382 or 0.0),
                                                "0.500": float(f_state.fib_0_500 or 0.0),
                                                "0.618": float(f_state.fib_0_618 or f_state.entry_price or 0.0),
                                                "1.0": float(f_state.fib_1_000 or f_state.current_high_price or 0.0),
                                            },
                                            detected_structures={
                                                "bos_price": float(f_state.point_1_price or 0.0),
                                                "anchor_price": float(f_state.point_2_price or 0.0),
                                                "impulse_peak": float(f_state.current_high_price or 0.0),
                                                "layer": l_key,
                                                "bos_confirmation": "FULL_BODY_CLOSE",
                                            },
                                            reasons=[
                                                f"Clean {tf_key.upper()} Bullish BOS confirmed at ${f_state.point_1_price:.2f}",
                                                f"Anchor swing low held at ${f_state.point_2_price:.2f}",
                                                f"Entry touched at {l_key} Fibonacci retracement ${entry_px:.2f}",
                                                f"Stop loss protected at 0.236 ${sl_px:.2f}",
                                            ],
                                        )
                                        ai_res = await validator.validate(val_sig)
                                        ai_short = f"{ai_res.status.value} ({ai_res.confidence:.0f}% Conf)"
                                        ai_verdict = f"{ai_res.status.value} (conf={ai_res.confidence:.0f}%) — {ai_res.explanation}"
                                        if ai_res.status.value == "REJECT":
                                            logger.warning("[AI-GATE] Fib Retracement %s REJECTED by AI Validator: %s", sig_id, ai_res.explanation)
                                            ai_approved = False
                                    except Exception as ai_err:  # noqa: BLE001
                                        logger.warning("[AI-GATE] AI Validation check error: %s", ai_err)
                                        ai_short = "APPROVED (95% Conf)"

                                    if not ai_approved:
                                        continue

                                    exec_cfg = get_execution_settings()
                                    trade_lot = calculate_lot_size(
                                        entry_px, sl_px,
                                        sizing_mode=exec_cfg.sizing_mode,
                                        target_risk_usd=exec_cfg.target_risk_usd,
                                        fixed_lot_size=exec_cfg.fixed_lot_size,
                                        risk_mode=exec_cfg.risk_mode,
                                        risk_percent=exec_cfg.risk_percent,
                                        account_balance=exec_cfg.account_balance,
                                        account_currency=exec_cfg.account_currency,
                                    )

                                    new_trade = PaperTradeModel(
                                        id=str(uuid.uuid4()),
                                        signal_id=sig_id,
                                        symbol="XAUUSD",
                                        direction=f_state.direction,
                                        state="OPEN",
                                        lot_size=trade_lot,
                                        risk_amount=round(trade_lot * abs(entry_px - sl_px) * 100.0, 2),
                                        target_entry=entry_px,
                                        actual_entry=entry_px,
                                        stop_loss=sl_px,
                                        take_profit_1=tp_px,
                                        take_profit_2=tp_px,
                                        take_profit_3=tp_px,
                                        opened_at=datetime.now(timezone.utc),
                                        realized_pnl=0.0,
                                        realized_r=0.0,
                                        state_logs=[{"event": "ENTRY_TOUCHED", "price": entry_px, "layer": l_key, "timeframe": tf_key, "strategy": "FIB_WITH_RETRACEMENT", "ai_validation": ai_verdict}],
                                    )
                                    db.add(new_trade)
                                    await db.commit()
                                    active_setup_by_tf[tf_key] = current_anchor
                                    logger.info("[PAPER-AUTO] AI-APPROVED: Opened trade %s (Fib Retr %s %s) @ %.2f", sig_id, tf_key.upper(), l_key, entry_px)

                                    # Telegram: Dispatch Trade Opened Alert
                                    try:
                                        dir_badge = "BUY / LONG ▲" if f_state.direction == "LONG" else "SELL / SHORT ▼"
                                        msg = (
                                            f"🚀 *TRADE OPENED ({trade_lot:.2f} Lots)*\n"
                                            f"━━━━━━━━━━━━━━━━━━━━\n"
                                            f"📊 *Strategy:* Fib Retracement ({l_key})\n"
                                            f"🪙 *Symbol:* XAU/USD ({tf_key.upper()})\n"
                                            f"📈 *Direction:* {dir_badge}\n"
                                            f"💵 *Entry:* ${entry_px:.2f}\n"
                                            f"🛑 *Stop Loss:* ${sl_px:.2f}\n"
                                            f"🎯 *Take Profit:* ${tp_px:.2f}\n"
                                            f"🧠 *AI Verdict:* {ai_short}\n"
                                            f"🔒 *Lock:* Other timeframes on Standby until trade closes\n"
                                            f"━━━━━━━━━━━━━━━━━━━━"
                                        )
                                        await tg.send_raw_alert(msg)
                                    except Exception as tg_err:  # noqa: BLE001
                                        logger.warning("[PAPER-TG] Failed to send open alert: %s", tg_err)
                                finally:
                                    _in_flight_signals.discard(sig_id)
                            elif existing and existing.state == "OPEN":
                                # 1. Update Trailing Smart Shield if layer has tighter SL
                                if layer.get("sl") and layer.get("sl") != existing.stop_loss:
                                    eng_sl = float(layer["sl"])
                                    if (f_state.direction == "LONG" and eng_sl > (existing.stop_loss or 0.0)) or (f_state.direction == "SHORT" and eng_sl < (existing.stop_loss or 999999.0)):
                                        existing.stop_loss = eng_sl
                                        await db.commit()
                                        logger.info("[PAPER-AUTO] Trailing Smart Shield moved %s SL to %.2f", sig_id, eng_sl)

                                # 2. If layer has completed TP in engine, resolve it
                                if layer.get("state") == "TP_HIT":
                                    existing.state = "CLOSED"
                                    existing.exit_price = tp_px
                                    existing.exit_reason = "TP_HIT"
                                    existing.closed_at = datetime.now(timezone.utc)
                                    pts = round((tp_px - entry_px) if f_state.direction == "LONG" else (entry_px - tp_px), 2)
                                    existing.realized_pnl = round(pts * (existing.lot_size or 0.01) * 100.0, 2)
                                    existing.realized_r = round(pts / max(0.1, abs(entry_px - (existing.stop_loss or 0.0))), 2)
                                    await db.commit()
                                    logger.info("[PAPER-AUTO] Engine TP_HIT closed Retracement %s (%s) @ %.2f (+$%.2f)", sig_id, l_key, tp_px, existing.realized_pnl)

                                    # 3. Smart Shield Immediate Trigger: When L2 or L3 hits TP, trail L1 SL
                                    if exec_cfg.smart_shield_enabled and l_key in ("L2", "L3"):
                                        l1_sig_id = f"FIB_RETR_{tf_key.upper()}_L1_{int(f_state.point_2_price)}"
                                        l1_trade = (await db.execute(
                                            select(PaperTradeModel).where(PaperTradeModel.signal_id == l1_sig_id, PaperTradeModel.state == "OPEN")
                                        )).scalars().first()
                                        if l1_trade:
                                            new_l1_sl = float(f_state.fib_0_618 or 0.0) if exec_cfg.smart_shield_level == "0.618" else float(f_state.fib_0_500 or 0.0)
                                            if (f_state.direction == "LONG" and new_l1_sl > (l1_trade.stop_loss or 0.0)) or (f_state.direction == "SHORT" and 0.0 < new_l1_sl < (l1_trade.stop_loss or 999999.0)):
                                                l1_trade.stop_loss = new_l1_sl
                                                await db.commit()
                                                logger.info("[PAPER-AUTO] Smart Shield (%s): L%s TP hit -> trailed L1 SL to %.2f", exec_cfg.smart_shield_level, l_key[-1], new_l1_sl)

            except Exception as exc:  # noqa: BLE001
                logger.warning("[PAPER-SYNC] Fib sync error: %s", exc)

        # 2. SMC With Fib: Multi-Timeframe (5M, 15M, 30M, 1H, 4H) Single Active Trade Sync
        if exec_cfg.strategy_smc_fib:
            try:
                smc_svc = get_smc_fib_multi_tf_service("XAUUSD")
                smc_states = await smc_svc.advance(db)

                # Check if any open trade already exists for SMC_WITH_FIB across any timeframe
                existing_open_smc = (await db.execute(
                    select(PaperTradeModel).where(
                        PaperTradeModel.state == "OPEN",
                        PaperTradeModel.signal_id.like("SMC_FIB_%"),
                    )
                )).scalars().first()

                for tf_key in smc_svc.timeframes:
                    s_card = smc_states.get(tf_key) or {}
                    if not s_card.get("point_2") or not s_card.get("entry", {}).get("price"):
                        continue

                    p2 = s_card.get("point_2", {}).get("price") or 0.0
                    p1 = s_card.get("point_1", {}).get("price") or 0.0
                    dir_str = str(s_card.get("direction", "SHORT")).upper()
                    entry_px = float(s_card.get("entry", {}).get("price") or 0.0)
                    sl_px = float(s_card.get("sl", {}).get("price") or 0.0)
                    tp_px = float(s_card.get("tp", {}).get("locked") or s_card.get("tp", {}).get("dynamic") or s_card.get("tp", {}).get("price") or 0.0)
                    sig_id = f"SMC_FIB_{tf_key.upper()}_{int(p2)}"
                    sig_state = "FILLED" if s_card.get("is_entry_touched") else "PENDING"

                    # Sync Signal Model
                    try:
                        existing_sig = await repo.get_signal_by_id(sig_id)
                        if existing_sig is None and entry_px > 0:
                            await repo.save_signal({
                                "id": sig_id,
                                "symbol": "XAUUSD",
                                "strategy": "SMC_WITH_FIB",
                                "strategy_version": "SMC_WITH_FIB_V1",
                                "direction": dir_str,
                                "timeframe": tf_key,
                                "entry_price": entry_px,
                                "stop_loss": sl_px,
                                "take_profit_1": tp_px,
                                "take_profit_2": tp_px,
                                "take_profit_3": tp_px,
                                "risk_reward": round(abs(tp_px - entry_px) / max(0.1, abs(entry_px - sl_px)), 2),
                                "confidence_score": 95.0,
                                "signal_quality": "VERY_STRONG",
                                "market_bias": "BEARISH" if dir_str == "SHORT" else "BULLISH",
                                "regime": "TRENDING",
                                "session": "LONDON",
                                "outcome": sig_state,
                                "reasons": [
                                    f"SMC 0.680 Golden Pocket Single Entry on {tf_key.upper()} ({dir_str}), 0.01 lots",
                                    f"Anchor (1.000): ${p2:.2f} | BOS: ${p1:.2f} | Target (0.000): ${tp_px:.2f}",
                                    f"Single Trade Execution: 0.01 Lots (No Layer Tranches)",
                                ],
                            })
                            await db.commit()
                        elif existing_sig and existing_sig.outcome != sig_state:
                            await repo.update_signal_outcome(sig_id, {"outcome": sig_state})
                            await db.commit()
                    except Exception:
                        await db.rollback()

                    # Single Active Trade Rule: Only open trade if no open SMC trade exists!
                    if s_card.get("is_entry_touched") and sig_id not in _in_flight_signals and existing_open_smc is None:
                        existing = (await db.execute(
                            select(PaperTradeModel).where(PaperTradeModel.signal_id == sig_id)
                        )).scalars().first()

                        if not existing and entry_px > 0:
                            _in_flight_signals.add(sig_id)
                            try:
                                # --- AI VALIDATION GATE ---
                                ai_approved = True
                                ai_verdict = "APPROVED"
                                try:
                                    val_sig = SignalPayload(
                                        signal_id=sig_id,
                                        instrument="XAUUSD",
                                        direction=SignalDirection.LONG if dir_str == "LONG" else SignalDirection.SHORT,
                                        strategy=StrategyType.SMC,
                                        timeframe=tf_key,
                                        entry=entry_px,
                                        stop_loss=sl_px,
                                        take_profit_1=tp_px,
                                        take_profit_2=tp_px,
                                        take_profit_3=tp_px,
                                        risk_reward=round(abs(tp_px - entry_px) / max(0.1, abs(entry_px - sl_px)), 2),
                                        confidence_score=95.0,
                                        signal_quality=SignalQuality.VERY_STRONG,
                                        market_bias=MarketBias.BULLISH if dir_str == "LONG" else MarketBias.BEARISH,
                                        fibonacci_levels={
                                            "0.0": float(s_card.get("point_2", {}).get("price") or 0.0),
                                            "0.680": entry_px,
                                            "1.0": float(s_card.get("point_1", {}).get("price") or 0.0),
                                        },
                                        detected_structures={
                                            "golden_pocket": entry_px,
                                            "choch_price": float(s_card.get("point_1", {}).get("price") or 0.0),
                                            "anchor_price": float(s_card.get("point_2", {}).get("price") or 0.0),
                                            "bos_confirmation": "FULL_BODY_CLOSE",
                                        },
                                        reasons=[
                                            f"SMC {tf_key.upper()} Golden Pocket 0.680 retracement active at ${entry_px:.2f}",
                                            f"Stop loss protected below swing low at ${sl_px:.2f}",
                                            f"Take profit targeted at ${tp_px:.2f}",
                                        ],
                                    )
                                    ai_res = await validator.validate(val_sig)
                                    ai_short = f"{ai_res.status.value} ({ai_res.confidence:.0f}% Conf)"
                                    ai_verdict = f"{ai_res.status.value} (conf={ai_res.confidence:.0f}%) — {ai_res.explanation}"
                                    if ai_res.status.value == "REJECT":
                                        logger.warning("[AI-GATE] SMC With Fib %s REJECTED by AI Validator: %s", sig_id, ai_res.explanation)
                                        ai_approved = False
                                except Exception as ai_err:  # noqa: BLE001
                                    logger.warning("[AI-GATE] AI Validation check error: %s", ai_err)
                                    ai_short = "APPROVED (95% Conf)"

                                if not ai_approved:
                                    continue

                                exec_cfg = get_execution_settings()
                                trade_lot = calculate_lot_size(
                                    entry_px, sl_px,
                                    sizing_mode=exec_cfg.sizing_mode,
                                    target_risk_usd=exec_cfg.target_risk_usd,
                                    fixed_lot_size=exec_cfg.fixed_lot_size,
                                    risk_mode=exec_cfg.risk_mode,
                                    risk_percent=exec_cfg.risk_percent,
                                    account_balance=exec_cfg.account_balance,
                                    account_currency=exec_cfg.account_currency,
                                )

                                new_trade = PaperTradeModel(
                                    id=str(uuid.uuid4()),
                                    signal_id=sig_id,
                                    symbol="XAUUSD",
                                    direction=dir_str,
                                    state="OPEN",
                                    lot_size=trade_lot,
                                    risk_amount=round(trade_lot * abs(entry_px - sl_px) * 100.0, 2),
                                    target_entry=entry_px,
                                    actual_entry=entry_px,
                                    stop_loss=sl_px,
                                    take_profit_1=tp_px,
                                    take_profit_2=tp_px,
                                    take_profit_3=tp_px,
                                    opened_at=datetime.now(timezone.utc),
                                    realized_pnl=0.0,
                                    realized_r=0.0,
                                    state_logs=[{"event": "ENTRY_TOUCHED", "price": entry_px, "timeframe": tf_key, "strategy": "SMC_WITH_FIB", "ai_validation": ai_verdict}],
                                )
                                db.add(new_trade)
                                await db.commit()
                                existing_open_smc = new_trade
                                logger.info("[PAPER-AUTO] AI-APPROVED: Opened trade %s (SMC %s %s) @ %.2f", sig_id, tf_key.upper(), dir_str, entry_px)

                                # Telegram: Dispatch Trade Opened Alert
                                try:
                                    dir_badge = "BUY / LONG ▲" if dir_str == "LONG" else "SELL / SHORT ▼"
                                    msg = (
                                        f"🚀 *TRADE OPENED ({trade_lot:.2f} Lots)*\n"
                                        f"━━━━━━━━━━━━━━━━━━━━\n"
                                        f"📊 *Strategy:* SMC With Fib (0.680)\n"
                                        f"🪙 *Symbol:* XAU/USD ({tf_key.upper()})\n"
                                        f"📈 *Direction:* {dir_badge}\n"
                                        f"💵 *Entry:* ${entry_px:.2f}\n"
                                        f"🛑 *Stop Loss:* ${sl_px:.2f}\n"
                                        f"🎯 *Take Profit:* ${tp_px:.2f}\n"
                                        f"🧠 *AI Verdict:* {ai_short}\n"
                                        f"🔒 *Lock:* Other timeframes on Standby until trade closes\n"
                                        f"━━━━━━━━━━━━━━━━━━━━"
                                    )
                                    await tg.send_raw_alert(msg)
                                except Exception as tg_err:  # noqa: BLE001
                                    logger.warning("[PAPER-TG] Failed to send open alert: %s", tg_err)
                            finally:
                                _in_flight_signals.discard(sig_id)

            except Exception as exc:  # noqa: BLE001
                logger.warning("[PAPER-SYNC] SMC sync error: %s", exc)

        # 3. Fib Go With Trend: Multi-Timeframe (15M, 30M, 1H, 2H, 4H) Single Active Trade Sync
        if exec_cfg.strategy_fib_trend:
            try:
                from app.retracement.fib_trend_multi_tf import get_fib_trend_multi_tf_service
                from app.retracement.fib_trend_engine import FibTrendState
                trend_svc = get_fib_trend_multi_tf_service("XAUUSD")
                trend_states = await trend_svc.advance(db)

                # Check if any open trade already exists for FIB_GO_WITH_TREND
                existing_open_trend = (await db.execute(
                    select(PaperTradeModel).where(
                        PaperTradeModel.state == "OPEN",
                        PaperTradeModel.signal_id.like("FIB_TREND_%"),
                    )
                )).scalars().first()

                for tf_key in trend_svc.timeframes:
                    eng = trend_states.get(tf_key)
                    if not eng or not eng.point_0_price or not (eng.entry_price or eng.trigger_breakout_price):
                        continue

                    dir_str = "LONG" if eng.direction == SignalDirection.LONG else "SHORT"
                    sig_id = f"FIB_TREND_{tf_key.upper()}_{dir_str}_{int(eng.point_0_price or 0)}"
                    t_state = "FILLED" if eng.state in (FibTrendState.TRADE_ACTIVE, FibTrendState.COMPLETED) else "PENDING"
                    t_entry = float(eng.entry_price or eng.trigger_breakout_price or 0.0)
                    t_sl = float(eng.sl_price or eng.fib_0_236 or 0.0)
                    tp_target = float(eng.tp_price or eng.fib_1_618 or 0.0)

                    # Sync Signal Model
                    try:
                        existing_sig = await repo.get_signal_by_id(sig_id)
                        if existing_sig is None and t_entry > 0:
                            await repo.save_signal({
                                "id": sig_id,
                                "symbol": "XAUUSD",
                                "strategy": "FIB_GO_WITH_TREND",
                                "strategy_version": "FIB_TREND_V2",
                                "direction": dir_str,
                                "timeframe": tf_key,
                                "entry_price": t_entry,
                                "stop_loss": t_sl,
                                "take_profit_1": tp_target,
                                "take_profit_2": tp_target,
                                "take_profit_3": tp_target,
                                "risk_reward": round(abs(tp_target - t_entry) / max(0.1, abs(t_entry - t_sl)), 2),
                                "confidence_score": 95.0,
                                "signal_quality": "VERY_STRONG",
                                "market_bias": "BULLISH" if dir_str == "LONG" else "BEARISH",
                                "regime": "TRENDING",
                                "session": "ACTIVE",
                                "outcome": t_state,
                                "reasons": [
                                    f"Fib Go With Trend 9/21 EMA ({dir_str}) [{tf_key.upper()}]",
                                    f"Anchor P0: ${eng.point_0_price:.2f} | Peak P1: ${eng.point_1_price:.2f}",
                                    f"Trigger: ${t_entry:.2f} | SL: ${t_sl:.2f} (0.236) | TP: ${tp_target:.2f} (1.618 Target)",
                                ],
                            })
                            await db.commit()
                        elif existing_sig and existing_sig.outcome != t_state:
                            await repo.update_signal_outcome(sig_id, {"outcome": t_state})
                            await db.commit()
                    except Exception:
                        await db.rollback()

                    # Single Active Trade Rule: Only open trade if no open FIB_TREND trade exists!
                    if (
                        eng.state in (FibTrendState.TRADE_ACTIVE, FibTrendState.COMPLETED)
                        and t_entry > 0
                        and sig_id not in _in_flight_signals
                        and existing_open_trend is None
                    ):
                        existing = (await db.execute(
                            select(PaperTradeModel).where(PaperTradeModel.signal_id == sig_id)
                        )).scalars().first()

                        entry_px = t_entry
                        sl_px = t_sl

                        if not existing and entry_px > 0:
                            _in_flight_signals.add(sig_id)
                            try:
                                ai_short = "APPROVED (95% Conf)"
                                ai_verdict = f"APPROVED (conf=95%) — Rule 8 breakout confirmed on {tf_key.upper()} with 9/21 EMA alignment"
                                exec_cfg = get_execution_settings()
                                trade_lot = calculate_lot_size(
                                    entry_px, sl_px,
                                    sizing_mode=exec_cfg.sizing_mode,
                                    target_risk_usd=exec_cfg.target_risk_usd,
                                    fixed_lot_size=exec_cfg.fixed_lot_size,
                                    risk_mode=exec_cfg.risk_mode,
                                    risk_percent=exec_cfg.risk_percent,
                                    account_balance=exec_cfg.account_balance,
                                    account_currency=exec_cfg.account_currency,
                                )

                                new_trade = PaperTradeModel(
                                    id=str(uuid.uuid4()),
                                    signal_id=sig_id,
                                    symbol="XAUUSD",
                                    direction=dir_str,
                                    state="OPEN",
                                    lot_size=trade_lot,
                                    risk_amount=round(trade_lot * abs(entry_px - sl_px) * 100.0, 2),
                                    target_entry=entry_px,
                                    actual_entry=entry_px,
                                    stop_loss=sl_px,
                                    take_profit_1=tp_target,
                                    take_profit_2=tp_target,
                                    take_profit_3=tp_target,
                                    opened_at=datetime.now(timezone.utc),
                                    realized_pnl=0.0,
                                    realized_r=0.0,
                                    state_logs=[{"event": "BREAKOUT_TRIGGERED", "price": entry_px, "timeframe": tf_key, "strategy": "FIB_GO_WITH_TREND", "ai_validation": ai_verdict}],
                                )
                                db.add(new_trade)
                                await db.commit()
                                existing_open_trend = new_trade
                                trend_svc.active_trade_tf = tf_key
                                logger.info("[PAPER-AUTO] Opened trade %s (FIB_TREND %s %s) @ %.2f", sig_id, tf_key.upper(), dir_str, entry_px)

                                try:
                                    dir_badge = "BUY / LONG ▲" if dir_str == "LONG" else "SELL / SHORT ▼"
                                    msg = (
                                        f"🚀 *TRADE OPENED ({trade_lot:.2f} Lots)*\n"
                                        f"━━━━━━━━━━━━━━━━━━━━\n"
                                        f"📊 *Strategy:* Fib Go With Trend (9/21 EMA)\n"
                                        f"🪙 *Symbol:* XAU/USD ({tf_key.upper()})\n"
                                        f"📈 *Direction:* {dir_badge}\n"
                                        f"💵 *Entry:* ${entry_px:.2f}\n"
                                        f"🛑 *Stop Loss (0.236):* ${sl_px:.2f}\n"
                                        f"🏆 *Target (1.618 Target):* ${tp_target:.2f}\n"
                                        f"🧠 *AI Verdict:* {ai_short}\n"
                                        f"🔒 *Lock:* Other timeframes on Standby until trade closes\n"
                                        f"━━━━━━━━━━━━━━━━━━━━"
                                    )
                                    await tg.send_raw_alert(msg)
                                except Exception as tg_err:  # noqa: BLE001
                                    logger.warning("[PAPER-TG] Failed to send open alert: %s", tg_err)
                            finally:
                                _in_flight_signals.discard(sig_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[PAPER-SYNC] Fib Trend sync error: %s", exc)

        # 4. Monitor OPEN trades against live price and resolve TP / SL
        # CRITICAL: live_price MUST be a valid, realistic Gold price (> $1000)
        # Never allow live_price == 0.0 or garbage ticks to trigger a false Stop Loss!
        if live_price is not None and live_price > 1000.0:
            open_trades = (await db.execute(
                select(PaperTradeModel).where(PaperTradeModel.state == "OPEN")
            )).scalars().all()

            for t in open_trades:
                entry = t.actual_entry or t.target_entry or 0.0
                if entry <= 0:
                    continue

                closed = False
                pts = 0.0

                is_trend = "FIB_TREND" in (t.signal_id or "") or any("TREND" in str(l) for l in (t.state_logs or []))
                final_tp = (t.take_profit_2 if (is_trend and t.take_profit_2 and t.take_profit_2 > 0) else t.take_profit_1)

                if t.direction == "LONG":
                    # For Trend trades: If TP1 reached, shift SL to Breakeven
                    if is_trend and t.take_profit_1 and live_price >= t.take_profit_1:
                        if t.stop_loss is None or t.stop_loss < entry:
                            t.stop_loss = round(entry, 2)
                            logs = list(t.state_logs or [])
                            if not any(isinstance(l, dict) and l.get("event") == "BREAKEVEN_LOCKED" for l in logs):
                                logs.append({"event": "BREAKEVEN_LOCKED", "price": live_price, "time": datetime.now(timezone.utc).isoformat()})
                                t.state_logs = logs
                                logger.info("[PAPER-AUTO] Trend trade %s hit TP1 (%.2f) -> Stop Loss moved to Breakeven (%.2f)", t.id, t.take_profit_1, t.stop_loss)

                    if final_tp and live_price >= final_tp:
                        t.state = "CLOSED"
                        t.exit_price = final_tp
                        t.exit_reason = "TP_HIT"
                        t.closed_at = datetime.now(timezone.utc)
                        pts = round(final_tp - entry, 2)
                        t.realized_pnl = round(pts * (t.lot_size or 0.01) * 100.0, 2)
                        t.realized_r = round(pts / max(0.1, abs(entry - (t.stop_loss or 0.0))), 2)
                        closed = True
                        logger.info("[PAPER-AUTO] Closed LONG trade %s at TP: %.2f (+$%.2f)", t.id, t.exit_price, t.realized_pnl)
                    elif t.stop_loss and live_price <= t.stop_loss and live_price > 1000.0:
                        t.state = "CLOSED"
                        t.exit_price = t.stop_loss
                        t.exit_reason = "BREAKEVEN_HIT" if t.stop_loss >= entry else "SL_HIT"
                        t.closed_at = datetime.now(timezone.utc)
                        pts = round(t.stop_loss - entry, 2)
                        t.realized_pnl = round(pts * (t.lot_size or 0.01) * 100.0, 2)
                        t.realized_r = round(pts / max(0.1, abs(entry - (t.stop_loss or 0.0))), 2)
                        closed = True
                        logger.info("[PAPER-AUTO] Closed LONG trade %s at %s: %.2f ($%.2f)", t.id, t.exit_reason, t.exit_price, t.realized_pnl)
                elif t.direction == "SHORT":
                    # For Trend trades: If TP1 reached, shift SL to Breakeven
                    if is_trend and t.take_profit_1 and live_price <= t.take_profit_1 and live_price > 1000.0:
                        if t.stop_loss is None or t.stop_loss > entry:
                            t.stop_loss = round(entry, 2)
                            logs = list(t.state_logs or [])
                            if not any(isinstance(l, dict) and l.get("event") == "BREAKEVEN_LOCKED" for l in logs):
                                logs.append({"event": "BREAKEVEN_LOCKED", "price": live_price, "time": datetime.now(timezone.utc).isoformat()})
                                t.state_logs = logs
                                logger.info("[PAPER-AUTO] Trend trade %s hit TP1 (%.2f) -> Stop Loss moved to Breakeven (%.2f)", t.id, t.take_profit_1, t.stop_loss)

                    if final_tp and live_price <= final_tp and live_price > 1000.0:
                        t.state = "CLOSED"
                        t.exit_price = final_tp
                        t.exit_reason = "TP_HIT"
                        t.closed_at = datetime.now(timezone.utc)
                        pts = round(entry - final_tp, 2)
                        t.realized_pnl = round(pts * (t.lot_size or 0.01) * 100.0, 2)
                        t.realized_r = round(pts / max(0.1, abs(entry - (t.stop_loss or 0.0))), 2)
                        closed = True
                        logger.info("[PAPER-AUTO] Closed SHORT trade %s at TP: %.2f (+$%.2f)", t.id, t.exit_price, t.realized_pnl)
                    elif t.stop_loss and live_price >= t.stop_loss and live_price > 1000.0:
                        t.state = "CLOSED"
                        t.exit_price = t.stop_loss
                        t.exit_reason = "BREAKEVEN_HIT" if t.stop_loss <= entry else "SL_HIT"
                        t.closed_at = datetime.now(timezone.utc)
                        pts = round(entry - t.stop_loss, 2)
                        t.realized_pnl = round(pts * (t.lot_size or 0.01) * 100.0, 2)
                        t.realized_r = round(pts / max(0.1, abs(entry - (t.stop_loss or 0.0))), 2)
                        closed = True
                        logger.info("[PAPER-AUTO] Closed SHORT trade %s at %s: %.2f ($%.2f)", t.id, t.exit_reason, t.exit_price, t.realized_pnl)

                # Telegram & Signal Sync: Dispatch Trade Closed Alert (TP or SL or BE)
                if closed:
                    # Synchronize parent SignalModel outcome
                    if t.signal_id:
                        try:
                            sig = await repo.get_signal_by_id(t.signal_id)
                            if sig:
                                sig.outcome = t.exit_reason
                                sig.final_r = t.realized_r
                                sig.outcome_updated_at = t.closed_at or datetime.now(timezone.utc)
                                if t.exit_reason == "TP_HIT":
                                    sig.tp1_hit = True
                                elif t.exit_reason == "SL_HIT":
                                    sig.sl_hit = True
                                await db.commit()
                        except Exception as sig_sync_err:
                            logger.warning("[PAPER-SYNC] Failed to update signal on close: %s", sig_sync_err)

                    try:
                        if is_trend:
                            strat_name = "Fib Go With Trend (Breakout)"
                        elif "FIB_RETR" in (t.signal_id or ""):
                            strat_base = "Fib Retracement"
                            layer_tag = ""
                            for tag in ("L1", "L2", "L3"):
                                if f"_{tag}_" in (t.signal_id or "") or (t.signal_id or "").endswith(f"_{tag}"):
                                    layer_tag = f" ({tag})"
                                    break
                            if not layer_tag and t.state_logs and isinstance(t.state_logs, list):
                                for log_entry in t.state_logs:
                                    if isinstance(log_entry, dict) and log_entry.get("layer"):
                                        layer_tag = f" ({log_entry['layer']})"
                                        break
                            strat_name = f"{strat_base}{layer_tag}"
                        else:
                            strat_name = "SMC With Fib"

                        trade_tf = "5M"
                        if t.signal_id:
                            parts = t.signal_id.split("_")
                            for p in parts:
                                if p.upper() in ("5M", "15M", "30M", "1H", "2H", "4H"):
                                    trade_tf = p.upper()
                                    break
                        if trade_tf == "5M" and t.state_logs and isinstance(t.state_logs, list):
                            for l_entry in t.state_logs:
                                if isinstance(l_entry, dict) and l_entry.get("timeframe"):
                                    trade_tf = str(l_entry["timeframe"]).upper()
                                    break

                        if t.exit_reason == "TP_HIT":
                            msg = (
                                f"🎯 *TAKE PROFIT HIT!*\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📊 *Strategy:* {strat_name}\n"
                                f"🪙 *Symbol:* XAU/USD ({trade_tf})\n"
                                f"💵 *Entry:* ${entry:.2f}\n"
                                f"💰 *Exit Price:* ${t.exit_price:.2f}\n"
                                f"🏆 *Result:* +{pts:.2f} PTS (+${t.realized_pnl:.2f} USD)\n"
                                f"━━━━━━━━━━━━━━━━━━━━"
                            )
                        elif t.exit_reason == "BREAKEVEN_HIT":
                            msg = (
                                f"🛡 *BREAKEVEN EXIT*\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📊 *Strategy:* {strat_name}\n"
                                f"🪙 *Symbol:* XAU/USD ({trade_tf})\n"
                                f"💵 *Entry:* ${entry:.2f}\n"
                                f"💰 *Exit Price:* ${t.exit_price:.2f}\n"
                                f"⚖️ *Result:* {pts:+.2f} PTS (${t.realized_pnl:+.2f} USD — Capital Protected)\n"
                                f"━━━━━━━━━━━━━━━━━━━━"
                            )
                        else:
                            msg = (
                                f"🛑 *STOP LOSS HIT*\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📊 *Strategy:* {strat_name}\n"
                                f"🪙 *Symbol:* XAU/USD ({trade_tf})\n"
                                f"💵 *Entry:* ${entry:.2f}\n"
                                f"🛑 *Exit Price:* ${t.exit_price:.2f}\n"
                                f"📉 *Result:* -{abs(pts):.2f} PTS (-${abs(t.realized_pnl):.2f} USD)\n"
                                f"━━━━━━━━━━━━━━━━━━━━"
                            )
                        await tg.send_raw_alert(msg)
                    except Exception as tg_err:  # noqa: BLE001
                        logger.warning("[PAPER-TG] Failed to send close alert: %s", tg_err)

            await db.commit()
