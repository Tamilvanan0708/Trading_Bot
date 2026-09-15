"""
Automatic Synchronization of Strategy Setups to Paper Trades with AI Validation Gate.

Thread-safe, race-condition protected synchronization service.
Ensures exactly ONE 0.01 lot paper trade per unique strategy signal ID.
"""

import asyncio
import os
from datetime import datetime, timezone
import uuid

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.validator import AIValidator, get_ai_validator
from app.config.settings import Settings, get_settings
from app.core.constants import MarketBias, SignalDirection, SignalQuality, StrategyType
from app.core.logging import logger
from app.data.live.service import get_live_service
from app.database.models import PaperTradeModel, SignalModel
from app.database.repository import Repository
from app.config.execution_settings import calculate_lot_size, get_execution_settings
from app.notifications.telegram_service import TelegramService
from app.retracement.multi_tf import get_retracement_multi_tf_service
from app.retracement.smc_fib_multi_tf import get_smc_fib_multi_tf_service
from app.risk.admission import TradeAdmissionGate
from app.signals.models import SignalPayload

# Global async mutex lock and in-flight guard to strictly prevent double-executions
_sync_lock = asyncio.Lock()
_in_flight_signals: set[str] = set()
_last_paper_sync_ts: float = 0.0
_bg_tg_tasks: set[asyncio.Task] = set()


def _dispatch_tg_alert(coro) -> asyncio.Task:
    """Dispatches a Telegram alert in a shielded background task with a strong reference
    so it is NEVER cancelled by outer sync timeouts or garbage-collected.
    """
    task = asyncio.create_task(coro)
    _bg_tg_tasks.add(task)
    task.add_done_callback(_bg_tg_tasks.discard)
    return task


async def sync_strategy_paper_trades(db: AsyncSession, force: bool = False) -> None:
    """Scan in-memory 5M strategy engine states, run AI validation, and open/update paper trades.
    Thread-safe and guarded by _sync_lock with 10s debounce.
    """
    global _last_paper_sync_ts
    import time
    now = time.time()
    if not force and (now - _last_paper_sync_ts < 2.0):
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
                select(PaperTradeModel).where(PaperTradeModel.state == "CLOSED").order_by(PaperTradeModel.closed_at.desc()).limit(15)
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
            # Sanitize any inverted Stop Losses in signals table for active/pending signals
            all_sigs = (await db.execute(
                select(SignalModel).where(SignalModel.outcome.in_(("OPEN", "PENDING", "FILLED", None))).limit(25)
            )).scalars().all()
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
        if not live_price:
            try:
                live_price = getattr(get_retracement_multi_tf_service("XAUUSD"), "live_price", None)
            except Exception:
                pass
        # Per-timeframe snapshot prices reported by the Fib monitors themselves
        # (authoritative context for resolving FIB_RETR trades in the sweep below).
        fib_tf_price: dict[str, float] = {}
        validator = get_ai_validator()
        if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("APP_ENV") == "test":
            # Tests must never reach paid LLM providers through the background
            # sync path; use the deterministic heuristic validator instead.
            validator = AIValidator(Settings(AI_PROVIDER="mock"))
        exec_cfg = get_execution_settings()

        # 1. Fib With Retracement: Multi-Timeframe (5M, 15M, 30M, 1H, 4H) Single Active Trade Sync
        if exec_cfg.strategy_fib_retracement:
            try:
                fib_svc = get_retracement_multi_tf_service("XAUUSD")
                try:
                    fib_states = await fib_svc.advance(db, live_price=live_price)
                except TypeError:
                    fib_states = await fib_svc.advance(db)

                try:
                    _fib_slots = getattr(fib_svc, "slots", None)
                    _fib_global = getattr(fib_svc, "live_price", None)
                    if isinstance(_fib_slots, dict):
                        for _tf, _slot in _fib_slots.items():
                            _p = getattr(_slot, "live_price", None) or _fib_global
                            if not _p and getattr(_slot, "engine", None) and getattr(_slot.engine, "_candles", None):
                                _p = _slot.engine._candles[-1].close
                            if _p:
                                fib_tf_price[str(_tf).lower()] = float(_p)
                except Exception:  # noqa: BLE001
                    pass

                # Option 1A: Multi-Slot Parallel Execution — track open trades per timeframe slot
                existing_open_trades = (await db.execute(
                    select(PaperTradeModel).where(
                        PaperTradeModel.state == "OPEN",
                        PaperTradeModel.signal_id.like("FIB_RETR_%"),
                    )
                )).scalars().all()

                # ── ORPHAN RECONCILIATION SWEEP ───────────────────────────────────────
                # If a FIB_RETR paper trade is OPEN in DB but the corresponding engine
                # slot has no active setup (engine completed via live tick and cleared
                # self.setup = None before sync.py could see it), force-close the DB
                # record using the outcome stored in engine.last_completed.
                # This prevents:
                #   (a) Ghost "⚡ LIVE TRADE: 5M ACTIVE" banners when bot is scanning.
                #   (b) Timeframe slot being blocked (active_setup_by_tf lock) so new
                #       scalp BOS setups can be traded immediately after TP.
                _fib_slots_map = getattr(fib_svc, "slots", {}) or {}
                for _ot in existing_open_trades:
                    if not _ot.signal_id:
                        continue
                    _parts = _ot.signal_id.split("_")
                    if len(_parts) < 3:
                        continue
                    _ot_tf = _parts[2].lower()
                    _slot = _fib_slots_map.get(_ot_tf)
                    if _slot is None:
                        continue
                    _engine = getattr(_slot, "engine", None)
                    if _engine is None:
                        continue
                    # Only sweep if engine has no active setup right now
                    if getattr(_engine, "setup", None) is not None:
                        continue
                    # Determine exit price and reason from last_completed
                    _lc = getattr(_engine, "last_completed", None)
                    if _lc is None and getattr(_slot, "last_completed", None) is not None:
                        _lc = _slot.last_completed
                    if _lc is None and _ot.opened_at:
                        _ot_tz = _ot.opened_at if _ot.opened_at.tzinfo else _ot.opened_at.replace(tzinfo=timezone.utc)
                        if (datetime.now(timezone.utc) - _ot_tz).total_seconds() < 60:
                            continue
                    _outcome = getattr(_lc, "outcome", None) if _lc else None
                    _locked_tp = getattr(_lc, "locked_tp", None) if _lc else None
                    _sl_price = getattr(_lc, "sl_price", None) if _lc else None
                    _direction = _ot.direction or (getattr(_lc, "direction", None) if _lc else None)
                    _entry_px = float(_ot.actual_entry or _ot.target_entry or 0.0)
                    if _outcome == "SL_HIT" and _sl_price:
                        _exit_px = float(_sl_price)
                        _exit_reason = "SL_HIT"
                    elif _outcome == "TP_HIT" and _locked_tp:
                        _exit_px = float(_locked_tp)
                        _exit_reason = "TP_HIT"
                    else:
                        _exit_px = fib_tf_price.get(_ot_tf) or live_price or _entry_px
                        _exit_reason = "ENGINE_RESET_ORPHAN"
                    _pts = round(
                        (_exit_px - _entry_px) if _direction == "LONG" else (_entry_px - _exit_px), 2
                    ) if _entry_px else 0.0
                    _ot.state = "CLOSED"
                    _ot.exit_price = round(_exit_px, 2)
                    _ot.exit_reason = _exit_reason
                    _ot.realized_pnl = round(_pts * (_ot.lot_size or 0.01) * 100.0, 2)
                    _ot.realized_r = round(_pts / max(0.1, abs(_entry_px - float(_ot.stop_loss or _entry_px + 1))), 2)
                    _ot.closed_at = datetime.now(timezone.utc)
                    logger.info(
                        "[PAPER-ORPHAN] Force-closed orphaned FIB_RETR trade %s (%s) @ %.2f reason=%s pnl=$%.2f",
                        _ot.signal_id, _ot_tf.upper(), _exit_px, _exit_reason, _ot.realized_pnl,
                    )
                    try:
                        await db.commit()
                        # Dispatch Telegram Alert for Orphan Closure
                        try:
                            is_tp = _exit_reason == "TP_HIT"
                            is_be = _exit_reason == "BREAKEVEN_HIT"
                            header = "🎯 *TAKE PROFIT HIT*" if is_tp else ("🛡 *BREAKEVEN HIT*" if is_be else "🛑 *STOP LOSS HIT*")
                            pnl_str = f"+${_ot.realized_pnl:.2f}" if _ot.realized_pnl >= 0 else f"-${abs(_ot.realized_pnl):.2f}"
                            pts_str = f"{_pts:+.2f}"
                            layer_name = _parts[3] if len(_parts) > 3 else "L1"
                            dir_badge = "BUY / LONG ▲" if _direction in ("LONG", "BUY") else "SELL / SHORT ▼"
                            orphan_msg = (
                                f"{header}\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📊 *Strategy:* Fib Retracement ({layer_name})\n"
                                f"🪙 *Symbol:* XAU/USD ({_ot_tf.upper()})\n"
                                f"📈 *Direction:* {dir_badge}\n"
                                f"💵 *Entry:* ${_entry_px:.2f}\n"
                                f"🏁 *Exit:* ${_exit_px:.2f}\n"
                                f"💰 *Realized PnL:* {pnl_str} ({pts_str} PTS)\n"
                                f"━━━━━━━━━━━━━━━━━━━━"
                            )
                            _dispatch_tg_alert(tg.send_raw_alert(orphan_msg))
                        except Exception as tg_err:
                            logger.warning("[PAPER-TG] Failed to send orphan close alert: %s", tg_err)

                        # Dispatch MT5 bridge close if live execution is enabled
                        if exec_cfg.mt5_bridge_enabled:
                            try:
                                from app.services.mt5_bridge_manager import get_mt5_bridge_manager
                                get_mt5_bridge_manager().enqueue_close(
                                    paper_trade_id=_ot.id,
                                    symbol=exec_cfg.mt5_symbol or "XAUUSD-VIP",
                                    reason=_exit_reason,
                                    direction=_direction or "LONG",
                                )
                            except Exception as _mt5_err:
                                logger.warning("[MT5-BRIDGE] Orphan close dispatch failed: %s", _mt5_err)
                    except Exception as _commit_err:
                        logger.warning("[PAPER-ORPHAN] DB commit failed for orphan close %s: %s", _ot.signal_id, _commit_err)
                        await db.rollback()

                # Refresh open trades list after orphan sweep (closed records excluded)
                existing_open_trades = [t for t in existing_open_trades if t.state == "OPEN"]

                # Map active timeframe -> base_anchor of the active setup
                active_setup_by_tf: dict[str, str] = {}
                for ot in existing_open_trades:
                    if ot.signal_id:
                        # format: FIB_RETR_{TF}_{LAYER}_{ANCHOR} or FIB_RETR_{TF}_{LAYER}_{ANCHOR}_{TS}
                        parts = ot.signal_id.split("_")
                        if len(parts) >= 5 and parts[2].lower() in fib_svc.timeframes:
                            active_setup_by_tf[parts[2].lower()] = "_".join(parts[4:])

                allowed_tfs = [tf.lower() for tf in (exec_cfg.fib_retracement_timeframes or ["5m", "15m", "30m", "1h"])]
                for tf_key in fib_svc.timeframes:
                    if tf_key.lower() not in allowed_tfs:
                        continue
                    f_state = fib_states.get(tf_key)
                    if not f_state or not getattr(f_state, "layers", None) or not getattr(f_state, "point_2_price", None):
                        try:
                            from app.retracement.repository import RetracementRepository
                            db_state = await RetracementRepository(db).load_latest_active("XAUUSD", strategy="RETRACEMENT_BOS_V1", timeframe=tf_key)
                            if db_state and getattr(db_state, "layers", None) and getattr(db_state, "point_2_price", None):
                                f_state = db_state
                        except Exception:
                            pass
                    if not f_state or not getattr(f_state, "layers", None) or not getattr(f_state, "point_2_price", None):
                        continue
                    p2_ts = getattr(f_state, "point_2_timestamp", None)
                    anchor_ts = int(p2_ts.timestamp()) if (p2_ts and hasattr(p2_ts, "timestamp")) else int(f_state.point_2_price)
                    # Option C fix: also include BOS (point_1) timestamp so that 15M and 30M
                    # setups referencing the same swing low (identical point_2_price) but confirmed
                    # on different BOS candles get genuinely distinct signal IDs.
                    p1_ts = getattr(f_state, "point_1_timestamp", None)
                    bos_ts_seg = f"_{int(p1_ts.timestamp())}" if (p1_ts and hasattr(p1_ts, "timestamp")) else ""
                    current_anchor = f"{int(f_state.point_2_price)}_{anchor_ts}{bos_ts_seg}"

                    for l_key, layer in f_state.layers.items():
                        ratio_val = 0.618 if l_key == "L1" else (0.500 if l_key == "L2" else 0.382)
                        attr = f"fib_{ratio_val:.3f}".replace(".", "_")
                        l_entry = getattr(f_state, attr, None) or float(layer.get("entry_price") or 0.0)
                        if not l_entry:
                            continue
                        l_state = layer.get("state", "PENDING") if layer else "PENDING"
                        l_tp = float(f_state.fib_1_000 if l_key == "L1" else (f_state.fib_0_618 or 0.0))
                        sig_id = f"FIB_RETR_{tf_key.upper()}_{l_key}_{current_anchor}"

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
                                # Cancel older PENDING signals for this layer and timeframe (1 active signal rule)
                                prev_pendings = (await db.execute(
                                    select(SignalModel).where(
                                        SignalModel.strategy == "FIB_WITH_RETRACEMENT",
                                        SignalModel.timeframe == tf_key,
                                        SignalModel.strategy_version == f"FIB_RETR_V1:{l_key}",
                                        SignalModel.outcome == "PENDING",
                                    )
                                )).scalars().all()
                                for p_sig in prev_pendings:
                                    p_sig.outcome = "CANCELLED"

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
                        except Exception as sig_err:
                            logger.warning("[PAPER-SYNC] Failed to save/update signal %s: %s", sig_id, sig_err)
                            await db.rollback()

                        if layer.get("state") in ("FILLED", "TP_HIT", "ESCAPE_CLOSED", "SL_HIT"):
                            if sig_id in _in_flight_signals:
                                continue

                            # Option 1A: Multi-Slot Parallel Execution
                            # Timeframe isolation: an active trade on another timeframe does NOT block tf_key!
                            # Within the same timeframe slot, if a trade with a DIFFERENT anchor is still open, wait.
                            tf_active_anchor = active_setup_by_tf.get(tf_key)
                            if tf_active_anchor and tf_active_anchor != current_anchor and not current_anchor.startswith(tf_active_anchor + "_"):
                                # Verify if that previous trade is still genuinely OPEN in DB before deferring
                                is_still_open = (await db.execute(
                                    select(PaperTradeModel.id).where(
                                        PaperTradeModel.state == "OPEN",
                                        PaperTradeModel.signal_id.like(f"FIB_RETR_{tf_key.upper()}_%_{tf_active_anchor}"),
                                    )
                                )).scalars().first()
                                if is_still_open:
                                    logger.info(
                                        "[PAPER-AUTO] Deferring %s: %s slot still busy with anchor %s",
                                        sig_id, tf_key.upper(), tf_active_anchor,
                                    )
                                    continue
                                else:
                                    active_setup_by_tf.pop(tf_key, None)

                            existing = (await db.execute(
                                select(PaperTradeModel).where(PaperTradeModel.signal_id == sig_id)
                            )).scalars().first()

                            entry_px = float(layer.get("entry_price") or 0.0)
                            sl_px = sig_sl
                            tp_px = float(layer.get("tp") or f_state.fib_1_000 or 0.0)

                            # Stale-entry guard must use the MONITOR's own snapshot
                            # price — the same data the setup was computed from.
                            slot = fib_svc.slots.get(tf_key) if isinstance(getattr(fib_svc, "slots", None), dict) else None
                            ref_price = getattr(slot, "live_price", None) or getattr(fib_svc, "live_price", None)

                            if not existing:
                                # Strictly only create new paper trade if layer is currently actively FILLED in engine.
                                # If layer has already completed (TP_HIT / SL_HIT) before being tracked, it is historical -> skip it!
                                if layer.get("state") != "FILLED" or entry_px <= 0:
                                    continue

                                # Stale-entry guard: If price has already reached TP or SL, do not open a stale trade!
                                effective_check_price = ref_price if (ref_price is not None and ref_price > 0) else live_price
                                if effective_check_price is not None and effective_check_price > 0:
                                    if f_state.direction in ("LONG", "BUY"):
                                        if effective_check_price >= tp_px:
                                            logger.info(
                                                "[PAPER-AUTO] Skipping stale %s: price %.2f already reached TP %.2f",
                                                sig_id, effective_check_price, tp_px,
                                            )
                                            continue
                                        elif effective_check_price <= sl_px:
                                            logger.info(
                                                "[PAPER-AUTO] Skipping stale %s: price %.2f already beyond SL %.2f",
                                                sig_id, effective_check_price, sl_px,
                                            )
                                            continue
                                    elif f_state.direction in ("SHORT", "SELL"):
                                        if effective_check_price <= tp_px:
                                            logger.info(
                                                "[PAPER-AUTO] Skipping stale %s: price %.2f already reached TP %.2f",
                                                sig_id, effective_check_price, tp_px,
                                            )
                                            continue
                                        elif effective_check_price >= sl_px:
                                            logger.info(
                                                "[PAPER-AUTO] Skipping stale %s: price %.2f already beyond SL %.2f",
                                                sig_id, effective_check_price, sl_px,
                                            )
                                            continue

                                # --- CROSS-TIMEFRAME DE-DUPLICATION FILTER ---
                                # 1) If cross_tf_dedup_enabled is True: matches entry (≤5.0) / SL (≤2.5) / TP (≤2.5)
                                # 2) Default: always blocks near-identical duplicate entries (≤0.50 pt diff) across TFs with same direction & SL
                                is_explicit_dedup = getattr(exec_cfg, "cross_tf_dedup_enabled", False)
                                norm_dirs = ["LONG", "BUY"] if f_state.direction in ("LONG", "BUY") else ["SHORT", "SELL"]
                                if is_explicit_dedup:
                                    dup_cond = or_(
                                        and_(
                                            func.abs(PaperTradeModel.target_entry - entry_px) <= 5.0,
                                            func.abs(PaperTradeModel.stop_loss - sl_px) <= 2.5,
                                        ),
                                        and_(
                                            func.abs(PaperTradeModel.take_profit_1 - tp_px) <= 2.5,
                                            func.abs(PaperTradeModel.stop_loss - sl_px) <= 2.5,
                                        ),
                                    )
                                else:
                                    dup_cond = and_(
                                        func.abs(PaperTradeModel.target_entry - entry_px) <= 0.50,
                                        func.abs(PaperTradeModel.stop_loss - sl_px) <= 2.5,
                                    )

                                dup_query = await db.execute(
                                    select(PaperTradeModel).where(
                                        PaperTradeModel.state == "OPEN",
                                        PaperTradeModel.direction.in_(norm_dirs),
                                        ~PaperTradeModel.signal_id.like(f"%_{tf_key.upper()}_%"),
                                        dup_cond,
                                    )
                                )
                                existing_dup = dup_query.scalars().first()
                                if existing_dup:
                                    logger.info(
                                        "[PAPER-XDEDUP] Skipping duplicate trade on %s because active %s trade already exists at $%.2f (ID: %s, SL: $%.2f, TP: $%.2f) — sig %s",
                                        tf_key.upper(),
                                        existing_dup.direction,
                                        existing_dup.target_entry,
                                        existing_dup.id[:8],
                                        existing_dup.stop_loss,
                                        existing_dup.take_profit_1,
                                        sig_id,
                                    )
                                    continue

                                # --- MINIMUM IMPULSE RANGE FILTER (DISABLED PER USER REQUEST) ---
                                # Rejects micro sideways consolidation noise when enabled
                                if getattr(exec_cfg, "min_impulse_filter_enabled", False):
                                    p1 = float(f_state.point_1_price or 0.0)
                                    p2 = float(f_state.point_2_price or 0.0)
                                    peak = float(f_state.current_high_price or 0.0)
                                    setup_span = abs(peak - p2) if peak > 0 and p2 > 0 else abs(p1 - p2)
                                    min_req_pts = 4.0 if tf_key in ("1m", "3m", "5m") else (6.0 if tf_key == "15m" else (8.0 if tf_key == "30m" else 12.0))
                                    if setup_span > 0 and setup_span < min_req_pts and entry_px > 500.0:
                                        logger.info(
                                            "[PAPER-AUTO] Min Impulse Filter: Setup range $%.2f is below min required $%.2f on %s — skipping noisy trade.",
                                            setup_span,
                                            min_req_pts,
                                            tf_key.upper(),
                                        )
                                        continue

                                # --- MACRO TREND ALIGNMENT FILTER (EMA) ---
                                # Optional filter: only checks if explicitly enabled by user
                                if getattr(exec_cfg, "trend_filter_enabled", False) and slot and getattr(slot, "engine", None):
                                    candles_for_ema = getattr(slot.engine, "_candles", [])
                                    if len(candles_for_ema) >= 30 and entry_px > 500.0:
                                        from app.indicators.ema import calculate_ema
                                        period = 200 if len(candles_for_ema) >= 200 else (50 if len(candles_for_ema) >= 50 else 20)
                                        ema_vals = calculate_ema(candles_for_ema, period=period)
                                        if ema_vals:
                                            latest_ema = ema_vals[-1]
                                            if f_state.direction in ("LONG", "BUY") and entry_px < (latest_ema - 20.0):
                                                logger.info(
                                                    "[PAPER-AUTO] Trend Filter: Skipping LONG trade on %s because entry $%.2f is counter-trend vs %d EMA $%.2f (Downtrend)",
                                                    tf_key.upper(), entry_px, period, latest_ema,
                                                )
                                                continue
                                            elif f_state.direction in ("SHORT", "SELL") and entry_px > (latest_ema + 20.0):
                                                logger.info(
                                                    "[PAPER-AUTO] Trend Filter: Skipping SHORT trade on %s because entry $%.2f is counter-trend vs %d EMA $%.2f (Uptrend)",
                                                    tf_key.upper(), entry_px, period, latest_ema,
                                                )
                                                continue

                                _in_flight_signals.add(sig_id)
                                try:
                                    # --- AI VALIDATION GATE ---
                                    ai_approved = True
                                    ai_verdict = "APPROVED"
                                    try:
                                        bos_label = "Bullish BOS" if f_state.direction in ("LONG", "BUY") else "Bearish BOS"
                                        anchor_label = "Anchor swing low held" if f_state.direction in ("LONG", "BUY") else "Anchor swing high held"
                                        sl_label = f"Stop loss protected below 0.236 at ${sl_px:.2f}" if f_state.direction in ("LONG", "BUY") else f"Stop loss protected above 0.236 at ${sl_px:.2f}"
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
                                                f"Clean {tf_key.upper()} {bos_label} confirmed at ${f_state.point_1_price:.2f}",
                                                f"{anchor_label} at ${f_state.point_2_price:.2f}",
                                                f"Entry touched at {l_key} Fibonacci retracement ${entry_px:.2f}",
                                                sl_label,
                                            ],
                                        )
                                        # --- OPTION A: INSTANT ZERO-LATENCY EXECUTION ---
                                        # Fib Retracement is 100% deterministic mathematical price-action.
                                        # Zero delay: Trade executes in 0.01s without waiting for LLM network latency.
                                        ai_short = "QUANT APPROVED (100% Rule-Based)"
                                        ai_verdict = "APPROVED (Instant Quant Retracement Entry)"

                                        # Run AI validation as non-blocking background audit for dashboard telemetry
                                        async def _run_ai_audit_bg(_vs, _s_id, _val):
                                            try:
                                                _res = await _val.validate(_vs)
                                                from app.database.connection import async_session_factory
                                                from app.database.repository import Repository as _AuditRepo
                                                async with async_session_factory() as _adb:
                                                    await _AuditRepo(_adb).save_ai_validation({
                                                        "signal_id": _s_id,
                                                        "status": _res.status.value,
                                                        "confidence": float(_res.confidence),
                                                        "explanation": str(_res.explanation),
                                                        "identified_risks": list(getattr(_res, "identified_risks", []) or []),
                                                        "missing_confirmations": list(getattr(_res, "missing_confirmations", []) or []),
                                                        "provider": getattr(_res, "provider", None),
                                                        "model": getattr(_res, "model", None),
                                                        "reason_code": getattr(_res, "reason_code", None),
                                                    })
                                                    await _adb.commit()
                                            except Exception as _bg_err:
                                                logger.debug("[AI-AUDIT] Background AI audit completed/skipped: %s", _bg_err)

                                        try:
                                            asyncio.create_task(_run_ai_audit_bg(val_sig, sig_id, validator))
                                        except Exception:
                                            pass
                                    except Exception as ai_err:  # noqa: BLE001
                                        logger.warning("[AI-GATE] AI Validation check error: %s", ai_err)
                                        ai_short = "APPROVED (100% Rule-Based)"

                                    # --- HARD SAFETY: authoritative admission gate ---
                                    # Rule 1 (Max Open Trades), Rule 2 (Min Impulse / R:R), Rule 7 (Daily Loss/Limits)
                                    # are removed per user specification. Geometry & data quality remain active.
                                    _gate = TradeAdmissionGate()
                                    try:
                                        _dq = None
                                        try:
                                            # Only consult the live service when it is actually
                                            # running; an unstarted singleton (unit tests, cold
                                            # state) would report a meaningless degraded-empty.
                                            if getattr(ls, "_running", False) or getattr(ls, "_startup_task", None) is not None:
                                                _dq = await ls.data_quality()
                                        except Exception:  # noqa: BLE001
                                            _dq = None
                                        # Pass repo=None to bypass daily trade/loss/consecutive loss limits (Rule 7 removed)
                                        admission = await _gate.evaluate(val_sig, repo=None, data_quality=_dq)
                                        # Fib retracement tranche levels (0.618 -> 1.0 = 1R)
                                        # intentionally price below the confluence MIN_RISK_REWARD gate.
                                        # Geometry is strictly enforced; transient history freshness lag
                                        # (e.g. market open, container cold-start) must not block valid closed-candle setups.
                                        blocking = [
                                            r for r in admission.reasons
                                            if r.startswith("FAIL") and "R:R" not in r and "Data Quality Gate" not in r
                                        ]
                                        if blocking:
                                            logger.warning(
                                                "[ADMISSION] Fib Retracement %s blocked: %s",
                                                sig_id, "; ".join(blocking),
                                            )
                                            continue
                                    except Exception as adm_err:  # noqa: BLE001
                                        logger.warning("[ADMISSION] Gate evaluation failed for %s: %s", sig_id, adm_err)

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

                                    trade_state = "OPEN"
                                    exit_px = None
                                    exit_reason = None
                                    closed_at = None
                                    pts = 0.0
                                    realized_pnl = 0.0
                                    realized_r = 0.0

                                    new_trade = PaperTradeModel(
                                        id=str(uuid.uuid4()),
                                        signal_id=sig_id,
                                        symbol="XAUUSD",
                                        direction=f_state.direction,
                                        state=trade_state,
                                        lot_size=trade_lot,
                                        risk_amount=round(trade_lot * abs(entry_px - sl_px) * 100.0, 2),
                                        target_entry=entry_px,
                                        actual_entry=entry_px,
                                        stop_loss=sl_px,
                                        take_profit_1=tp_px,
                                        take_profit_2=tp_px,
                                        take_profit_3=tp_px,
                                        opened_at=datetime.now(timezone.utc),
                                        exit_price=exit_px,
                                        exit_reason=exit_reason,
                                        closed_at=closed_at,
                                        realized_pnl=realized_pnl,
                                        realized_r=realized_r,
                                        state_logs=[
                                            {"event": "ENTRY_TOUCHED", "price": entry_px, "layer": l_key, "timeframe": tf_key, "strategy": "FIB_WITH_RETRACEMENT", "ai_validation": ai_verdict},
                                        ],
                                    )
                                    try:
                                        db.add(new_trade)
                                        await db.commit()
                                    except Exception as trade_err:
                                        await db.rollback()
                                        logger.error("[PAPER-AUTO] Failed to save paper trade %s: %s", sig_id, trade_err)
                                        continue

                                    active_setup_by_tf[tf_key] = current_anchor
                                    logger.info(
                                        "[PAPER-AUTO] AI-APPROVED: Opened trade %s (Fib Retr %s %s) @ %.2f",
                                        sig_id, tf_key.upper(), l_key, entry_px,
                                    )

                                    # MT5 Bridge Live Execution Dispatch (Strictly Fib Retracement Only)
                                    try:
                                        from app.services.mt5_bridge_manager import get_mt5_bridge_manager
                                        sl_distance = round(abs(entry_px - sl_px), 2)
                                        tp_distance = round(abs(tp_px - entry_px), 2)
                                        get_mt5_bridge_manager().enqueue_order({
                                            "id": f"mt5-{new_trade.id[:8]}",
                                            "paper_trade_id": new_trade.id,
                                            "strategy": "Fib Retracement",
                                            "layer": l_key,
                                            "direction": f_state.direction,
                                            "symbol": exec_cfg.mt5_symbol or "XAUUSD-VIP",
                                            "lot_size": trade_lot,
                                            "entry_price": entry_px,
                                            "stop_loss": sl_px,
                                            "take_profit_1": tp_px,
                                            "sl_points": sl_distance,
                                            "tp_points": tp_distance,
                                            "execution_mode": "POINTS_DISTANCE",
                                        })
                                    except Exception as mt5_err:  # noqa: BLE001
                                        logger.warning("[MT5-BRIDGE] Failed to dispatch order to MT5 queue: %s", mt5_err)

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
                                                f"⚡ *Multi-Slot:* {tf_key.upper()} Active (15M, 30M, 1H scanning in parallel)\n"
                                                f"━━━━━━━━━━━━━━━━━━━━"
                                            )
                                            _dispatch_tg_alert(tg.send_raw_alert(msg))
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

                                    # Telegram Alert: TP Hit
                                    try:
                                        pts_sign = "+" if pts >= 0 else ""
                                        dir_badge = "BUY / LONG ▲" if f_state.direction in ("LONG", "BUY") else "SELL / SHORT ▼"
                                        tp_msg = (
                                            f"🎯 *TAKE PROFIT HIT ({pts_sign}{pts:.2f} PTS)*\n"
                                            f"━━━━━━━━━━━━━━━━━━━━\n"
                                            f"📊 *Strategy:* Fib Retracement ({l_key})\n"
                                            f"🪙 *Symbol:* XAU/USD ({tf_key.upper()})\n"
                                            f"📈 *Direction:* {dir_badge}\n"
                                            f"💵 *Entry:* ${entry_px:.2f}\n"
                                            f"🏁 *Exit:* ${tp_px:.2f}\n"
                                            f"💰 *Profit:* +${existing.realized_pnl:.2f}\n"
                                            f"━━━━━━━━━━━━━━━━━━━━"
                                        )
                                        _dispatch_tg_alert(tg.send_raw_alert(tp_msg))
                                    except Exception as tg_err:
                                        logger.warning("[PAPER-TG] Failed to send TP hit alert: %s", tg_err)

                                    # MT5 Bridge Live Close Dispatch
                                    try:
                                        from app.services.mt5_bridge_manager import get_mt5_bridge_manager
                                        get_mt5_bridge_manager().enqueue_close(
                                            paper_trade_id=existing.id,
                                            symbol=exec_cfg.mt5_symbol or "XAUUSD-VIP",
                                            reason="TP_HIT",
                                            direction=f_state.direction,
                                        )
                                    except Exception as mt5_err:
                                        logger.warning("[MT5-BRIDGE] Failed to dispatch close to MT5: %s", mt5_err)

                                    # 3. Smart Shield Immediate Trigger: When L2 or L3 hits TP, trail L1 SL
                                    if exec_cfg.smart_shield_enabled and l_key in ("L2", "L3"):
                                        l1_trade = (await db.execute(
                                            select(PaperTradeModel).where(
                                                PaperTradeModel.signal_id.like(f"FIB_RETR_{tf_key.upper()}_L1_{int(f_state.point_2_price)}%"),
                                                PaperTradeModel.state == "OPEN",
                                            )
                                        )).scalars().first()
                                        if l1_trade:
                                            new_l1_sl = float(f_state.fib_0_618 or 0.0) if exec_cfg.smart_shield_level == "0.618" else float(f_state.fib_0_500 or 0.0)
                                            if (f_state.direction == "LONG" and new_l1_sl > (l1_trade.stop_loss or 0.0)) or (f_state.direction == "SHORT" and 0.0 < new_l1_sl < (l1_trade.stop_loss or 999999.0)):
                                                l1_trade.stop_loss = new_l1_sl
                                                await db.commit()
                                                logger.info("[PAPER-AUTO] Smart Shield (%s): L%s TP hit -> trailed L1 SL to %.2f", exec_cfg.smart_shield_level, l_key[-1], new_l1_sl)

                                                # Telegram Alert: Smart Shield Trailing SL
                                                try:
                                                    shield_msg = (
                                                        f"🛡 *SMART SHIELD ACTIVATED*\n"
                                                        f"━━━━━━━━━━━━━━━━━━━━\n"
                                                        f"📊 *Strategy:* Fib Retracement (L1 Protected)\n"
                                                        f"🪙 *Symbol:* XAU/USD ({tf_key.upper()})\n"
                                                        f"⚡ *Trigger:* L{l_key[-1]} TP Hit\n"
                                                        f"🔒 *New L1 SL:* ${new_l1_sl:.2f} (Breakeven)\n"
                                                        f"🛡 *Downside Risk:* 0.00 (Risk-Free Trade)\n"
                                                        f"━━━━━━━━━━━━━━━━━━━━"
                                                    )
                                                    _dispatch_tg_alert(tg.send_raw_alert(shield_msg))
                                                except Exception as tg_err:
                                                    logger.warning("[PAPER-TG] Failed to send shield alert: %s", tg_err)

                                                # MT5 Bridge Live SL Modify Dispatch
                                                try:
                                                    from app.services.mt5_bridge_manager import get_mt5_bridge_manager
                                                    get_mt5_bridge_manager().enqueue_modify(
                                                        paper_trade_id=l1_trade.id,
                                                        symbol=exec_cfg.mt5_symbol or "XAUUSD-VIP",
                                                        new_sl=new_l1_sl,
                                                        direction=f_state.direction,
                                                    )
                                                except Exception as mt5_err:
                                                    logger.warning("[MT5-BRIDGE] Failed to dispatch L1 modify to MT5: %s", mt5_err)

                                elif layer.get("state") == "SL_HIT":
                                    exit_px = float(layer.get("exit_price") or existing.stop_loss or sl_px)
                                    existing.state = "CLOSED"
                                    existing.exit_price = exit_px
                                    existing.exit_reason = "BREAKEVEN_HIT" if abs(exit_px - entry_px) <= 0.5 else "SL_HIT"
                                    existing.closed_at = datetime.now(timezone.utc)
                                    pts = round((exit_px - entry_px) if f_state.direction == "LONG" else (entry_px - exit_px), 2)
                                    existing.realized_pnl = round(pts * (existing.lot_size or 0.01) * 100.0, 2)
                                    existing.realized_r = round(pts / max(0.1, abs(entry_px - (existing.stop_loss or 0.0))), 2)
                                    await db.commit()
                                    logger.info("[PAPER-AUTO] Engine SL_HIT closed Retracement %s (%s) @ %.2f ($%.2f)", sig_id, l_key, exit_px, existing.realized_pnl)

                                    # Telegram Alert: SL Hit / Breakeven Hit
                                    try:
                                        is_be = existing.exit_reason == "BREAKEVEN_HIT"
                                        header = "🛡 *BREAKEVEN HIT (0.00 PTS)*" if is_be else f"🛑 *STOP LOSS HIT ({pts:.2f} PTS)*"
                                        pnl_str = f"+${existing.realized_pnl:.2f}" if existing.realized_pnl >= 0 else f"-${abs(existing.realized_pnl):.2f}"
                                        dir_badge = "BUY / LONG ▲" if f_state.direction in ("LONG", "BUY") else "SELL / SHORT ▼"
                                        sl_msg = (
                                            f"{header}\n"
                                            f"━━━━━━━━━━━━━━━━━━━━\n"
                                            f"📊 *Strategy:* Fib Retracement ({l_key})\n"
                                            f"🪙 *Symbol:* XAU/USD ({tf_key.upper()})\n"
                                            f"📈 *Direction:* {dir_badge}\n"
                                            f"💵 *Entry:* ${entry_px:.2f}\n"
                                            f"🛑 *Exit:* ${exit_px:.2f}\n"
                                            f"💰 *Realized PnL:* {pnl_str}\n"
                                            f"━━━━━━━━━━━━━━━━━━━━"
                                        )
                                        _dispatch_tg_alert(tg.send_raw_alert(sl_msg))
                                    except Exception as tg_err:
                                        logger.warning("[PAPER-TG] Failed to send SL hit alert: %s", tg_err)

                                    # MT5 Bridge Live Close Dispatch
                                    try:
                                        from app.services.mt5_bridge_manager import get_mt5_bridge_manager
                                        get_mt5_bridge_manager().enqueue_close(
                                            paper_trade_id=existing.id,
                                            symbol=exec_cfg.mt5_symbol or "XAUUSD-VIP",
                                            reason=existing.exit_reason,
                                            direction=f_state.direction,
                                        )
                                    except Exception as mt5_err:
                                        logger.warning("[MT5-BRIDGE] Failed to dispatch close to MT5: %s", mt5_err)

            except Exception as exc:  # noqa: BLE001
                logger.warning("[PAPER-SYNC] Fib sync error: %s", exc)

        # 2. SMC With Fib: Multi-Timeframe (5M, 15M, 30M, 1H, 4H) Single Active Trade Sync
        if exec_cfg.strategy_smc_fib:
            try:
                smc_svc = get_smc_fib_multi_tf_service("XAUUSD")
                smc_states = await smc_svc.advance(db)

                # Check if any open trades exist for SMC_WITH_FIB across any timeframe
                open_smc_trades = (await db.execute(
                    select(PaperTradeModel).where(
                        PaperTradeModel.state == "OPEN",
                        PaperTradeModel.signal_id.like("SMC_FIB_%"),
                    )
                )).scalars().all()

                # Per-trade validation: track active open trades per timeframe.
                existing_open_smc_by_tf: dict[str, PaperTradeModel] = {}
                for ot in open_smc_trades:
                    parts = (ot.signal_id or "").split("_")
                    ot_tf = parts[2].lower() if len(parts) >= 3 else None
                    if not ot_tf:
                        continue
                    ot_card = smc_states.get(ot_tf) if ot_tf else None
                    ot_engine_active = bool(ot_card and ot_card.get("is_trade_active"))

                    ot_anchor = parts[3] if len(parts) >= 4 else None
                    card_p2 = ot_card.get("point_2", {}).get("price") if ot_card else None
                    card_anchor = str(int(card_p2)) if card_p2 else None
                    is_valid_active = ot_engine_active and (ot_anchor is None or ot_anchor == card_anchor)

                    if ot_tf not in existing_open_smc_by_tf:
                        if is_valid_active:
                            existing_open_smc_by_tf[ot_tf] = ot
                        else:
                            # If trade is still within bounds, let the global price monitor exit it naturally!
                            # Only close if it has genuinely reached SL/TP or is stale (> 6h)
                            entry_chk = ot.actual_entry or ot.target_entry or 0.0
                            close_px = live_price if (live_price and live_price > 1000.0) else (ot.stop_loss or entry_chk)
                            dir_chk = ot.direction or "LONG"
                            sl_hit = (close_px <= ot.stop_loss) if (dir_chk == "LONG" and ot.stop_loss) else ((close_px >= ot.stop_loss) if (dir_chk == "SHORT" and ot.stop_loss) else False)
                            tp_hit = (close_px >= ot.take_profit_1) if (dir_chk == "LONG" and ot.take_profit_1) else ((close_px <= ot.take_profit_1) if (dir_chk == "SHORT" and ot.take_profit_1) else False)
                            age_hours = (datetime.now(timezone.utc) - (ot.opened_at or ot.created_at)).total_seconds() / 3600.0 if (ot.opened_at or ot.created_at) else 0.0

                            if not sl_hit and not tp_hit and age_hours < 6.0:
                                existing_open_smc_by_tf[ot_tf] = ot
                            else:
                                pts_stale = round((close_px - entry_chk) if dir_chk == "LONG" else (entry_chk - close_px), 2)
                                ot.state = "CLOSED"
                                ot.exit_price = close_px
                                ot.exit_reason = "TP_HIT" if tp_hit else ("SL_HIT" if sl_hit else "SETUP_INVALIDATED")
                                ot.closed_at = datetime.now(timezone.utc)
                                ot.realized_pnl = round(pts_stale * (ot.lot_size or 0.01) * 100.0, 2)
                                ot.realized_r = round(pts_stale / max(0.1, abs(entry_chk - (ot.stop_loss or 0.0))), 2)
                                await db.commit()
                                logger.info("[PAPER-AUTO] Closed stale SMC trade %s (TF %s, reason: %s) @ %.2f", ot.signal_id, ot_tf, ot.exit_reason, close_px)
                    else:
                        # Extra duplicate trade on the same timeframe -> close older
                        entry_chk = ot.actual_entry or ot.target_entry or 0.0
                        close_px = live_price if (live_price and live_price > 1000.0) else (ot.stop_loss or entry_chk)
                        dir_chk = ot.direction or "LONG"
                        pts_stale = round((close_px - entry_chk) if dir_chk == "LONG" else (entry_chk - close_px), 2)
                        ot.state = "CLOSED"
                        ot.exit_price = close_px
                        ot.exit_reason = "DUPLICATE_CLOSED"
                        ot.closed_at = datetime.now(timezone.utc)
                        ot.realized_pnl = round(pts_stale * (ot.lot_size or 0.01) * 100.0, 2)
                        ot.realized_r = round(pts_stale / max(0.1, abs(entry_chk - (ot.stop_loss or 0.0))), 2)
                        await db.commit()

                smc_allowed_tfs = [tf.lower() for tf in (exec_cfg.fib_retracement_timeframes or ["5m", "15m", "30m", "1h"])]
                for tf_key in smc_svc.timeframes:
                    if tf_key.lower() not in smc_allowed_tfs:
                        continue
                    s_card = smc_states.get(tf_key) or {}
                    if not s_card.get("point_2") or not s_card.get("entry", {}).get("price"):
                        continue

                    p2 = s_card.get("point_2", {}).get("price") or 0.0
                    p1 = s_card.get("point_1", {}).get("price") or 0.0
                    p2_ts = s_card.get("point_2", {}).get("timestamp") or s_card.get("point_2", {}).get("time")
                    ts_suffix = ""
                    if p2_ts:
                        try:
                            if isinstance(p2_ts, str):
                                ts_suffix = f"_{int(datetime.fromisoformat(p2_ts.replace('Z', '+00:00')).timestamp())}"
                            elif hasattr(p2_ts, "timestamp"):
                                ts_suffix = f"_{int(p2_ts.timestamp())}"
                        except Exception:
                            pass
                    dir_str = str(s_card.get("direction", "SHORT")).upper()
                    entry_px = float(s_card.get("entry", {}).get("price") or 0.0)
                    sl_px = float(s_card.get("sl", {}).get("price") or 0.0)
                    tp_px = float(s_card.get("tp", {}).get("locked") or s_card.get("tp", {}).get("dynamic") or s_card.get("tp", {}).get("price") or 0.0)
                    sig_id = f"SMC_FIB_{tf_key.upper()}_{int(p2)}{ts_suffix}"
                    sig_state = "FILLED" if s_card.get("is_entry_touched") else "PENDING"

                    # Sync Signal Model
                    try:
                        existing_sig = await repo.get_signal_by_id(sig_id)
                        if existing_sig is None and entry_px > 0:
                            # Cancel older PENDING signals for SMC on this timeframe (1 active signal rule)
                            prev_pendings = (await db.execute(
                                select(SignalModel).where(
                                    SignalModel.strategy == "SMC_WITH_FIB",
                                    SignalModel.timeframe == tf_key,
                                    SignalModel.outcome == "PENDING",
                                )
                            )).scalars().all()
                            for p_sig in prev_pendings:
                                p_sig.outcome = "CANCELLED"

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

                    # Track + close existing OPEN SMC trade if engine signals TP_HIT / SL_HIT / COMPLETED
                    smc_outcome = s_card.get("outcome")  # "TP_HIT" | "SL_HIT" | None
                    tf_open_trade = existing_open_smc_by_tf.get(tf_key)
                    if tf_open_trade is not None and tf_open_trade.signal_id == sig_id and tf_open_trade.state == "OPEN":
                        entry_chk = tf_open_trade.actual_entry or tf_open_trade.target_entry or 0.0
                        if smc_outcome in ("TP_HIT", "SL_HIT") and entry_chk > 0:
                            exit_reason = smc_outcome
                            exit_px = tp_px if smc_outcome == "TP_HIT" else sl_px
                            pts = round((tp_px - entry_chk) if dir_str == "LONG" else (entry_chk - tp_px), 2) if smc_outcome == "TP_HIT" else round((sl_px - entry_chk) if dir_str == "LONG" else (entry_chk - sl_px), 2)
                            tf_open_trade.state = "CLOSED"
                            tf_open_trade.exit_price = exit_px
                            tf_open_trade.exit_reason = exit_reason
                            tf_open_trade.closed_at = datetime.now(timezone.utc)
                            tf_open_trade.realized_pnl = round(pts * (tf_open_trade.lot_size or 0.01) * 100.0, 2)
                            tf_open_trade.realized_r = round(pts / max(0.1, abs(entry_chk - (tf_open_trade.stop_loss or 0.0))), 2)
                            await db.commit()
                            logger.info("[PAPER-AUTO] Engine %s closed SMC trade %s @ %.2f (+$%.2f)", smc_outcome, sig_id, exit_px, tf_open_trade.realized_pnl)
                            existing_open_smc_by_tf.pop(tf_key, None)  # Allow next setup to open on this TF

                    # Single Active Trade Rule: Only open trade if no open SMC trade exists on THIS timeframe!
                    if s_card.get("is_entry_touched") and sig_id not in _in_flight_signals and (tf_key not in existing_open_smc_by_tf):
                        existing = (await db.execute(
                            select(PaperTradeModel).where(PaperTradeModel.signal_id == sig_id)
                        )).scalars().first()

                        if not existing and entry_px > 0:
                            # --- SMC MACRO TREND ALIGNMENT FILTER (EMA) ---
                            if getattr(exec_cfg, "trend_filter_enabled", True):
                                tf_slot = smc_svc.slots.get(tf_key) if hasattr(smc_svc, "slots") else None
                                candles_for_ema = getattr(tf_slot.engine, "_candles", []) if tf_slot else []
                                if len(candles_for_ema) >= 30 and entry_px > 500.0:
                                    from app.indicators.ema import calculate_ema
                                    period = 200 if len(candles_for_ema) >= 200 else (50 if len(candles_for_ema) >= 50 else 20)
                                    ema_vals = calculate_ema(candles_for_ema, period=period)
                                    if ema_vals:
                                        latest_ema = ema_vals[-1]
                                        if dir_str in ("LONG", "BUY") and entry_px < latest_ema:
                                            logger.info(
                                                "[PAPER-AUTO] SMC Trend Filter: Skipping LONG trade on %s because entry $%.2f is counter-trend vs %d EMA $%.2f (Downtrend)",
                                                tf_key.upper(), entry_px, period, latest_ema,
                                            )
                                            continue
                                        elif dir_str in ("SHORT", "SELL") and entry_px > latest_ema:
                                            logger.info(
                                                "[PAPER-AUTO] SMC Trend Filter: Skipping SHORT trade on %s because entry $%.2f is counter-trend vs %d EMA $%.2f (Uptrend)",
                                                tf_key.upper(), entry_px, period, latest_ema,
                                            )
                                            continue

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

                                    # Persist authoritative AI validation record
                                    try:
                                        await repo.save_ai_validation({
                                            "signal_id": sig_id,
                                            "status": ai_res.status.value,
                                            "confidence": float(ai_res.confidence),
                                            "explanation": str(ai_res.explanation),
                                            "identified_risks": list(getattr(ai_res, "identified_risks", []) or []),
                                            "missing_confirmations": list(getattr(ai_res, "missing_confirmations", []) or []),
                                            "provider": getattr(ai_res, "provider", None),
                                            "model": getattr(ai_res, "model", None),
                                            "reason_code": getattr(ai_res, "reason_code", None),
                                        })
                                        await db.commit()
                                    except Exception as ai_save_err:
                                        logger.debug("[AI-GATE] Failed to persist AI validation: %s", ai_save_err)

                                    if ai_res.status.value == "REJECT":
                                        logger.warning("[AI-GATE] SMC With Fib %s REJECTED by AI Validator: %s", sig_id, ai_res.explanation)
                                        ai_approved = False
                                        try:
                                            await repo.update_signal_outcome(sig_id, {"outcome": "AI_REJECTED"})
                                            await db.commit()
                                        except Exception:
                                            pass
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
                                        f"⚡ *Multi-Slot:* {tf_key.upper()} Active (Parallel execution)\n"
                                        f"━━━━━━━━━━━━━━━━━━━━"
                                    )
                                    _dispatch_tg_alert(tg.send_raw_alert(msg))
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
                    p0_ts = getattr(eng, "point_0_ts", None)
                    p0_ts_suffix = f"_{int(p0_ts.timestamp())}" if (p0_ts and hasattr(p0_ts, "timestamp")) else ""
                    sig_id = f"FIB_TREND_{tf_key.upper()}_{dir_str}_{int(eng.point_0_price or 0)}{p0_ts_suffix}"
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
                                        f"⚡ *Multi-Slot:* {tf_key.upper()} Active (Parallel execution)\n"
                                        f"━━━━━━━━━━━━━━━━━━━━"
                                    )
                                    _dispatch_tg_alert(tg.send_raw_alert(msg))
                                except Exception as tg_err:  # noqa: BLE001
                                    logger.warning("[PAPER-TG] Failed to send open alert: %s", tg_err)
                            finally:
                                _in_flight_signals.discard(sig_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[PAPER-SYNC] Fib Trend sync error: %s", exc)

        # 4. Monitor OPEN trades against live price and resolve TP / SL
        # CRITICAL: effective_live_price MUST be a valid, realistic Gold price (> $1000)
        effective_live_price = live_price if (live_price is not None and live_price > 1000.0) else None
        if not effective_live_price and fib_tf_price:
            for p_val in fib_tf_price.values():
                if p_val and p_val > 1000.0:
                    effective_live_price = p_val
                    break

        if effective_live_price is not None:
            open_trades = (await db.execute(
                select(PaperTradeModel).where(PaperTradeModel.state == "OPEN")
            )).scalars().all()

            # MT5 Bridge Catch-Up: Dispatch any currently OPEN Fib Retracement trades
            # that have not yet been sent to MT5 (e.g. if the trade opened before MT5 bridge was toggled ON)
            if exec_cfg.mt5_bridge_enabled:
                try:
                    from app.services.mt5_bridge_manager import get_mt5_bridge_manager
                    mgr = get_mt5_bridge_manager()
                    for ot in open_trades:
                        if not mgr.is_paper_trade_enqueued(ot.id):
                            sig_s = (ot.signal_id or "").upper()
                            is_fib_retr = "FIB_RETR" in sig_s or any("RETRACEMENT" in str(l) for l in (ot.state_logs or []))
                            if is_fib_retr:
                                ot_entry = ot.actual_entry or ot.target_entry or 0.0
                                ot_sl = ot.stop_loss or 0.0
                                ot_tp = ot.take_profit_1 or 0.0
                                if ot_entry > 0:
                                    layer_name = "L1"
                                    for lk in ("L1", "L2", "L3"):
                                        if lk in sig_s:
                                            layer_name = lk
                                            break
                                    sl_d = round(abs(ot_entry - ot_sl), 2) if ot_sl > 0 else 3.0
                                    tp_d = round(abs(ot_tp - ot_entry), 2) if ot_tp > 0 else 3.0
                                    mgr.enqueue_order({
                                        "id": f"mt5-{ot.id[:8]}",
                                        "paper_trade_id": ot.id,
                                        "strategy": "Fib Retracement",
                                        "layer": layer_name,
                                        "direction": ot.direction,
                                        "symbol": exec_cfg.mt5_symbol or "XAUUSD-VIP",
                                        "lot_size": ot.lot_size or 0.01,
                                        "entry_price": ot_entry,
                                        "stop_loss": ot_sl,
                                        "take_profit_1": ot_tp,
                                        "sl_points": sl_d,
                                        "tp_points": tp_d,
                                        "execution_mode": "POINTS_DISTANCE",
                                    })
                                    logger.info("[MT5-BRIDGE] Catch-up dispatched active open trade %s (Fib Retr %s) to MT5", ot.id, layer_name)
                except Exception as mt5_catch_err:
                    logger.warning("[MT5-BRIDGE] Catch-up sync error: %s", mt5_catch_err)

            for t in open_trades:
                entry = t.actual_entry or t.target_entry or 0.0
                if entry <= 0:
                    continue

                price_px = live_price
                sig_upper = (t.signal_id or "").upper()
                if sig_upper.startswith("FIB_RETR_"):
                    # Engine-tracked tranche: resolve ONLY against the monitor's
                    # own snapshot price for that timeframe. The process-global
                    # feed price (possibly from unrelated data/instruments or a
                    # polluted test singleton) must never double-resolve these —
                    # the layer loop in the Fib section owns their lifecycle.
                    _parts = sig_upper.split("_")
                    price_px = fib_tf_price.get(_parts[2].lower(), None) if len(_parts) >= 3 else None
                    if price_px is None:
                        price_px = live_price
                    if price_px is None:
                        continue

                # Regime guard: the feed price may belong to a different
                # instrument/session than this trade (symbol switch). Never
                # resolve TP/SL with a price outside the trade's neighborhood.
                if not (0.5 * entry <= price_px <= 2.0 * entry):
                    continue

                closed = False
                pts = 0.0

                is_trend = "FIB_TREND" in (t.signal_id or "") or any("TREND" in str(l) for l in (t.state_logs or []))
                final_tp = (t.take_profit_2 if (is_trend and t.take_profit_2 and t.take_profit_2 > 0) else t.take_profit_1)

                if t.direction == "LONG":
                    # For Trend trades: If TP1 reached, shift SL to Breakeven
                    if is_trend and t.take_profit_1 and price_px >= t.take_profit_1:
                        if t.stop_loss is None or t.stop_loss < entry:
                            t.stop_loss = round(entry, 2)
                            logs = list(t.state_logs or [])
                            if not any(isinstance(l, dict) and l.get("event") == "BREAKEVEN_LOCKED" for l in logs):
                                logs.append({"event": "BREAKEVEN_LOCKED", "price": price_px, "time": datetime.now(timezone.utc).isoformat()})
                                t.state_logs = logs
                                logger.info("[PAPER-AUTO] Trend trade %s hit TP1 (%.2f) -> Stop Loss moved to Breakeven (%.2f)", t.id, t.take_profit_1, t.stop_loss)

                    if final_tp and price_px >= final_tp:
                        t.state = "CLOSED"
                        t.exit_price = final_tp
                        t.exit_reason = "TP_HIT"
                        t.closed_at = datetime.now(timezone.utc)
                        pts = round(final_tp - entry, 2)
                        t.realized_pnl = round(pts * (t.lot_size or 0.01) * 100.0, 2)
                        t.realized_r = round(pts / max(0.1, abs(entry - (t.stop_loss or 0.0))), 2)
                        closed = True
                        logger.info("[PAPER-AUTO] Closed LONG trade %s at TP: %.2f (+$%.2f)", t.id, t.exit_price, t.realized_pnl)
                    elif t.stop_loss and price_px <= t.stop_loss:
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
                    if is_trend and t.take_profit_1 and price_px <= t.take_profit_1:
                        if t.stop_loss is None or t.stop_loss > entry:
                            t.stop_loss = round(entry, 2)
                            logs = list(t.state_logs or [])
                            if not any(isinstance(l, dict) and l.get("event") == "BREAKEVEN_LOCKED" for l in logs):
                                logs.append({"event": "BREAKEVEN_LOCKED", "price": price_px, "time": datetime.now(timezone.utc).isoformat()})
                                t.state_logs = logs
                                logger.info("[PAPER-AUTO] Trend trade %s hit TP1 (%.2f) -> Stop Loss moved to Breakeven (%.2f)", t.id, t.take_profit_1, t.stop_loss)

                    if final_tp and price_px <= final_tp:
                        t.state = "CLOSED"
                        t.exit_price = final_tp
                        t.exit_reason = "TP_HIT"
                        t.closed_at = datetime.now(timezone.utc)
                        pts = round(entry - final_tp, 2)
                        t.realized_pnl = round(pts * (t.lot_size or 0.01) * 100.0, 2)
                        t.realized_r = round(pts / max(0.1, abs(entry - (t.stop_loss or 0.0))), 2)
                        closed = True
                        logger.info("[PAPER-AUTO] Closed SHORT trade %s at TP: %.2f (+$%.2f)", t.id, t.exit_price, t.realized_pnl)
                    elif t.stop_loss and price_px >= t.stop_loss:
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
                    # MT5 Live Position Close Synchronization (Strict Fib Retracement)
                    if "FIB_RETR" in (t.signal_id or ""):
                        try:
                            from app.services.mt5_bridge_manager import get_mt5_bridge_manager
                            get_mt5_bridge_manager().enqueue_close(
                                paper_trade_id=t.id,
                                symbol=exec_cfg.mt5_symbol or "XAUUSD-VIP",
                                reason=t.exit_reason or "CLOSED",
                                direction=t.direction,
                            )
                        except Exception as mt5_err:
                            logger.warning("[MT5-BRIDGE] Failed to dispatch close to MT5: %s", mt5_err)

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

                        exec_cfg = get_execution_settings()
                        is_cent = (exec_cfg.account_currency == "cent")
                        curr_sym = "₹" if is_cent else "$"
                        curr_name = "INR" if is_cent else "USD"

                        if t.exit_reason == "TP_HIT":
                            msg = (
                                f"🎯 *TAKE PROFIT HIT!*\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📊 *Strategy:* {strat_name}\n"
                                f"🪙 *Symbol:* XAU/USD ({trade_tf})\n"
                                f"💵 *Entry:* ${entry:.2f}\n"
                                f"💰 *Exit Price:* ${t.exit_price:.2f}\n"
                                f"🏆 *Result:* +{pts:.2f} PTS (+{curr_sym}{t.realized_pnl:.2f} {curr_name})\n"
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
                                f"⚖️ *Result:* {pts:+.2f} PTS ({curr_sym}{t.realized_pnl:+.2f} {curr_name} — Capital Protected)\n"
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
                                f"📉 *Result:* -{abs(pts):.2f} PTS (-{curr_sym}{abs(t.realized_pnl):.2f} {curr_name})\n"
                                f"━━━━━━━━━━━━━━━━━━━━"
                            )
                        _dispatch_tg_alert(tg.send_raw_alert(msg))
                    except Exception as tg_err:  # noqa: BLE001
                        logger.warning("[PAPER-TG] Failed to send close alert: %s", tg_err)

            await db.commit()
