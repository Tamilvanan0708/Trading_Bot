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
        """True if EA reported heartbeat within the last 15 seconds."""
        return (time.time() - self._heartbeat.get("last_seen", 0.0)) <= 15.0

    def get_status(self) -> dict[str, Any]:
        """Returns comprehensive bridge status for frontend settings UI."""
        with self._lock:
            exec_cfg = get_execution_settings()
            online = self.is_online
            last_seen = self._heartbeat.get("last_seen", 0.0)
            sec_ago = round(time.time() - last_seen, 1) if last_seen > 0 else None

            return {
                "bridge_enabled": exec_cfg.mt5_bridge_enabled,
                "is_online": online,
                "seconds_since_heartbeat": sec_ago,
                "target_symbol": exec_cfg.mt5_symbol,
                "magic_number": exec_cfg.mt5_magic_number,
                "allowed_strategy": exec_cfg.mt5_allowed_strategy,  # "Fib Retracement"
                "connected_account": {
                    "login": self._heartbeat.get("account_login"),
                    "server": self._heartbeat.get("server"),
                    "balance": self._heartbeat.get("balance"),
                    "equity": self._heartbeat.get("equity"),
                    "leverage": self._heartbeat.get("leverage"),
                    "symbol": self._heartbeat.get("symbol"),
                } if online else None,
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
        sl_pts = round(abs(entry_px - sl_px), 2) if (entry_px > 0 and sl_px > 0) else float(order_data.get("sl_points") or 0.0)
        tp_pts = round(abs(tp_px - entry_px), 2) if (entry_px > 0 and tp_px > 0) else float(order_data.get("tp_points") or 0.0)

        order_payload = {
            "id": order_id,
            "symbol": str(order_data.get("symbol") or exec_cfg.mt5_symbol or "XAUUSD").upper(),
            "action": str(order_data.get("direction") or order_data.get("action") or "BUY").upper(),
            "lot_size": clamped_lot,
            "entry_price": entry_px,
            "stop_loss": sl_px,
            "take_profit": tp_px,
            "sl_points": sl_pts,
            "tp_points": tp_pts,
            "strategy": "Fib Retracement",
            "layer": str(order_data.get("layer", "L1")),
            "magic_number": exec_cfg.mt5_magic_number,
            "comment": f"XAU_AI_{order_data.get('layer', 'L1')}",
            "created_at": time.time(),
            "status": "PENDING",
            "paper_trade_id": order_data.get("paper_trade_id"),
        }

        with self._lock:
            self._orders[order_id] = order_payload
            self._pending_ids.append(order_id)

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
            "symbol": str(symbol or exec_cfg.mt5_symbol or "XAUUSD").upper(),
            "reason": reason,
            "magic_number": exec_cfg.mt5_magic_number,
            "created_at": time.time(),
            "status": "PENDING",
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
            "symbol": str(symbol or exec_cfg.mt5_symbol or "XAUUSD").upper(),
            "stop_loss": float(new_sl) if new_sl is not None else 0.0,
            "take_profit": float(new_tp) if new_tp is not None else 0.0,
            "magic_number": exec_cfg.mt5_magic_number,
            "created_at": time.time(),
            "status": "PENDING",
        }

        with self._lock:
            self._orders[order_id] = order_payload
            self._pending_ids.append(order_id)

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
