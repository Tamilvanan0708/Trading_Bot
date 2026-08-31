"""
Telegram Notification Service with duplicate prevention and DB logging.
"""


import httpx

from app.ai.models import AIValidationResult
from app.config.settings import Settings, get_settings
from app.core.logging import logger
from app.database.repository import Repository
from app.notifications.formatter import format_telegram_signal
from app.signals.models import SignalPayload


class TelegramService:
    """Dispatches signal alerts to configured Telegram chat/channel."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._last_signal_id: str | None = None

    async def send_signal_alert(
        self,
        signal: SignalPayload,
        ai_val: AIValidationResult | None = None,
        repo: Repository | None = None,
        metadata: dict | None = None,
    ) -> bool:
        """Sends formatted signal message to Telegram if enabled.

        Returns True if the message was dispatched successfully.
        """
        if not self.settings.TELEGRAM_ENABLED or not self.settings.TELEGRAM_BOT_TOKEN or not self.settings.TELEGRAM_CHAT_ID:
            logger.info("Telegram notification skipped (service not configured or disabled).")
            if repo:
                await self._log_notification(repo, signal.signal_id, "TELEGRAM", "SKIPPED", "Telegram not configured.")
            return False

        # Duplicate prevention: skip if this signal was already sent
        if signal.signal_id == self._last_signal_id:
            logger.debug("Duplicate signal %s; skipping Telegram alert.", signal.signal_id)
            return True
        self._last_signal_id = signal.signal_id

        message_text = format_telegram_signal(signal, ai_val, metadata=metadata)
        url = f"https://api.telegram.org/bot{self.settings.TELEGRAM_BOT_TOKEN}/sendMessage"

        payload = {
            "chat_id": self.settings.TELEGRAM_CHAT_ID,
            "text": message_text,
            "parse_mode": "Markdown",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    logger.info("Telegram alert sent successfully for signal %s.", signal.signal_id)
                    if repo:
                        await self._log_notification(repo, signal.signal_id, "TELEGRAM", "SENT", "OK")
                    return True
                else:
                    logger.error("Telegram API responded with error %s: %s", res.status_code, res.text)
                    if repo:
                        await self._log_notification(repo, signal.signal_id, "TELEGRAM", "FAILED", res.text[:200])
                    return False
        except Exception as e:
            logger.error("Failed to dispatch Telegram message: %s", e)
            if repo:
                await self._log_notification(repo, signal.signal_id, "TELEGRAM", "FAILED", str(e))
            return False

    async def send_raw_alert(self, text: str, repo: Repository | None = None) -> bool:
        """Sends a raw message (system alerts, degradation warnings) to Telegram."""
        if not self.settings.TELEGRAM_ENABLED or not self.settings.TELEGRAM_BOT_TOKEN or not self.settings.TELEGRAM_CHAT_ID:
            return False
        url = f"https://api.telegram.org/bot{self.settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": self.settings.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    if repo:
                        await self._log_notification(repo, "", "TELEGRAM", "SENT", "OK")
                    return True
                logger.error("Telegram raw alert error %s: %s", res.status_code, res.text)
                if repo:
                    await self._log_notification(repo, "", "TELEGRAM", "FAILED", res.text[:200])
                return False
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to send raw Telegram alert: %s", exc)
            if repo:
                await self._log_notification(repo, "", "TELEGRAM", "FAILED", str(exc))
            return False

    async def send_typed_alert(
        self,
        alert_type: str,
        text: str,
        repo: Repository | None = None,
        cooldown_seconds: int = 3600,
    ) -> bool:
        """Sends a system alert with a dedup cooldown persisted in the DB.

        ``alert_type`` is the dedup key.  The same alert will not be sent again
        within ``cooldown_seconds`` (unless ``cooldown_seconds <= 0``).
        """
        if not self.settings.TELEGRAM_ENABLED or not self.settings.TELEGRAM_BOT_TOKEN or not self.settings.TELEGRAM_CHAT_ID:
            return False
        if cooldown_seconds > 0:
            key = f"alert:{alert_type}"
            try:
                from datetime import datetime, timezone

                from app.database.connection import async_session_factory
                async with async_session_factory() as session:
                    repo2 = Repository(session)
                    last = await repo2.get_system_state(key, "")
                    now = datetime.now(timezone.utc)
                    if last:
                        last_dt = datetime.fromisoformat(last)
                        if (now - last_dt).total_seconds() < cooldown_seconds:
                            return False  # deduped
                    await repo2.set_system_state(key, now.isoformat())
                    await session.commit()
            except Exception as exc:  # noqa: BLE001
                logger.error("Telegram dedup check failed for %s: %s", alert_type, exc)
        return await self.send_raw_alert(text, repo=repo)

    async def _log_notification(self, repo: Repository, signal_id: str, channel: str, status: str, message: str) -> None:
        try:
            await repo.log_notification({
                "channel": channel,
                "recipient": self.settings.TELEGRAM_CHAT_ID or "unknown",
                "message_content": message[:500],
                "status": status,
                "signal_id": signal_id,
                "error_message": message if status == "FAILED" else None,
            })
        except Exception as log_err:
            logger.error("Failed to persist notification log: %s", log_err)
