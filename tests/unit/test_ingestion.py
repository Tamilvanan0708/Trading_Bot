"""
Unit tests for historical data ingestion, validation, and resampling.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.constants import TimeFrame
from app.data.ingestion import (
    build_multi_timeframe_dataset,
    deduplicate_candles,
    ingest_candles_from_csv,
    validate_candles,
)
from app.data.models import Candle


def _candle(ts: datetime, o=2650.0, h=2655.0, l=2645.0, c=2652.0, v=100.0) -> Candle:
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def test_validate_empty_candles():
    result = validate_candles([])
    assert result.valid is False
    assert "No candles" in result.errors[0]


def test_validate_valid_candles():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [
        _candle(now + timedelta(minutes=15 * i)) for i in range(10)
    ]
    result = validate_candles(candles, TimeFrame.M15)
    assert result.valid is True
    assert result.total_candles == 10
    assert result.errors == []


def test_validate_out_of_order():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [
        _candle(now + timedelta(minutes=15)),
        _candle(now),
    ]
    result = validate_candles(candles, TimeFrame.M15)
    assert result.valid is False
    assert result.out_of_order == 1


def test_validate_duplicate():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [
        _candle(now),
        _candle(now),
    ]
    result = validate_candles(candles, TimeFrame.M15)
    assert result.duplicates == 1


def test_validate_invalid_ohlc():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    # Candle model validates high>=open and low<=open but NOT high>=close or low<=close
    candles = [
        _candle(now, o=2650.0, h=2655.0, l=2645.0, c=2700.0),  # close > high
    ]
    result = validate_candles(candles, TimeFrame.M15)
    assert result.valid is False
    assert result.invalid_ohlc >= 1


def test_validate_gaps_detected():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [
        _candle(now),
        _candle(now + timedelta(hours=2)),  # 2-hour gap (should be 15 min)
    ]
    result = validate_candles(candles, TimeFrame.M15)
    assert result.gaps == 1  # 120 min gap, expected 15 min


def test_validate_strict_gaps():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [
        _candle(now),
        _candle(now + timedelta(minutes=30)),
    ]
    result = validate_candles(candles, TimeFrame.M15, strict_gaps=True)
    assert result.valid is False  # 30 min gap instead of 15
    assert result.gaps == 1


def test_deduplicate_candles():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [
        _candle(now, c=2650.0),
        _candle(now + timedelta(minutes=15), c=2655.0),
        _candle(now, c=2660.0),  # duplicate with later value
    ]
    deduped = deduplicate_candles(candles)
    assert len(deduped) == 2
    # The duplicate should be replaced by the later occurrence
    assert deduped[0].close == 2660.0


def test_build_multi_timeframe_dataset():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [
        _candle(now + timedelta(minutes=15 * i), o=2650.0 + i, h=2655.0 + i, l=2645.0 + i, c=2652.0 + i)
        for i in range(200)
    ]
    dataset = build_multi_timeframe_dataset(candles, TimeFrame.M15, validate=False)
    assert TimeFrame.M15 in dataset
    assert TimeFrame.M30 in dataset
    assert TimeFrame.H1 in dataset
    assert TimeFrame.H4 in dataset
    assert len(dataset[TimeFrame.M15]) == 200
    assert len(dataset[TimeFrame.M30]) > 0
    assert len(dataset[TimeFrame.H1]) > 0
    assert len(dataset[TimeFrame.H4]) > 0


def test_ingest_csv_file(tmp_path):
    csv_content = """timestamp,open,high,low,close,volume
2024-01-01 00:00:00,2650.0,2655.0,2645.0,2652.0,100
2024-01-01 00:15:00,2652.0,2658.0,2648.0,2655.0,150
2024-01-01 00:30:00,2655.0,2660.0,2650.0,2658.0,120
"""
    path = tmp_path / "test_xauusd.csv"
    path.write_text(csv_content)
    candles = ingest_candles_from_csv(str(path), TimeFrame.M15, validate=True)
    assert len(candles) == 3
    assert candles[0].open == 2650.0
    assert candles[-1].close == 2658.0


def test_ingest_csv_missing_file():
    with pytest.raises(Exception, match="not found"):
        ingest_candles_from_csv("nonexistent.csv", TimeFrame.M15)


def test_ingest_csv_no_timestamp_column(tmp_path):
    csv_content = """price,volume\n2650,100\n"""
    path = tmp_path / "bad.csv"
    path.write_text(csv_content)
    with pytest.raises(Exception, match="timestamp"):
        ingest_candles_from_csv(str(path), TimeFrame.M15)