"""
cTrader Open API (Spotware) real-time spot feed.

Implements the cTrader Open API WebSocket protocol for live bid/ask quotes:
1. Connect to the app's WebSocket endpoint.
2. Authenticate with an Open API access token (``spot.app.auth``).
3. Subscribe to spots for the target account (``spot.subscribe.spots``).
4. Normalize incoming ``spot.spot`` frames into :class:`Tick` models.

Requires a valid cTrader Open API access token.  The mapping from logical
symbols (e.g. ``XAUUSD``) to numeric cTrader ``symbolId`` values must be
provided via :attr:`symbol_ids`; it can be resolved at runtime with the
``spot.get_symbols`` request and cached.
"""

import json
from datetime import datetime, timezone
from typing import Any

from app.data.models import Tick
from app.data.websocket_provider import WebSocketMarketFeed

CTRADER_WS_URL = "wss://connect.spotware.com/apps/"

MSG_AUTH = "spot.app.auth"
MSG_SUBSCRIBE_SPOTS = "spot.subscribe.spots"
MSG_SPOT = "spot.spot"


def _parse_timestamp(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 1e12 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def build_auth_message(access_token: str, cid: int) -> dict[str, Any]:
    return {"cid": cid, "type": MSG_AUTH, "data": {"accessToken": access_token}}


def build_subscribe_spots_message(account_id: int, symbol_ids: list[int], cid: int) -> dict[str, Any]:
    return {
        "cid": cid,
        "type": MSG_SUBSCRIBE_SPOTS,
        "data": {"accountId": account_id, "symbolIds": list(symbol_ids)},
    }


class CTraderMarketProvider(WebSocketMarketFeed):
    """Real-time spot feed via the cTrader Open API WebSocket."""

    def __init__(
        self,
        symbol: str = "XAUUSD",
        symbol_id: int | None = None,
        account_id: int | None = None,
        access_token: str = "",
        url: str = CTRADER_WS_URL,
        capacity: int = 10_000,
        reconnect_delay: float = 5.0,
        max_reconnect_attempts: int = 0,
    ) -> None:
        if not access_token:
            raise ValueError("CTraderMarketProvider requires an access token.")
        if account_id is None or symbol_id is None:
            raise ValueError("CTraderMarketProvider requires account_id and symbol_id.")

        self._account_id = account_id
        self._symbol_id = symbol_id
        self._access_token = access_token
        self._cid = 0
        self._authenticated = False

        super().__init__(
            url=url,
            symbols=[symbol],
            capacity=capacity,
            reconnect_delay=reconnect_delay,
            max_reconnect_attempts=max_reconnect_attempts,
        )

    def _next_cid(self) -> int:
        self._cid += 1
        return self._cid

    def build_subscribe_message(self) -> str | None:
        return json.dumps(
            build_auth_message(self._access_token, self._next_cid())
        )

    def build_followup_messages(self, message: dict) -> list[str]:
        """Subscribe to spots immediately after a successful authentication."""
        if message.get("type") != MSG_AUTH:
            return []
        data = message.get("data", {}) or {}
        if not data.get("isAuthorized", False):
            return []
        self._authenticated = True
        return [
            json.dumps(
                build_subscribe_spots_message(
                    self._account_id, [self._symbol_id], self._next_cid()
                )
            )
        ]

    def parse_message(self, raw: str) -> list[Tick]:
        try:
            message = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

        msg_type = message.get("type")
        data = message.get("data", {}) or {}

        if msg_type == MSG_AUTH:
            self._authenticated = bool(data.get("isAuthorized", False))
            return []

        if msg_type == MSG_SPOT:
            bid = data.get("bid")
            ask = data.get("ask")
            if bid is None or ask is None:
                return []
            return [
                Tick(
                    symbol=self.symbols[0],
                    timestamp=_parse_timestamp(data.get("timestamp")),
                    bid=float(bid),
                    ask=float(ask),
                    last=float(data.get("price") or (bid + ask) / 2.0),
                )
            ]

        return []