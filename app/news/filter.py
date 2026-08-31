"""
Economic news blackout filter.

Clean abstraction over any news/economic-calendar provider.  Currently supports
a "none" provider (safe fallback that reports news filtering as unavailable)
and a generic provider interface ready for future API integrations.

During a blackout window no NEW paper trades are opened.  Existing trades
continue to be managed by the normal risk rules.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from app.config.settings import Settings, get_settings
from app.core.logging import logger


class NewsEvent:
    """A single high-impact economic event."""
    title: str
    currency: str
    impact: str          # HIGH | MEDIUM | LOW
    start_time: datetime

    def __init__(self, title: str, currency: str, impact: str, start_time: datetime):
        self.title = title
        self.currency = currency
        self.impact = impact
        self.start_time = start_time


class NewsCalendarProvider(ABC):
    """Abstract economic-calendar provider interface."""

    @abstractmethod
    async def get_upcoming_events(
        self, currencies: list[str], max_events: int = 10
    ) -> list[NewsEvent]:
        """Returns upcoming events for the given currencies."""


class NullNewsCalendarProvider(NewsCalendarProvider):
    """Safe fallback: no provider configured."""

    def __init__(self):
        logger.warning("No news provider configured (NEWS_PROVIDER=none); news filtering is UNAVAILABLE.")

    async def get_upcoming_events(self, currencies: list[str], max_events: int = 10) -> list[NewsEvent]:
        return []


class NewsFilter:
    """Enforces news blackout windows around high-impact events."""

    def __init__(self, settings: Settings | None = None, provider: NewsCalendarProvider | None = None):
        self.settings = settings or get_settings()
        self._provider = provider or NullNewsCalendarProvider()
        self._cache: list[NewsEvent] = []
        self._cache_ts: datetime | None = None

    @property
    def available(self) -> bool:
        return not isinstance(self._provider, NullNewsCalendarProvider) and self.settings.NEWS_FILTER_ENABLED

    async def in_blackout(self, at: datetime | None = None) -> bool:
        """True if `at` falls inside a news blackout window."""
        if not self.settings.NEWS_FILTER_ENABLED:
            return False
        now = at or datetime.now(timezone.utc)
        events = await self._get_events()
        before = self.settings.NEWS_BLACKOUT_BEFORE_MINUTES
        after = self.settings.NEWS_BLACKOUT_AFTER_MINUTES

        from datetime import timedelta
        for ev in events:
            if ev.currency != "USD" and ev.currency != "ALL":
                continue
            if ev.impact != "HIGH":
                continue
            start = ev.start_time.astimezone(timezone.utc)
            if start - timedelta(minutes=before) <= now <= start + timedelta(minutes=after):
                return True
        return False

    async def _get_events(self) -> list[NewsEvent]:
        # Cache for 15 minutes to avoid hammering the provider
        now = datetime.now(timezone.utc)
        if self._cache and self._cache_ts and (now - self._cache_ts).total_seconds() < 900:
            return self._cache
        try:
            self._cache = await self._provider.get_upcoming_events(["USD"])
            self._cache_ts = now
        except Exception as exc:
            logger.error("News provider fetch failed (%s); treating as no blackout.", exc)
            self._cache = []
            self._cache_ts = now
        return self._cache