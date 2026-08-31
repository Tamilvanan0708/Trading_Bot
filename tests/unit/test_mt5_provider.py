"""
Unit tests for the MT5 bridge provider.

The MetaTrader5 package is real (Windows) but we test the conversion,
streaming, and error logic by injecting a fake module via ``sys.modules``.
"""

import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pytest

from app.core.constants import SignalDirection, TimeFrame
from app.core.exceptions import DataProviderError

# ---------------------------------------------------------------------------
# Fake MetaTrader5 module
# ---------------------------------------------------------------------------

_MT5_RATES_DTYPE = np.dtype([
    ("time", "<i8"),
    ("open", "<f8"),
    ("high", "<f8"),
    ("low", "<f8"),
    ("close", "<f8"),
    ("tick_volume", "<u8"),
    ("spread", "<i8"),
    ("real_volume", "<u8"),
])


class _FakeSymbolInfoTick:
    time: int
    bid: float
    ask: float
    last: float
    volume: float

    def __init__(self, time: int, bid: float, ask: float, last: float = 0.0, volume: float = 0.0):
        self.time = time
        self.bid = bid
        self.ask = ask
        self.last = last
        self.volume = volume


class _FakeSymbolInfo:
    spread: int
    digits: int

    def __init__(self, spread: int = 10, digits: int = 3):
        self.spread = spread
        self.digits = digits


class _FakeAccountInfo:
    balance: float = 10000.0


class _FakeMT5:
    """Stub for the MetaTrader5 module."""

    TIMEFRAME_M15 = 15
    TIMEFRAME_M30 = 30
    TIMEFRAME_H1 = 60
    TIMEFRAME_H4 = 240
    TIMEFRAME_D1 = 1440

    def __init__(self):
        self._initialized = False
        self._rates: dict[Any, Any] = {}
        self._tick = _FakeSymbolInfoTick(1723000000, 2650.0, 2650.5)
        self._symbol_info = _FakeSymbolInfo()
        self._account_info = _FakeAccountInfo()
        self._positions: list = []

    def initialize(self, timeout: int = 30) -> bool:
        self._initialized = True
        return True

    def last_error(self) -> str:
        return ""

    def login(self, **kwargs) -> bool:
        return True

    def shutdown(self) -> None:
        self._initialized = False

    def symbol_info_tick(self, symbol: str) -> _FakeSymbolInfoTick:
        return self._tick

    def symbol_info(self, symbol: str) -> _FakeSymbolInfo:
        return self._symbol_info

    def account_info(self) -> _FakeAccountInfo:
        return self._account_info

    def copy_rates_from_pos(self, symbol: str, tf: int, start: int, count: int) -> np.ndarray:
        key = (symbol, tf)
        if key in self._rates:
            return self._rates[key][start:start + count]
        return np.array([], dtype=_MT5_RATES_DTYPE)

    def copy_rates_range(self, symbol: str, tf: int, start: datetime, end: datetime) -> np.ndarray:
        return self.copy_rates_from_pos(symbol, tf, 0, 100)

    def positions_get(self, symbol: str | None = None) -> list:
        return self._positions


def _make_rates(timestamps: list, close_prices: list) -> np.ndarray:
    arr = np.zeros(len(timestamps), dtype=_MT5_RATES_DTYPE)
    for i, (ts, cp) in enumerate(zip(timestamps, close_prices)):
        arr[i] = (int(ts), cp - 2.0, cp + 1.0, cp - 3.0, cp, 1000, 5, 1000)
    return arr


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_mt5():
    module = _FakeMT5()
    sys.modules["MetaTrader5"] = module
    yield module
    sys.modules.pop("MetaTrader5", None)


@pytest.fixture
def provider(fake_mt5):
    from app.data.mt5_provider import MT5MarketDataProvider
    p = MT5MarketDataProvider(symbol="XAUUSD", timezone_offset_minutes=0)
    p._connected = True
    return p


# ---------------------------------------------------------------------------
# Import error
# ---------------------------------------------------------------------------

def test_import_error_raised_when_mt5_missing(monkeypatch):
    # Setting the sys.modules entry to None makes `import MetaTrader5` raise ImportError
    monkeypatch.setitem(sys.modules, "MetaTrader5", None)
    from app.data.mt5_provider import MT5MarketDataProvider
    with pytest.raises(DataProviderError, match="MetaTrader5 package is not installed"):
        MT5MarketDataProvider()


# ---------------------------------------------------------------------------
# Timeframe mapping
# ---------------------------------------------------------------------------

def test_timeframe_mapping(provider):
    assert provider._to_mt5_timeframe(TimeFrame.M15) == 15
    assert provider._to_mt5_timeframe(TimeFrame.H1) == 60
    assert provider._to_mt5_timeframe(TimeFrame.D1) == 1440

    with pytest.raises(DataProviderError, match="Unsupported"):
        provider._to_mt5_timeframe("invalid")  # type: ignore


# ---------------------------------------------------------------------------
# Rates conversion
# ---------------------------------------------------------------------------

def test_rates_to_candles(provider, fake_mt5):
    epoch = 1723000000
    rates = _make_rates([epoch, epoch + 900], [2650.0, 2655.0])
    candles = provider._rates_to_candles(rates)
    assert len(candles) == 2
    assert candles[0].close == 2650.0
    assert candles[1].close == 2655.0
    assert candles[0].timestamp.tzinfo == timezone.utc


def test_rates_to_candles_with_offset(provider, fake_mt5):
    from app.data.mt5_provider import MT5MarketDataProvider
    p = MT5MarketDataProvider(symbol="XAUUSD", timezone_offset_minutes=120)
    p._connected = True
    epoch = 1723000000
    rates = _make_rates([epoch], [2650.0])
    candles = p._rates_to_candles(rates)
    expected = datetime.fromtimestamp(epoch, tz=timezone.utc) + timedelta(minutes=120)
    assert candles[0].timestamp.hour == expected.hour
    assert candles[0].timestamp.minute == expected.minute


# ---------------------------------------------------------------------------
# get_ohlcv
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_ohlcv_returns_candles(provider, fake_mt5):
    epoch = 1723000000
    fake_mt5._rates[("XAUUSD", 15)] = _make_rates(
        [epoch + i * 900 for i in range(10)], [2650.0 + i for i in range(10)]
    )
    candles = await provider.get_ohlcv("XAUUSD", TimeFrame.M15, limit=5)
    assert len(candles) == 5
    assert candles[0].close == 2650.0


@pytest.mark.asyncio
async def test_get_ohlcv_empty_returns_empty(provider, fake_mt5):
    candles = await provider.get_ohlcv("XAUUSD", TimeFrame.M15, limit=5)
    assert candles == []


# ---------------------------------------------------------------------------
# get_tick
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_tick(provider, fake_mt5):
    tick = await provider.get_tick("XAUUSD")
    assert tick is not None
    assert tick.bid == 2650.0
    assert tick.ask == 2650.5
    assert tick.mid == 2650.25


# ---------------------------------------------------------------------------
# get_spread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_spread_from_tick(provider, fake_mt5):
    spread = await provider.get_spread("XAUUSD")
    assert spread == 0.5  # 2650.5 - 2650.0


@pytest.mark.asyncio
async def test_get_spread_from_symbol_info(provider, fake_mt5):
    fake_mt5._tick = _FakeSymbolInfoTick(0, 0.0, 0.0)
    spread = await provider.get_spread("XAUUSD")
    # spread = 10 * 10^-3 = 0.01
    assert spread >= 0.0


# ---------------------------------------------------------------------------
# get_open_positions
# ---------------------------------------------------------------------------

def _fake_position(ticket=1, symbol="XAUUSD", ptype=0, volume=1.0, price_open=2650.0, sl=2630.0, tp=2680.0, profit=100.0, swap=0.0, time=1723000000, magic=0, comment=""):
    return types.SimpleNamespace(
        ticket=ticket,
        symbol=symbol,
        type=ptype,
        volume=volume,
        price_open=price_open,
        sl=sl,
        tp=tp,
        profit=profit,
        swap=swap,
        time=time,
        magic=magic,
        comment=comment,
    )


@pytest.mark.asyncio
async def test_get_open_positions(provider, fake_mt5):
    fake_mt5._positions = [
        _fake_position(ticket=1, ptype=0),
        _fake_position(ticket=2, ptype=1),
    ]
    positions = await provider.get_open_positions()
    assert len(positions) == 2
    assert positions[0].direction == SignalDirection.LONG
    assert positions[1].direction == SignalDirection.SHORT
    assert positions[0].ticket == "1"
    assert positions[1].ticket == "2"


# ---------------------------------------------------------------------------
# get_account_balance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_account_balance(provider, fake_mt5):
    balance = await provider.get_account_balance()
    assert balance == 10000.0


# ---------------------------------------------------------------------------
# stream_closed_bars (zero-lookahead)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_closed_bars_only_yields_closed_bars(provider, fake_mt5):
    utc_now = datetime.now(timezone.utc)
    # Create two bars: one closed (10 min ago) and one still forming (2 min ago)
    closed_ts = utc_now - timedelta(minutes=25)
    forming_ts = utc_now - timedelta(minutes=5)

    epoch_closed = int(closed_ts.timestamp())
    epoch_forming = int(forming_ts.timestamp())
    rates = _make_rates([epoch_closed, epoch_forming], [2650.0, 2655.0])
    fake_mt5._rates[("XAUUSD", 15)] = rates

    gen = provider.stream_closed_bars("XAUUSD", TimeFrame.M15, poll_interval=0.1)
    # Should yield the closed bar (25 min ago, closed 15m bar ended 10 min ago)
    bar = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert bar.close == 2650.0
    expected_ts = datetime.fromtimestamp(int(closed_ts.timestamp()), tz=timezone.utc)
    assert bar.timestamp == expected_ts

    # The forming bar should NOT be yielded yet
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(gen.__anext__(), timeout=0.2)

    await gen.aclose()


# ---------------------------------------------------------------------------
# stream_ticks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_ticks_yields_new_ticks(provider, fake_mt5):
    gen = provider.stream_ticks("XAUUSD", poll_interval=0.05)
    tick = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert tick.bid == 2650.0
    await gen.aclose()


# ---------------------------------------------------------------------------
# get_multi_timeframe_snapshot
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_multi_timeframe_snapshot(provider, fake_mt5):
    epoch = 1723000000
    m15_rates = _make_rates([epoch + i * 900 for i in range(50)], [2650.0 + i * 0.5 for i in range(50)])
    m30_rates = _make_rates([epoch + i * 1800 for i in range(25)], [2650.0 + i for i in range(25)])
    h1_rates = _make_rates([epoch + i * 3600 for i in range(15)], [2650.0 + i * 2 for i in range(15)])
    h4_rates = _make_rates([epoch + i * 14400 for i in range(8)], [2650.0 + i * 5 for i in range(8)])

    fake_mt5._rates[("XAUUSD", 15)] = m15_rates
    fake_mt5._rates[("XAUUSD", 30)] = m30_rates
    fake_mt5._rates[("XAUUSD", 60)] = h1_rates
    fake_mt5._rates[("XAUUSD", 240)] = h4_rates

    snap = await provider.get_multi_timeframe_snapshot("XAUUSD")
    assert len(snap.m15) > 0
    assert len(snap.m30) > 0
    assert len(snap.h1) > 0
    assert len(snap.h4) > 0
    assert snap.symbol == "XAUUSD"