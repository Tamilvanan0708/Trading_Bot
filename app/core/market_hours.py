"""
Market hours utility for XAU/USD (Binance XAUUSDT futures).

XAUUSDT trades 24 hours a day, 5 days a week:
  Open:   Sunday 23:00 UTC
  Close:  Friday 23:00 UTC
  Closed: Friday 23:00 UTC → Sunday 23:00 UTC (weekend)

The scheduler skips analysis during the weekend to avoid excess log noise
and wasted CPU.
"""

from datetime import datetime, timedelta, timezone


def is_market_open(now: datetime) -> bool:
    """True if the XAUUSDT market is currently open for trading."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    weekday = now.weekday()  # Monday=0, Sunday=6
    hour = now.hour
    # Saturday (5) — closed all day
    if weekday == 5:
        return False
    # Sunday (6) — closed until 23:00 UTC
    if weekday == 6:
        return hour >= 23
    # Friday (4) — closes at 23:00 UTC
    if weekday == 4:
        return hour < 23
    # Monday–Thursday — open 24h
    return True


def next_market_open(now: datetime) -> datetime:
    """Return the next market open time after now (always in the future)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    weekday = now.weekday()
    # Sunday 23:00–23:59 is ALREADY open (a new week started); the "next"
    # open is therefore next Sunday, not a past time.
    if weekday == 6 and now.hour >= 23:
        days_to_sunday = 7
    # Friday after 23:00 → next open is Sunday 23:00
    elif weekday == 4 and now.hour >= 23:
        days_to_sunday = 2
    # Saturday → next open is Sunday 23:00
    elif weekday == 5:
        days_to_sunday = 1
    # Sunday before 23:00 → next open is Sunday 23:00
    elif weekday == 6:
        days_to_sunday = 0
    else:
        # Market is open now — the next open is the coming Sunday 23:00.
        days_to_sunday = (6 - weekday) % 7
        if days_to_sunday == 0:
            days_to_sunday = 7
    target = now.replace(hour=23, minute=0, second=0, microsecond=0) + timedelta(days=days_to_sunday)
    if target <= now:
        target += timedelta(days=7)
    return target