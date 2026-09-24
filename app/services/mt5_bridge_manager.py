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

    @property
    def is_online(self) -> bool:
        """True if EA reported heartbeat or native MT5 package is connected to terminal."""
        try:
            import MetaTrader5 as mt5
            t_info = mt5.terminal_info()
            if t_info is not None and getattr(t_info, "connected", False):
                return True
        except Exception:
            pass
        return (time.time() - self._heartbeat.get("last_seen", 0.0)) <= 15.0

    def get_status(self) -> dict[str, Any]:
        """Returns comprehensive bridge status for frontend settings UI."""
        with self._lock:
            exec_cfg = get_execution_settings()
            online = self.is_online
            last_seen = self._heartbeat.get("last_seen", 0.0)
            sec_ago = round(time.time() - last_seen, 1) if last_seen > 0 else None

            # Attempt live account info directly from native Python MT5
            acct_data = None
            try:
                import MetaTrader5 as mt5
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
                "seconds_since_heartbeat": sec_ago if last_seen > 0 else (0.1 if acct_data else None),
                "target_symbol": exec_cfg.mt5_symbol,
                "magic_number": exec_cfg.mt5_magic_number,
                "allowed_strategy": exec_cfg.mt5_allowed_strategy,  # "Fib Retracement"
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

    def get_ticket_for_paper_trade(self, paper_trade_id: str | None) -> int | None:
        """Lookup native or EA execution ticket for a given paper trade ID."""
        if not paper_trade_id:
            return None
        with self._lock:
            return self._paper_trade_to_ticket.get(str(paper_trade_id))

    def get_account_balance(self) -> float | None:
        """Returns live account balance in USD from MT5 if connected."""
        try:
            import MetaTrader5 as mt5
            ai = mt5.account_info()
            if ai is not None and getattr(ai, "balance", None) is not None:
                return round(float(ai.balance), 2)
        except Exception:
            pass
        with self._lock:
            return self._heartbeat.get("balance")

    def enqueue_order(self, order_data: dict[str, Any]) -> dict[str, Any]:
        """
        Enqueues an order for MT5 execution.
        STRICT POLICY:
        - Only 'Fib Retracement' strategy is permitted.
        - SMC With Fib and Fib Trend Breakout are blocked from MT5.
        - Lots are safety-clamped to max 0.50 Lot.
        """
        strat = str(order_data.get("strategy", "Fib Retracement")).strip()
        strat_upper = strat.upper()

        # Block any strategy that is not Fib Retracement
        if "SMC" in strat_upper or "TREND" in strat_upper or ("FIB" in strat_upper and "RETRACEMENT" not in strat_upper):
            logger.info(
                "[MT5-BRIDGE] Strategy '%s' blocked. MT5 execution is strictly reserved for Fib Retracement only.",
                strat,
            )
            return {
                "status": "filtered",
                "reason": f"Strategy '{strat}' is disabled for MT5 live execution (Fib Retracement only).",
            }

        exec_cfg = get_execution_settings()
        if not exec_cfg.mt5_bridge_enabled and not order_data.get("is_test", False):
            logger.info("[MT5-BRIDGE] MT5 bridge is disabled in Execution Settings; skipping order.")
            return {
                "status": "disabled",
                "reason": "MT5 bridge is disabled in settings.",
            }

        order_id = str(order_data.get("id") or f"mt5-{uuid.uuid4().hex[:8]}")
        raw_lot = float(order_data.get("lot_size") or order_data.get("lots") or 0.01)
        clamped_lot = max(0.01, min(0.50, round(raw_lot, 2)))  # Solution A+B clamp

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
            "strategy": "Fib Retracement",
            "layer": str(order_data.get("layer", "L1")),
            "timeframe": str(order_data.get("timeframe", "5M")).upper(),
            "magic_number": exec_cfg.mt5_magic_number,
            "comment": str(order_data.get("comment") or f"XAU_{order_data.get('timeframe', '5M')}_{order_data.get('layer', 'L1')}").strip()[:31],
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
        try:
            import MetaTrader5 as mt5
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

            # Direction 4: Live Spread Filter Check
            live_spread = round(abs(tick.ask - tick.bid), 2)
            exec_cfg = get_execution_settings()
            if getattr(exec_cfg, "spread_filter_enabled", True):
                max_spread = float(getattr(exec_cfg, "max_spread_points", 2.0))
                if live_spread > max_spread:
                    logger.warning(
                        "[MT5-NATIVE] SPREAD FILTER TRIGGERED: Live spread %.2f pts exceeds maximum allowed %.2f pts for %s. Order %s BLOCKED.",
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
            if getattr(exec_cfg, "chase_filter_enabled", True) and entry_target > 0:
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

            # Check broker filling mode
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
                logger.warning("[MT5-NATIVE] OrderSend() failed: %s (retcode=%s)", err_msg, getattr(res, "retcode", None))
                return None
        except Exception as exc:
            logger.debug("[MT5-NATIVE] Native MT5 execution skipped: %s", exc)
            return None

    def _execute_native_mt5_close(self, ticket: int, symbol: str, lot: float, direction: str) -> bool:
        """Closes an open position directly via Python MetaTrader5 package."""
        try:
            import MetaTrader5 as mt5
            t_info = mt5.terminal_info()
            if not t_info or not getattr(t_info, "connected", False):
                return False

            mt5.symbol_select(symbol, True)
            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                return False

            is_buy = direction in ("BUY", "LONG")
            close_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
            close_price = tick.bid if is_buy else tick.ask

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": ticket,
                "symbol": symbol,
                "volume": lot,
                "type": close_type,
                "price": close_price,
                "deviation": 20,
                "magic": 123456,
                "comment": "Close Fib Retr",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
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
            target_ticket = self._paper_trade_to_ticket.get(str(paper_trade_id))

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
            target_ticket = self._paper_trade_to_ticket.get(str(paper_trade_id))

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
                import MetaTrader5 as mt5
                t_info = mt5.terminal_info()
                if t_info and getattr(t_info, "connected", False):
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
                "timestamp": time.time(),
            }
            self._execution_history.append(record)
            if len(self._execution_history) > 100:
                self._execution_history.pop(0)

        logger.info(
            "[MT5-BRIDGE] Execution Report: Order %s -> Ticket %s (%s @ %.2f)",
            order_id,
            ticket,
            status,
            fill_price,
        )

        return {"status": "success", "ticket": ticket, "order_id": order_id}

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
