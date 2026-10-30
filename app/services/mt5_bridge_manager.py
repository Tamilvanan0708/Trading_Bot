"""
MT5 Bridge Manager Service.
Handles pending order queuing, strategy filtering (strictly Fib Retracement),
heartbeat tracking, and trade execution reporting for the MQL5 EA Bridge.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config.execution_settings import get_execution_settings

logger = logging.getLogger("xau.mt5_bridge")


class MT5BridgeManager:
    _instance: MT5BridgeManager | None = None
    _lock = threading.Lock()

    def __new__(cls) -> MT5BridgeManager:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._lock = threading.Lock()
        self._orders: dict[str, dict[str, Any]] = {}
        self._pending_ids: list[str] = []
        self._paper_trade_to_ticket: dict[str, int] = {}
        self._ticket_to_paper_trade: dict[int, str] = {}
        self._heartbeat: dict[str, Any] = {
            "account_login": None,
            "server": None,
            "balance": None,
            "equity": None,
            "leverage": None,
            "symbol": "XAUUSD",
            "last_seen": 0.0,
            "client_ip": None,
        }
        self._execution_history: list[dict[str, Any]] = []
        self._initialized = True

    # ------------------------------------------------------------------
    # Heartbeat & Status
    # ------------------------------------------------------------------

    def record_heartbeat(self, data: dict[str, Any], client_ip: str | None = None) -> dict[str, Any]:
        """Record live heartbeat from MT5 EA."""
        with self._lock:
            self._heartbeat = {
                "account_login": data.get("account_login") or data.get("login"),
                "server": data.get("server"),
                "balance": float(data.get("balance", 0.0)) if data.get("balance") is not None else None,
                "equity": float(data.get("equity", 0.0)) if data.get("equity") is not None else None,
                "leverage": int(data.get("leverage", 500)) if data.get("leverage") is not None else 500,
                "symbol": data.get("symbol", "XAUUSD"),
                "last_seen": time.time(),
                "client_ip": client_ip or data.get("client_ip"),
            }
            return {
                "status": "ok",
                "server_time": time.time(),
                "pending_count": len(self._pending_ids),
            }

    # ------------------------------------------------------------------
    # Native MT5 Connection Lifecycle
    # ------------------------------------------------------------------

    def _ensure_native_mt5(self) -> Any:
        """Returns initialized MetaTrader5 package or None."""
        try:
            import MetaTrader5 as mt5
            t_info = mt5.terminal_info()
            if t_info is not None and getattr(t_info, "connected", False):
                return mt5
            # Terminal not yet initialized in this process - try initializing
            if mt5.initialize():
                logger.info("[MT5-NATIVE] Successfully initialized native MT5 connection to running terminal.")
                return mt5
        except Exception as e:
            logger.debug("[MT5-NATIVE] _ensure_native_mt5 skipped: %s", e)
        return None

    @property
    def is_online(self) -> bool:
        """True if EA reported heartbeat or native MT5 package is connected to terminal."""
        mt5 = self._ensure_native_mt5()
        if mt5 is not None:
            return True
        return (time.time() - self._heartbeat.get("last_seen", 0.0)) <= 15.0

    def is_connected(self) -> bool:
        """True if EA reported heartbeat or native MT5 package is connected to terminal."""
        return self.is_online

    def get_status(self) -> dict[str, Any]:
        """Returns comprehensive bridge status for frontend settings UI."""
        with self._lock:
            exec_cfg = get_execution_settings()
            online = self.is_online
            last_seen = self._heartbeat.get("last_seen", 0.0)
            sec_ago = round(time.time() - last_seen, 1) if last_seen > 0 else None

            # Attempt live account info directly from native Python MT5
            acct_data = None
            algo_trading_enabled = None
            mt5 = self._ensure_native_mt5()
            if mt5 is not None:
                try:
                    t_info = mt5.terminal_info()
                    if t_info:
                        algo_trading_enabled = getattr(t_info, "trade_allowed", True)
                    ai = mt5.account_info()
                    if ai is not None:
                        acct_data = {
                            "login": ai.login,
                            "server": ai.server,
                            "balance": round(float(ai.balance), 2),
                            "equity": round(float(ai.equity), 2),
                            "leverage": int(ai.leverage),
                            "symbol": exec_cfg.mt5_symbol or "XAUUSD-VIP",
                            "name": getattr(ai, "name", "VT Markets"),
                            "currency": getattr(ai, "currency", "USD"),
                        }
                except Exception:
                    pass

            if acct_data is None and online:
                acct_data = {
                    "login": self._heartbeat.get("account_login"),
                    "server": self._heartbeat.get("server"),
                    "balance": self._heartbeat.get("balance"),
                    "equity": self._heartbeat.get("equity"),
                    "leverage": self._heartbeat.get("leverage"),
                    "symbol": self._heartbeat.get("symbol"),
                }

            return {
                "bridge_enabled": exec_cfg.mt5_bridge_enabled,
                "is_online": online,
                "algo_trading_enabled": algo_trading_enabled,
                "seconds_since_heartbeat": sec_ago if last_seen > 0 else (0.1 if acct_data else None),
                "target_symbol": exec_cfg.mt5_symbol,
                "magic_number": exec_cfg.mt5_magic_number,
                "allowed_strategy": exec_cfg.mt5_allowed_strategy,
                "connected_account": acct_data,
                "pending_orders_count": len(self._pending_ids),
                "recent_executions": self._execution_history[-10:],
            }

    def is_paper_trade_enqueued(self, paper_trade_id: str | None) -> bool:
        """Check whether a paper trade has already been queued or dispatched to MT5."""
        if not paper_trade_id:
            return False
        with self._lock:
            for order in self._orders.values():
                if order.get("paper_trade_id") == str(paper_trade_id):
                    return True
            if str(paper_trade_id) in self._paper_trade_to_ticket:
                return True
        return False

    def get_open_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Fetch all currently open positions directly from MetaTrader 5."""
        mt5 = self._ensure_native_mt5()
        if mt5 is None:
            return []
        try:
            sym = str(symbol or get_execution_settings().mt5_symbol or "XAUUSD-VIP").strip()
            positions = mt5.positions_get(symbol=sym)
            if positions is None:
                positions = mt5.positions_get()
            if not positions:
                return []
            res = []
            for p in positions:
                res.append({
                    "ticket": int(p.ticket),
                    "symbol": p.symbol,
                    "type": "BUY" if p.type == 0 else "SELL",
                    "volume": float(p.volume),
                    "price_open": float(p.price_open),
                    "sl": float(p.sl),
                    "tp": float(p.tp),
                    "price_current": float(p.price_current),
                    "profit": float(p.profit),
                    "comment": getattr(p, "comment", ""),
                })
            return res
        except Exception as exc:
            logger.debug("[MT5-BRIDGE] get_open_positions error: %s", exc)
            return []

    def get_ticket_for_paper_trade(self, paper_trade_id: str | None, timeframe: str | None = None) -> int | None:
        """Lookup native or EA execution ticket for a given paper trade ID."""
        if not paper_trade_id:
            return None
        with self._lock:
            tkt = self._paper_trade_to_ticket.get(str(paper_trade_id))
            if tkt:
                return tkt
            for rec in reversed(self._execution_history):
                oid = str(rec.get("order_id", ""))
                if str(paper_trade_id)[:8] in oid and rec.get("ticket"):
                    t_val = int(rec["ticket"])
                    self._paper_trade_to_ticket[str(paper_trade_id)] = t_val
                    return t_val

        # Fallback 1: Query live positions directly from MT5
        mt5 = self._ensure_native_mt5()
        if mt5 is not None:
            try:
                positions = mt5.positions_get()
                if positions:
                    for pos in positions:
                        comm = getattr(pos, "comment", "")
                        # Match strictly by unique paper trade ID prefix
                        if str(paper_trade_id)[:6] in comm:
                            with self._lock:
                                self._paper_trade_to_ticket[str(paper_trade_id)] = int(pos.ticket)
                            return int(pos.ticket)
                # Fallback 2: Query recent history deals in case trade was already closed
                now_ts = time.time()
                deals = mt5.history_deals_get(now_ts - (7 * 86400), now_ts)
                if deals:
                    for d in reversed(deals):
                        comm = getattr(d, "comment", "")
                        pos_id = int(getattr(d, "position_id", 0))
                        if pos_id > 0 and str(paper_trade_id)[:6] in comm:
                            with self._lock:
                                self._paper_trade_to_ticket[str(paper_trade_id)] = pos_id
                            return pos_id
            except Exception:
                pass
        return None

    def get_live_spread(self, symbol: str | None = None) -> float:
        """Returns live broker spread in points (Ask - Bid) for the given symbol, or 0.15 fallback."""
        mt5 = self._ensure_native_mt5()
        if mt5 is not None:
            try:
                sym = str(symbol or get_execution_settings().mt5_symbol or "XAUUSD-VIP").strip()
                tick = mt5.symbol_info_tick(sym)
                if tick and tick.ask and tick.bid:
                    spr = round(abs(tick.ask - tick.bid), 2)
                    if 0.0 < spr < 5.0:
                        return spr
            except Exception:
                pass
        return 0.15

    def get_closed_deal_info(self, ticket: int) -> dict[str, Any] | None:
        """Query MT5 deal history to see if a position/ticket was closed, returning exit details."""
        if not ticket:
            return None
        mt5 = self._ensure_native_mt5()
        if mt5 is not None:
            try:
                deals = mt5.history_deals_get(position=ticket)
                if deals is None:
                    deals = mt5.history_deals_get(ticket=ticket)
                if deals:
                    close_deals = [d for d in deals if getattr(d, "entry", 0) in (1, 3)]
                    target_deal = close_deals[-1] if close_deals else (deals[-1] if len(deals) > 1 else None)
                    if target_deal:
                        tot_profit = sum(float(getattr(d, "profit", 0.0)) for d in deals)
                        return {
                            "ticket": ticket,
                            "close_price": float(target_deal.price),
                            "close_time": datetime.fromtimestamp(target_deal.time, tz=timezone.utc),
                            "profit": round(tot_profit, 2),
                            "comment": getattr(target_deal, "comment", ""),
                        }
            except Exception as e:
                logger.debug("[MT5-BRIDGE] get_closed_deal_info error for ticket #%s: %s", ticket, e)
        return None

    def get_account_balance(self) -> float | None:
        """Returns live account balance in USD from MT5 if connected."""
        mt5 = self._ensure_native_mt5()
        if mt5 is not None:
            try:
                ai = mt5.account_info()
                if ai is not None and getattr(ai, "balance", None) is not None:
                    return round(float(ai.balance), 2)
            except Exception:
                pass
        with self._lock:
            return self._heartbeat.get("balance")

    def get_account_equity(self) -> float | None:
        """Returns live account equity in USD from MT5 if connected."""
        mt5 = self._ensure_native_mt5()
        if mt5 is not None:
            try:
                ai = mt5.account_info()
                if ai is not None and getattr(ai, "equity", None) is not None:
                    return round(float(ai.equity), 2)
            except Exception:
                pass
        with self._lock:
            return self._heartbeat.get("equity")
        with self._lock:
            return self._heartbeat.get("balance")

    def enqueue_order(self, order_data: dict[str, Any]) -> dict[str, Any]:
        """
        Enqueues an order for MT5 execution.
        Supports:
        - Fib Retracement
        - SMC with Fib
        - Breakout Strategy
        - Lots are safety-clamped to max_lot_cap.
        """
        strat = str(order_data.get("strategy", "Fib Retracement")).strip()
        strat_upper = strat.upper()

        exec_cfg = get_execution_settings()

        allowed_strat = getattr(exec_cfg, "mt5_allowed_strategy", "ALL")
        if allowed_strat and allowed_strat != "ALL" and allowed_strat.upper() not in strat_upper:
            logger.info(
                "[MT5-BRIDGE] Strategy '%s' filtered out by configuration (%s).",
                strat,
                allowed_strat,
            )
            return {
                "status": "filtered",
                "reason": f"Strategy '{strat}' is disabled for MT5 live execution ({allowed_strat} only).",
            }

        # Check individual strategy master toggle
        if ("FIB RETRACEMENT" in strat_upper or "RETR" in strat_upper) and not getattr(exec_cfg, "strategy_fib_retracement", True):
            return {"status": "disabled", "reason": "Fib Retracement strategy is toggled OFF."}
        if ("SMC" in strat_upper) and not getattr(exec_cfg, "strategy_smc_fib", True):
            return {"status": "disabled", "reason": "SMC With Fib strategy is toggled OFF."}
        if ("BREAKOUT" in strat_upper) and not getattr(exec_cfg, "strategy_breakout", True):
            return {"status": "disabled", "reason": "Breakout Strategy is toggled OFF."}
        if ("TREND" in strat_upper) and not getattr(exec_cfg, "strategy_fib_trend", False):
            return {"status": "disabled", "reason": "Fib Go With Trend strategy is toggled OFF."}
        if not exec_cfg.mt5_bridge_enabled and not order_data.get("is_test", False):
            logger.info("[MT5-BRIDGE] MT5 bridge is disabled in Execution Settings; skipping order.")
            return {
                "status": "disabled",
                "reason": "MT5 bridge is disabled in settings.",
            }

        order_id = str(order_data.get("id") or f"mt5-{uuid.uuid4().hex[:8]}")
        raw_lot = float(order_data.get("lot_size") or order_data.get("lots") or 0.01)
        max_cap = float(getattr(exec_cfg, "max_lot_cap", 5.0) or 5.0)
        clamped_lot = max(0.01, min(max_cap, round(raw_lot, 2)))

        entry_px = float(order_data.get("entry_price") or order_data.get("target_entry") or 0.0)
        sl_px = float(order_data.get("stop_loss") or 0.0)
        tp_px = float(order_data.get("take_profit_1") or order_data.get("take_profit") or 0.0)
        sl_pts = round(abs(entry_px - sl_px), 2) if (entry_px > 0 and sl_px > 0) else float(order_data.get("sl_points") or order_data.get("sl_pts") or 0.0)
        tp_pts = round(abs(tp_px - entry_px), 2) if (entry_px > 0 and tp_px > 0) else float(order_data.get("tp_points") or order_data.get("tp_pts") or 0.0)

        order_payload = {
            "id": order_id,
            "symbol": str(order_data.get("symbol") or exec_cfg.mt5_symbol or "XAUUSD-VIP").upper(),
            "action": str(order_data.get("direction") or order_data.get("action") or "BUY").upper(),
            "lot_size": clamped_lot,
            "entry_price": entry_px,
            "stop_loss": sl_px,
            "take_profit": tp_px,
            "sl_points": sl_pts,
            "tp_points": tp_pts,
            "strategy": strat,
            "layer": str(order_data.get("layer", "L1")),
            "timeframe": str(order_data.get("timeframe", "5M")).upper(),
            "comment": str(
                order_data.get("comment")
                or (
                    f"XAU_{order_data.get('timeframe', '5M')}_{order_data.get('layer', 'L1')}_{str(order_data.get('paper_trade_id'))[:6]}"
                    if order_data.get("paper_trade_id")
                    else f"XAU_{order_data.get('timeframe', '5M')}_{order_data.get('layer', 'L1')}"
                )
            ).strip()[:31],
            "created_at": time.time(),
            "status": "PENDING",
            "paper_trade_id": order_data.get("paper_trade_id"),
            "spread_filter_enabled": getattr(exec_cfg, "spread_filter_enabled", True),
            "max_spread_points": float(getattr(exec_cfg, "max_spread_points", 2.0)),
        }

        with self._lock:
            self._orders[order_id] = order_payload
            self._pending_ids.append(order_id)

        # Attempt direct execution via native Python MetaTrader5 on Windows
        native_res = self._execute_native_mt5_order(order_payload)
        if native_res:
            if native_res.get("status") == "BLOCKED_HIGH_SPREAD":
                with self._lock:
                    if order_id in self._pending_ids:
                        self._pending_ids.remove(order_id)
                    order_payload["status"] = "BLOCKED_SPREAD"
                    order_payload["spread"] = native_res.get("spread")
                return {
                    "status": "blocked_spread",
                    "order_id": order_id,
                    "spread": native_res.get("spread"),
                    "max_spread": native_res.get("max_spread"),
                    "reason": f"Live spread {native_res.get('spread')} pts exceeds max allowed {native_res.get('max_spread')} pts.",
                }

            if native_res.get("status") == "BLOCKED_CHASE":
                with self._lock:
                    if order_id in self._pending_ids:
                        self._pending_ids.remove(order_id)
                    order_payload["status"] = "BLOCKED_CHASE"
                    order_payload["gap"] = native_res.get("gap")
                return {
                    "status": "blocked_chase",
                    "order_id": order_id,
                    "gap": native_res.get("gap"),
                    "max_chase": native_res.get("max_chase"),
                    "reason": f"Live price drifted {native_res.get('gap')} pts past entry target {native_res.get('entry_target')}. Blocked chasing market.",
                }

            if native_res.get("status") == "REJECTED_BY_BROKER":
                with self._lock:
                    if order_id in self._pending_ids:
                        self._pending_ids.remove(order_id)
                    order_payload["status"] = "REJECTED"
                    order_payload["error"] = native_res.get("error")
                return {
                    "status": "rejected",
                    "order_id": order_id,
                    "retcode": native_res.get("retcode"),
                    "error": native_res.get("error"),
                    "reason": f"MT5 / Broker rejected order: {native_res.get('error')} (retcode {native_res.get('retcode')}).",
                }

            with self._lock:
                if order_id in self._pending_ids:
                    self._pending_ids.remove(order_id)
                order_payload["status"] = "FILLED"
                order_payload["ticket"] = native_res["ticket"]
                pt_id = order_payload.get("paper_trade_id")
                if pt_id:
                    self._paper_trade_to_ticket[str(pt_id)] = native_res["ticket"]
            logger.info(
                "[MT5-NATIVE] Executed Fib Retracement order %s directly! Ticket #%d @ %.2f",
                order_id,
                native_res["ticket"],
                native_res["price"],
            )
            return {
                "status": "filled_native",
                "order_id": order_id,
                "ticket": native_res["ticket"],
                "price": native_res["price"],
                "payload": order_payload,
            }

        logger.info(
            "[MT5-BRIDGE] Enqueued Fib Retracement order %s: %s %.2f Lots @ SL=%.2f TP=%.2f",
            order_id,
            order_payload["action"],
            clamped_lot,
            order_payload["stop_loss"],
            order_payload["take_profit"],
        )

        return {
            "status": "queued",
            "order_id": order_id,
            "payload": order_payload,
        }

    def _execute_native_mt5_order(self, order_payload: dict[str, Any]) -> dict[str, Any] | None:
        """Executes an order directly via MetaTrader5 Python package without requiring an external EA."""
        mt5 = self._ensure_native_mt5()
        if mt5 is None:
            return None
        try:
            t_info = mt5.terminal_info()
            if not t_info or not getattr(t_info, "connected", False):
                return None

            symbol = str(order_payload.get("symbol") or "XAUUSD-VIP").strip()
            mt5.symbol_select(symbol, True)
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                logger.warning("[MT5-NATIVE] Failed to get tick for %s", symbol)
                return None

            action = str(order_payload.get("action", "BUY")).upper()
            is_buy = action in ("BUY", "LONG")
            order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
            price = tick.ask if is_buy else tick.bid

            # Direction 4: Live Spread Filter Check (with 5M scalp spread gate)
            live_spread = round(abs(tick.ask - tick.bid), 2)
            exec_cfg = get_execution_settings()
            tf = str(order_payload.get("timeframe", "5M")).upper()
            if getattr(exec_cfg, "spread_filter_enabled", True) and not order_payload.get("is_test", False):
                if tf == "5M":
                    max_spread = float(getattr(exec_cfg, "max_spread_points_5m", 0.50))
                else:
                    max_spread = float(getattr(exec_cfg, "max_spread_points", 2.0))

                if live_spread > max_spread:
                    logger.warning(
                        "[MT5-NATIVE] SPREAD FILTER TRIGGERED (%s): Live spread %.2f pts exceeds maximum allowed %.2f pts for %s. Order %s BLOCKED.",
                        tf,
                        live_spread,
                        max_spread,
                        symbol,
                        order_payload.get("id"),
                    )
                    return {
                        "ticket": None,
                        "price": 0.0,
                        "status": "BLOCKED_HIGH_SPREAD",
                        "spread": live_spread,
                        "max_spread": max_spread,
                    }

            # Idea 1: Entry Chase Guard (Prevent late market orders when price has already drifted far from Fib level)
            entry_target = float(order_payload.get("entry_price") or 0.0)
            if getattr(exec_cfg, "chase_filter_enabled", True) and entry_target > 0 and not order_payload.get("is_test", False):
                max_chase = float(getattr(exec_cfg, "max_chase_points", 2.0))
                gap = round(abs(price - entry_target), 2)
                is_chasing = False
                if is_buy and price > (entry_target + max_chase):
                    is_chasing = True
                elif not is_buy and price < (entry_target - max_chase):
                    is_chasing = True

                if is_chasing:
                    logger.warning(
                        "[MT5-NATIVE] CHASE GUARD TRIGGERED: Live price %.2f drifted %.2f pts past entry target %.2f (max allowed: %.2f pts) for %s. Order %s BLOCKED.",
                        price,
                        gap,
                        entry_target,
                        max_chase,
                        symbol,
                        order_payload.get("id"),
                    )
                    return {
                        "ticket": None,
                        "price": 0.0,
                        "status": "BLOCKED_CHASE",
                        "gap": gap,
                        "max_chase": max_chase,
                        "entry_target": entry_target,
                    }

            lot = float(order_payload.get("lot_size") or 0.01)
            sl = float(order_payload.get("stop_loss") or 0.0)
            tp = float(order_payload.get("take_profit") or 0.0)
            sl_pts = float(order_payload.get("sl_points") or 0.0)
            tp_pts = float(order_payload.get("tp_points") or 0.0)

            # If relative points provided, calculate valid broker SL & TP from live market price
            if sl <= 0.0 and sl_pts > 0.0:
                sl = round(price - sl_pts, 2) if is_buy else round(price + sl_pts, 2)
            if tp <= 0.0 and tp_pts > 0.0:
                tp = round(price + tp_pts, 2) if is_buy else round(price - tp_pts, 2)

            # Apply Spread Buffer to Stop Loss to prevent broker spread wick spikes
            spread_buffer = float(getattr(exec_cfg, "spread_buffer_points", 0.35) or 0.0)
            if sl > 0 and spread_buffer > 0:
                if is_buy:
                    sl = round(sl - spread_buffer, 2)
                else:
                    sl = round(sl + spread_buffer, 2)

            # Determine supported filling mode and symbol parameters dynamically
            sym_info = mt5.symbol_info(symbol)
            if sym_info is None:
                logger.warning("[MT5-NATIVE] Failed to get symbol_info for %s", symbol)
                return None

            # 1. Market Closed / Session Guard (Prevents Retcode 10018: Market closed)
            if getattr(sym_info, "trade_mode", 4) == 0:
                logger.warning(
                    "[MT5-NATIVE] Trading disabled on symbol %s (trade_mode=0). Order %s BLOCKED.",
                    symbol,
                    order_payload.get("id"),
                )
                return {
                    "ticket": None,
                    "price": 0.0,
                    "status": "REJECTED_BY_BROKER",
                    "retcode": getattr(mt5, "TRADE_RETCODE_MARKET_CLOSED", 10018),
                    "error": "Market closed (Broker session inactive)",
                }

            # 2. Daily Broker Rollover Break Guard (21:45 to 22:15 UTC / 02:45 to 03:15 AM IST)
            utc_now = datetime.now(timezone.utc)
            if not order_payload.get("is_test", False) and ((utc_now.hour == 21 and utc_now.minute >= 45) or (utc_now.hour == 22 and utc_now.minute <= 15)):
                logger.warning(
                    "[MT5-NATIVE] Broker daily rollover maintenance window active (21:45-22:15 UTC). Order %s BLOCKED.",
                    order_payload.get("id"),
                )
                return {
                    "ticket": None,
                    "price": 0.0,
                    "status": "REJECTED_BY_BROKER",
                    "retcode": getattr(mt5, "TRADE_RETCODE_MARKET_CLOSED", 10018),
                    "error": "Market closed (Daily broker rollover maintenance)",
                }

            # 3. Broker Stops Level & Bid/Ask Distance Auto-Clamp (Prevents Retcode 10016: Invalid stops)
            point = float(getattr(sym_info, "point", 0.01) or 0.01)
            stops_level_pts = float(getattr(sym_info, "trade_stops_level", 0) or 0) * point
            min_stop_distance = max(stops_level_pts, live_spread, 1.50)

            if sl > 0:
                if is_buy:
                    if sl >= (tick.bid - min_stop_distance):
                        sl = round(tick.bid - min_stop_distance, 2)
                else:
                    if sl <= (tick.ask + min_stop_distance):
                        sl = round(tick.ask + min_stop_distance, 2)

            if tp > 0:
                if is_buy:
                    if tp <= (tick.ask + min_stop_distance):
                        tp = round(tick.ask + min_stop_distance, 2)
                else:
                    if tp >= (tick.bid - min_stop_distance):
                        tp = round(tick.bid - min_stop_distance, 2)

            filling = mt5.ORDER_FILLING_IOC
            if hasattr(sym_info, "filling_mode"):
                fm = sym_info.filling_mode
                if fm & 2:
                    filling = mt5.ORDER_FILLING_IOC
                elif fm & 1:
                    filling = mt5.ORDER_FILLING_FOK
                else:
                    filling = mt5.ORDER_FILLING_RETURN

            order_comment = str(order_payload.get("comment", "Fib Retracement"))[:31]
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot,
                "type": order_type,
                "price": price,
                "sl": sl if sl > 0 else 0.0,
                "tp": tp if tp > 0 else 0.0,
                "deviation": 20,
                "magic": int(order_payload.get("magic_number", 123456)),
                "comment": order_comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }

            # Guard: Prevent duplicate order if position with matching comment or paper_trade_id already exists in MT5
            try:
                open_positions = mt5.positions_get(symbol=symbol)
                if open_positions:
                    for pos in open_positions:
                        pos_comm = getattr(pos, "comment", "")
                        if order_comment and pos_comm == order_comment:
                            logger.warning(
                                "[MT5-NATIVE] Position already active in MT5 for comment '%s' (Ticket #%d). Skipping duplicate send.",
                                order_comment,
                                pos.ticket,
                            )
                            self.record_execution_report({
                                "order_id": order_payload["id"],
                                "ticket": pos.ticket,
                                "status": "FILLED",
                                "fill_price": pos.price_open,
                                "retcode": mt5.TRADE_RETCODE_DONE,
                            })
                            return {"ticket": pos.ticket, "price": pos.price_open, "status": "FILLED"}
            except Exception as pos_chk_err:
                logger.debug("[MT5-NATIVE] Duplicate position check error: %s", pos_chk_err)

            res = mt5.order_send(request)
            # If rejected due to filling mode (10030), retry with alternate filling modes
            if res is not None and res.retcode == getattr(mt5, "TRADE_RETCODE_INVALID_FILL", 10030):
                for alt_filling in (mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_IOC):
                    if alt_filling != filling:
                        request["type_filling"] = alt_filling
                        res = mt5.order_send(request)
                        if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
                            break

            if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
                ticket = int(res.order)
                logger.info("[MT5-NATIVE] OrderSend() executed successfully! Ticket #%d @ %.2f", ticket, res.price)
                self.record_execution_report({
                    "order_id": order_payload["id"],
                    "ticket": ticket,
                    "status": "FILLED",
                    "fill_price": res.price,
                    "retcode": res.retcode,
                })
                return {"ticket": ticket, "price": res.price, "status": "FILLED"}
            else:
                err_msg = res.comment if res else str(mt5.last_error())
                retcode = getattr(res, "retcode", None)
                logger.warning("[MT5-NATIVE] OrderSend() failed: %s (retcode=%s)", err_msg, retcode)
                self.record_execution_report({
                    "order_id": order_payload["id"],
                    "ticket": 0,
                    "status": "REJECTED",
                    "fill_price": 0.0,
                    "retcode": retcode,
                    "error": err_msg,
                })
                return {
                    "ticket": None,
                    "status": "REJECTED_BY_BROKER",
                    "retcode": retcode,
                    "error": err_msg,
                }
        except Exception as exc:
            logger.debug("[MT5-NATIVE] Native MT5 execution skipped: %s", exc)
            return None
            logger.debug("[MT5-NATIVE] Native MT5 execution skipped: %s", exc)
            return None

    def _execute_native_mt5_close(self, ticket: int, symbol: str, lot: float, direction: str) -> bool:
        """Closes an open position directly via Python MetaTrader5 package."""
        mt5 = self._ensure_native_mt5()
        if mt5 is None:
            return False
        try:
            t_info = mt5.terminal_info()
            if not t_info or not getattr(t_info, "connected", False):
                return False

            mt5.symbol_select(symbol, True)
            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                return False

            # Fetch actual live position from MT5 to get accurate open volume and position type
            pos = None
            try:
                positions = mt5.positions_get(ticket=ticket)
                if positions and len(positions) > 0:
                    pos = positions[0]
            except Exception:
                pass

            actual_lot = float(getattr(pos, "volume", lot or 0.01)) if pos else float(lot or 0.01)
            # MT5 position type: 0 = POSITION_TYPE_BUY, 1 = POSITION_TYPE_SELL
            if pos is not None:
                is_buy = getattr(pos, "type", 0) == 0
            else:
                is_buy = direction in ("BUY", "LONG")

            close_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
            close_price = tick.bid if is_buy else tick.ask

            sym_info = mt5.symbol_info(symbol)
            filling = mt5.ORDER_FILLING_IOC
            if sym_info and hasattr(sym_info, "filling_mode"):
                fm = sym_info.filling_mode
                if fm & 2:
                    filling = mt5.ORDER_FILLING_IOC
                elif fm & 1:
                    filling = mt5.ORDER_FILLING_FOK
                else:
                    filling = mt5.ORDER_FILLING_RETURN

            magic_num = int(get_execution_settings().mt5_magic_number or 777888)

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": ticket,
                "symbol": symbol,
                "volume": actual_lot,
                "type": close_type,
                "price": close_price,
                "deviation": 25,
                "magic": magic_num,
                "comment": "Close Fib Retr",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }
            res = mt5.order_send(request)
            if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info("[MT5-NATIVE] Position #%d closed successfully @ %.2f", ticket, res.price)
                return True
            else:
                err = res.comment if res else str(mt5.last_error())
                logger.warning("[MT5-NATIVE] Close position #%d failed: %s", ticket, err)
                return False
        except Exception as exc:
            logger.debug("[MT5-NATIVE] Native close skipped: %s", exc)
            return False

    def enqueue_close(
        self,
        paper_trade_id: str | None = None,
        ticket: int | None = None,
        symbol: str | None = None,
        reason: str = "CLOSE",
        direction: str | None = None,
    ) -> dict[str, Any]:
        """Enqueues a position close request for MT5 execution."""
        exec_cfg = get_execution_settings()
        if not exec_cfg.mt5_bridge_enabled:
            return {"status": "disabled", "reason": "MT5 bridge is disabled."}

        target_ticket = ticket
        if not target_ticket and paper_trade_id:
            target_ticket = self.get_ticket_for_paper_trade(paper_trade_id)

        # Fallback: Query live positions directly from MT5 strictly matching paper trade ID prefix
        if not target_ticket:
            try:
                import MetaTrader5 as mt5
                sym = str(symbol or exec_cfg.mt5_symbol or "XAUUSD-VIP").upper()
                open_pos = mt5.positions_get(symbol=sym)
                if open_pos and paper_trade_id:
                    for pos in open_pos:
                        comm = getattr(pos, "comment", "")
                        if str(paper_trade_id)[:6] in comm:
                            target_ticket = int(pos.ticket)
                            with self._lock:
                                self._paper_trade_to_ticket[str(paper_trade_id)] = target_ticket
                            break
            except Exception as e:
                logger.debug("[MT5-BRIDGE] Fallback close position search failed: %s", e)

        order_id = f"mt5-close-{uuid.uuid4().hex[:8]}"
        order_payload = {
            "id": order_id,
            "action": "CLOSE",
            "ticket": int(target_ticket) if target_ticket else 0,
            "paper_trade_id": paper_trade_id,
            "direction": str(direction or "").upper(),
            "symbol": str(symbol or exec_cfg.mt5_symbol or "XAUUSD-VIP").upper(),
            "reason": reason,
            "magic_number": exec_cfg.mt5_magic_number,
            "created_at": time.time(),
            "status": "PENDING",
        }

        # Attempt immediate direct close via native Python MT5
        if target_ticket:
            lot_to_close = 0.01
            for rec in reversed(self._execution_history):
                if rec.get("ticket") == target_ticket and rec.get("lot_size"):
                    lot_to_close = float(rec["lot_size"])
                    break
            native_closed = self._execute_native_mt5_close(
                ticket=int(target_ticket),
                symbol=order_payload["symbol"],
                lot=lot_to_close,
                direction=order_payload["direction"],
            )
            if native_closed:
                order_payload["status"] = "CLOSED"
                return {
                    "status": "closed_native",
                    "ticket": target_ticket,
                    "order_id": order_id,
                }

        with self._lock:
            self._orders[order_id] = order_payload
            self._pending_ids.append(order_id)

        logger.info(
            "[MT5-BRIDGE] Enqueued CLOSE request %s for Ticket #%s (Paper Trade: %s, Reason: %s)",
            order_id,
            target_ticket or "ALL",
            paper_trade_id,
            reason,
        )
        return {"status": "queued", "order_id": order_id, "payload": order_payload}

    def enqueue_modify(
        self,
        paper_trade_id: str | None = None,
        ticket: int | None = None,
        symbol: str | None = None,
        new_sl: float | None = None,
        new_tp: float | None = None,
        direction: str | None = None,
    ) -> dict[str, Any]:
        """Enqueues a SL/TP modify request for MT5 execution (e.g. Smart Shield Breakeven)."""
        exec_cfg = get_execution_settings()
        if not exec_cfg.mt5_bridge_enabled:
            return {"status": "disabled", "reason": "MT5 bridge is disabled."}

        target_ticket = ticket
        if not target_ticket and paper_trade_id:
            target_ticket = self.get_ticket_for_paper_trade(paper_trade_id)

        # Fallback: Query live positions directly from MT5 if target_ticket is still unknown
        if not target_ticket:
            try:
                import MetaTrader5 as mt5
                sym = str(symbol or exec_cfg.mt5_symbol or "XAUUSD-VIP").upper()
                open_pos = mt5.positions_get(symbol=sym)
                if open_pos:
                    for pos in open_pos:
                        comm = getattr(pos, "comment", "")
                        if "L1" in comm or len(open_pos) == 1:
                            target_ticket = int(pos.ticket)
                            if paper_trade_id:
                                with self._lock:
                                    self._paper_trade_to_ticket[str(paper_trade_id)] = target_ticket
                            break
            except Exception as e:
                logger.debug("[MT5-BRIDGE] Fallback modify position search failed: %s", e)

        order_id = f"mt5-mod-{uuid.uuid4().hex[:8]}"
        order_payload = {
            "id": order_id,
            "action": "MODIFY",
            "ticket": int(target_ticket) if target_ticket else 0,
            "paper_trade_id": paper_trade_id,
            "direction": str(direction or "").upper(),
            "symbol": str(symbol or exec_cfg.mt5_symbol or "XAUUSD-VIP").upper(),
            "stop_loss": float(new_sl) if new_sl is not None else 0.0,
            "take_profit": float(new_tp) if new_tp is not None else 0.0,
            "magic_number": exec_cfg.mt5_magic_number,
            "created_at": time.time(),
            "status": "PENDING",
        }

        with self._lock:
            self._orders[order_id] = order_payload
            self._pending_ids.append(order_id)

        # Attempt immediate direct modify via native Python MT5 on Windows
        if target_ticket:
            try:
                mt5 = self._ensure_native_mt5()
                if mt5 is not None:
                    request = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "position": int(target_ticket),
                        "symbol": order_payload["symbol"],
                        "sl": float(new_sl or 0.0),
                        "tp": float(new_tp or 0.0),
                    }
                    res = mt5.order_send(request)
                    if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
                        logger.info("[MT5-NATIVE] Modified SL/TP for position #%d successfully (SL=%.2f, TP=%.2f)", target_ticket, new_sl or 0.0, new_tp or 0.0)
                        with self._lock:
                            if order_id in self._pending_ids:
                                self._pending_ids.remove(order_id)
                            order_payload["status"] = "MODIFIED"
                        return {"status": "modified_native", "order_id": order_id, "payload": order_payload}
            except Exception as exc:
                logger.debug("[MT5-NATIVE] Native modify skipped: %s", exc)

        logger.info(
            "[MT5-BRIDGE] Enqueued MODIFY request %s for Ticket #%s (SL=%.2f, TP=%.2f)",
            order_id,
            target_ticket or "ALL",
            new_sl or 0.0,
            new_tp or 0.0,
        )
        return {"status": "queued", "order_id": order_id, "payload": order_payload}

    def pop_pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Called by MT5 EA to fetch and consume pending orders."""
        with self._lock:
            if not self._pending_ids:
                return []

            results = []
            remaining_ids = []
            for oid in self._pending_ids:
                order = self._orders.get(oid)
                if not order:
                    continue
                if symbol:
                    req_sym = symbol.upper()
                    ord_sym = str(order.get("symbol", "")).upper()
                    is_sym_match = (req_sym == ord_sym) or (
                        ("XAU" in req_sym or "GOLD" in req_sym) and ("XAU" in ord_sym or "GOLD" in ord_sym)
                    )
                    if not is_sym_match:
                        remaining_ids.append(oid)
                        continue

                order["status"] = "SENT_TO_EA"
                order["sent_at"] = time.time()
                results.append(order)

            self._pending_ids = remaining_ids
            return results

    def record_execution_report(self, report: dict[str, Any]) -> dict[str, Any]:
        """Called by MT5 EA when an order is filled or rejected."""
        order_id = str(report.get("order_id") or report.get("id"))
        ticket = report.get("ticket") or report.get("order_ticket")
        status = str(report.get("status", "FILLED")).upper()
        fill_price = float(report.get("fill_price") or report.get("price") or 0.0)

        with self._lock:
            order = self._orders.get(order_id, {})
            order["status"] = status
            order["ticket"] = ticket
            order["fill_price"] = fill_price
            order["filled_at"] = time.time()
            order["retcode"] = report.get("retcode")
            order["error"] = report.get("error")

            pt_id = order.get("paper_trade_id")
            if ticket and pt_id:
                self._paper_trade_to_ticket[str(pt_id)] = int(ticket)
                self._ticket_to_paper_trade[int(ticket)] = str(pt_id)

            record = {
                "order_id": order_id,
                "ticket": ticket,
                "action": order.get("action"),
                "symbol": order.get("symbol", "XAUUSD"),
                "lot_size": order.get("lot_size"),
                "fill_price": fill_price,
                "status": status,
                "retcode": report.get("retcode"),
                "error": report.get("error"),
                "timestamp": time.time(),
            }
            self._execution_history.append(record)
            if len(self._execution_history) > 100:
                self._execution_history.pop(0)

        logger.info(
            "[MT5-BRIDGE] Execution Report: Order %s -> Ticket %s (%s @ %.2f, retcode=%s, error=%s)",
            order_id,
            ticket,
            status,
            fill_price,
            report.get("retcode"),
            report.get("error"),
        )

        return {"status": "success", "ticket": ticket, "order_id": order_id}

    def get_diagnostics(self) -> dict[str, Any]:
        """Deep diagnostic info on MT5 connection, terminal, account, and symbol permissions."""
        with self._lock:
            res: dict[str, Any] = {
                "is_online": self.is_online,
                "seconds_since_heartbeat": round(time.time() - self._heartbeat.get("last_seen", 0.0), 1),
                "pending_orders": [self._orders.get(oid) for oid in self._pending_ids],
                "recent_executions": self._execution_history[-10:],
            }
        mt5 = self._ensure_native_mt5()
        if mt5 is None:
            res["native_mt5"] = "Not initialized or not running on Windows"
            return res

        try:
            t_info = mt5.terminal_info()
            if t_info:
                res["terminal_info"] = {
                    "connected": getattr(t_info, "connected", None),
                    "trade_allowed": getattr(t_info, "trade_allowed", None),
                    "tradeapi_disabled": getattr(t_info, "tradeapi_disabled", None),
                    "dlls_allowed": getattr(t_info, "dlls_allowed", None),
                    "name": getattr(t_info, "name", None),
                    "path": getattr(t_info, "path", None),
                    "build": getattr(t_info, "build", None),
                }
            ai = mt5.account_info()
            if ai:
                res["account_info"] = {
                    "login": getattr(ai, "login", None),
                    "server": getattr(ai, "server", None),
                    "trade_allowed": getattr(ai, "trade_allowed", None),
                    "trade_expert": getattr(ai, "trade_expert", None),
                    "trade_mode": getattr(ai, "trade_mode", None),
                    "balance": getattr(ai, "balance", None),
                    "equity": getattr(ai, "equity", None),
                    "margin_free": getattr(ai, "margin_free", None),
                    "currency": getattr(ai, "currency", None),
                }
            exec_cfg = get_execution_settings()
            sym = exec_cfg.mt5_symbol or "XAUUSD-VIP"
            mt5.symbol_select(sym, True)
            s_info = mt5.symbol_info(sym)
            if s_info:
                res["symbol_info"] = {
                    "name": getattr(s_info, "name", None),
                    "trade_mode": getattr(s_info, "trade_mode", None),
                    "trade_execution": getattr(s_info, "trade_execution", None),
                    "filling_mode": getattr(s_info, "filling_mode", None),
                    "volume_min": getattr(s_info, "volume_min", None),
                    "volume_max": getattr(s_info, "volume_max", None),
                    "volume_step": getattr(s_info, "volume_step", None),
                    "ask": getattr(s_info, "ask", None),
                    "bid": getattr(s_info, "bid", None),
                }
            res["last_error"] = mt5.last_error()
        except Exception as e:
            res["diagnostic_error"] = str(e)
        return res

    def clear_all(self) -> None:
        """Clear queues (useful for testing or reset)."""
        with self._lock:
            self._orders.clear()
            self._pending_ids.clear()
            self._execution_history.clear()
            self._paper_trade_to_ticket.clear()
            self._ticket_to_paper_trade.clear()


# Global singleton helper
def get_mt5_bridge_manager() -> MT5BridgeManager:
    return MT5BridgeManager()
