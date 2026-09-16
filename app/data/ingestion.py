"""
Historical data ingestion, validation and resampling service.

Provides a clean interface for importing XAU/USD OHLCV data from any source
(CSV, MT5, broker exports) with strict validation of timestamp ordering,
duplicates, gaps, and OHLC consistency.
"""

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from app.core.constants import TimeFrame
from app.core.exceptions import DataProviderError
from app.core.logging import logger
from app.data.models import Candle
from app.data.timeframe_resampler import resample_candles


class ValidationResult(BaseModel):
    """Outcome of candle data validation."""
    total_candles: int = 0
    valid: bool = True
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duplicates: int = 0
    gaps: int = 0
    out_of_order: int = 0
    invalid_ohlc: int = 0
    start_time: datetime | None = None
    end_time: datetime | None = None


def _tf_minutes(tf: TimeFrame) -> int:
    return {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}.get(tf.value, 15)


def _round_timestamp(ts: datetime, tf: TimeFrame) -> datetime:
    """Round a timestamp down to the nearest expected interval boundary."""
    minutes = _tf_minutes(tf)
    total_minutes = ts.hour * 60 + ts.minute
    remainder = total_minutes % minutes
    adjusted = ts.replace(second=0, microsecond=0) - timedelta(minutes=remainder)
    return adjusted.replace(tzinfo=timezone.utc) if adjusted.tzinfo is None else adjusted.astimezone(timezone.utc)


def is_forex_market_break(prev_ts: datetime, ts: datetime) -> bool:
    """Check if the delta between prev_ts and ts corresponds to an expected Forex market break
    (such as the weekend closure from Friday evening to Sunday evening, or daily rollover break).
    """
    p_aware = prev_ts if prev_ts.tzinfo else prev_ts.replace(tzinfo=timezone.utc)
    t_aware = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    diff_sec = (t_aware - p_aware).total_seconds()
    if diff_sec <= 0:
        return False
    # Weekend break: Friday close (weekday 4) to Sunday open (weekday 6) or Monday (weekday 0)
    p_w = p_aware.weekday()
    t_w = t_aware.weekday()
    if (p_w == 4 and t_w in (6, 0)) or (p_w == 5 and t_w in (6, 0)):
        return True
    # Daily rollover break (typically 1 to 2 hours around 21:00-23:59 UTC on weekdays)
    if diff_sec <= 7800 and (p_aware.hour in (20, 21, 22, 23) or t_aware.hour in (21, 22, 23, 0)):
        return True
    return False


def validate_candles(
    candles: list[Candle],
    expected_tf: TimeFrame = TimeFrame.M15,
    strict_gaps: bool = False,
    ignore_market_breaks: bool = True,
) -> ValidationResult:
    """Validate a list of candles for consistency, ordering and completeness.

    Args:
        candles: Candle list to validate (must be sorted by timestamp).
        expected_tf: Expected timeframe interval between candles.
        strict_gaps: If True, missing candles are reported as errors.
        ignore_market_breaks: If True, scheduled Forex weekend closures and rollover breaks are not counted as gaps.

    Returns:
        A ValidationResult with errors, warnings, and counts.
    """
    result = ValidationResult(total_candles=len(candles))
    if not candles:
        result.valid = False
        result.errors.append("No candles provided.")
        return result

    expected_minutes = _tf_minutes(expected_tf)
    expected_delta = timedelta(minutes=expected_minutes)

    seen_timestamps: set = set()
    prev_ts: datetime | None = None

    for i, c in enumerate(candles):
        # NaN / zero price checks
        if c.open <= 0 or c.high <= 0 or c.low <= 0 or c.close <= 0:
            result.invalid_ohlc += 1
            result.errors.append(f"Candle[{i}]: Non-positive price detected.")
            result.valid = False
            continue

        # OHLC consistency
        if c.high < c.low:
            result.invalid_ohlc += 1
            result.errors.append(f"Candle[{i}]: High ({c.high}) < Low ({c.low}).")
            result.valid = False
            continue
        if c.high < c.open or c.high < c.close:
            result.invalid_ohlc += 1
            result.errors.append(f"Candle[{i}]: High ({c.high}) below Open/Close.")
            result.valid = False
            continue
        if c.low > c.open or c.low > c.close:
            result.invalid_ohlc += 1
            result.errors.append(f"Candle[{i}]: Low ({c.low}) above Open/Close.")
            result.valid = False
            continue

        # Timestamp consistency
        ts = c.timestamp
        if ts.tzinfo is None:
            result.warnings.append(f"Candle[{i}]: Timestamp is timezone-naive, assuming UTC.")
            ts = ts.replace(tzinfo=timezone.utc)

        if ts in seen_timestamps:
            result.duplicates += 1
            result.warnings.append(f"Candle[{i}]: Duplicate timestamp {ts}.")
            continue

        if prev_ts is not None:
            diff = (ts - prev_ts).total_seconds()
            if diff < 0:
                result.out_of_order += 1
                result.errors.append(f"Candle[{i}]: Out-of-order timestamp {ts} after {prev_ts}.")
                result.valid = False
            elif diff > expected_delta.total_seconds() * 1.5:
                if ignore_market_breaks and is_forex_market_break(prev_ts, ts):
                    # Scheduled market closure — not a data degradation gap
                    pass
                else:
                    result.gaps += 1
                    msg = f"Candle[{i}]: Gap of {diff / 60:.0f} min after {prev_ts}."
                    if strict_gaps:
                        result.errors.append(msg)
                        result.valid = False
                    else:
                        result.warnings.append(msg)

        seen_timestamps.add(ts)
        prev_ts = ts

    if candles:
        aware_ts = [c.timestamp.replace(tzinfo=timezone.utc) if c.timestamp.tzinfo is None else c.timestamp.astimezone(timezone.utc) for c in candles]
        result.start_time = min(aware_ts)
        result.end_time = max(aware_ts)

    if result.valid and not result.warnings:
        logger.info(
            "Candle validation passed: %s candles, %s -> %s.",
            len(candles), result.start_time, result.end_time,
        )
    return result


def deduplicate_candles(candles: list[Candle]) -> list[Candle]:
    """Remove duplicate timestamps, keeping the last occurrence."""
    seen: dict = {}
    for c in candles:
        seen[c.timestamp] = c
    return sorted(seen.values(), key=lambda c: c.timestamp)


def sort_candles(candles: list[Candle]) -> list[Candle]:
    """Sort candles chronologically and return a new list (non-mutating)."""
    return sorted(candles, key=lambda c: c.timestamp)


def ingest_candles_from_csv(
    filepath: str,
    expected_tf: TimeFrame = TimeFrame.M15,
    validate: bool = True,
) -> list[Candle]:
    """Load candles from a CSV file with optional validation.

    Expected CSV columns: timestamp (or time/date), open, high, low, close [, volume].
    """
    import os

    import pandas as pd

    if not os.path.exists(filepath):
        raise DataProviderError(f"File not found: {filepath}")

    df = pd.read_csv(filepath)
    col_map = {c.lower().strip(): c for c in df.columns}

    ts_col = next((col_map[c] for c in ["timestamp", "time", "date", "datetime"] if c in col_map), None)
    if ts_col is None:
        raise DataProviderError("CSV must contain a timestamp/time/date column.")

    df["_ts"] = pd.to_datetime(df[ts_col], utc=True)
    df.sort_values("_ts", inplace=True)

    candles = []
    for _, row in df.iterrows():
        o = float(row[col_map.get("open", "open")])
        h = float(row[col_map.get("high", "high")])
        l = float(row[col_map.get("low", "low")])
        c = float(row[col_map.get("close", "close")])
        v = float(row[col_map["volume"]]) if "volume" in col_map else 0.0
        h = max(h, o, c, l)
        l = min(l, o, c, h)
        candles.append(
            Candle(
                timestamp=row["_ts"].to_pydatetime(),
                open=o, high=h, low=l, close=c, volume=v,
            )
        )

    if validate:
        vresult = validate_candles(candles, expected_tf)
        if not vresult.valid:
            logger.error("CSV data validation failed: %s", vresult.errors)
            raise DataProviderError(f"CSV data validation failed: {vresult.errors[0]}")
        if vresult.warnings:
            for w in vresult.warnings:
                logger.warning("CSV data warning: %s", w)

    return candles


def build_multi_timeframe_dataset(
    base_candles: list[Candle],
    base_tf: TimeFrame = TimeFrame.M15,
    target_tfs: list[TimeFrame] | None = None,
    validate: bool = True,
) -> dict[TimeFrame, list[Candle]]:
    """Resample base candles into multiple target timeframes.

    Args:
        base_candles: The base series (e.g., 15M).
        base_tf: Timeframe of the base series.
        target_tfs: Desired output timeframes. Defaults to [M15, M30, H1, H4].

    Returns:
        Dict mapping TimeFrame -> candle list.
    """
    if target_tfs is None:
        target_tfs = [TimeFrame.M15, TimeFrame.M30, TimeFrame.H1, TimeFrame.H4]

    if validate:
        vresult = validate_candles(base_candles, base_tf)
        if not vresult.valid:
            raise DataProviderError(f"Base candle validation failed: {vresult.errors[0]}")

    result: dict[TimeFrame, list[Candle]] = {}
    for tf in target_tfs:
        if tf == base_tf:
            result[tf] = list(base_candles)
        else:
            result[tf] = resample_candles(base_candles, tf)
    return result