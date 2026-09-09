"""
Overtrading / duplicate-trade protection.

Tracks daily trade counts, daily loss, and consecutive losses in the
restart-safe system_state store so limits survive application restarts.
"""

from datetime import datetime, timezone

from app.config.settings import Settings, get_settings
from app.core.logging import logger
from app.database.repository import Repository

KEY_DAILY_DATE = "limits:daily_date"
KEY_DAILY_TRADES = "limits:daily_trades"
KEY_DAILY_LOSS = "limits:daily_loss"
KEY_CONSECUTIVE_LOSSES = "limits:consecutive_losses"
KEY_DRAWDOWN_PAUSED = "limits:drawdown_paused"


class LimitDecision:
    """Result of a trading-limit check."""
    allowed: bool
    reason: str

    def __init__(self, allowed: bool, reason: str = ""):
        self.allowed = allowed
        self.reason = reason


class TradingLimits:
    """Enforces configurable overtrading protection limits."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async def _load(self, repo: Repository) -> None:
        date = await repo.get_system_state(KEY_DAILY_DATE)
        today = self._today()
        if date != today:
            # New trading day: reset daily counters
            await repo.set_system_state(KEY_DAILY_DATE, today)
            await repo.set_system_state(KEY_DAILY_TRADES, "0")
            await repo.set_system_state(KEY_DAILY_LOSS, "0")

    async def check_open_allowed(
        self,
        repo: Repository,
        current_balance: float | None = None,
        initial_balance: float | None = None,
    ) -> LimitDecision:
        """Checks whether a new paper trade may be opened right now."""
        await self._load(repo)

        daily_trades = int(await repo.get_system_state(KEY_DAILY_TRADES, "0"))
        daily_loss = float(await repo.get_system_state(KEY_DAILY_LOSS, "0"))
        consecutive_losses = int(await repo.get_system_state(KEY_CONSECUTIVE_LOSSES, "0"))
        balance = current_balance if current_balance is not None else self.settings.ACCOUNT_BALANCE
        initial_balance = initial_balance if initial_balance is not None else self.settings.ACCOUNT_BALANCE

        # Max total drawdown circuit breaker (survives restarts via system_state)
        if self.settings.MAX_TOTAL_DRAWDOWN_PCT > 0 and initial_balance > 0:
            drawdown_pct = (initial_balance - balance) / initial_balance * 100.0
            if drawdown_pct >= self.settings.MAX_TOTAL_DRAWDOWN_PCT:
                await repo.set_system_state(KEY_DRAWDOWN_PAUSED, "true")
                return LimitDecision(
                    False,
                    f"Total drawdown {drawdown_pct:.1f}% >= max {self.settings.MAX_TOTAL_DRAWDOWN_PCT}% — "
                    f"automatic paper trading paused.",
                )
            await repo.set_system_state(KEY_DRAWDOWN_PAUSED, "false")

        if self.settings.MAX_DAILY_TRADES > 0 and daily_trades >= self.settings.MAX_DAILY_TRADES:
            return LimitDecision(
                False,
                f"Daily trade limit reached ({daily_trades}/{self.settings.MAX_DAILY_TRADES}).",
            )
        if self.settings.MAX_DAILY_LOSS_PCT > 0:
            max_loss_usd = balance * (self.settings.MAX_DAILY_LOSS_PCT / 100.0)
            if daily_loss >= max_loss_usd:
                return LimitDecision(
                    False,
                    f"Daily loss limit reached (${daily_loss:.2f} >= ${max_loss_usd:.2f}).",
                )
        if self.settings.MAX_CONSECUTIVE_LOSSES > 0 and consecutive_losses >= self.settings.MAX_CONSECUTIVE_LOSSES:
            return LimitDecision(
                False,
                f"Max consecutive losses reached ({consecutive_losses}/{self.settings.MAX_CONSECUTIVE_LOSSES}).",
            )
        return LimitDecision(True)

    async def register_trade_result(self, repo: Repository, pnl_usd: float) -> None:
        """Updates daily loss/consecutive-loss counters after a paper trade closes.

        NOTE: daily_trades is incremented only in register_trade_opened() at
        position open; closing a trade must NOT increment it again.
        """
        await self._load(repo)
        daily_trades = int(await repo.get_system_state(KEY_DAILY_TRADES, "0"))
        daily_loss = float(await repo.get_system_state(KEY_DAILY_LOSS, "0"))
        consecutive_losses = int(await repo.get_system_state(KEY_CONSECUTIVE_LOSSES, "0"))

        if pnl_usd < 0:
            daily_loss += abs(pnl_usd)
            consecutive_losses += 1
        else:
            consecutive_losses = 0

        await repo.set_system_state(KEY_DAILY_LOSS, f"{daily_loss:.2f}")
        await repo.set_system_state(KEY_CONSECUTIVE_LOSSES, str(consecutive_losses))
        logger.info(
            "Limits updated: daily_trades=%s daily_loss=$%.2f consecutive_losses=%s",
            daily_trades, daily_loss, consecutive_losses,
        )

    async def register_trade_opened(self, repo: Repository) -> None:
        """Increments the daily trade counter when a position is opened."""
        await self._load(repo)
        daily_trades = int(await repo.get_system_state(KEY_DAILY_TRADES, "0")) + 1
        await repo.set_system_state(KEY_DAILY_TRADES, str(daily_trades))

    async def current_state(self, repo: Repository) -> dict:
        await self._load(repo)
        return {
            "date": await repo.get_system_state(KEY_DAILY_DATE, self._today()),
            "daily_trades": int(await repo.get_system_state(KEY_DAILY_TRADES, "0")),
            "daily_loss_usd": round(float(await repo.get_system_state(KEY_DAILY_LOSS, "0")), 2),
            "consecutive_losses": int(await repo.get_system_state(KEY_CONSECUTIVE_LOSSES, "0")),
        }