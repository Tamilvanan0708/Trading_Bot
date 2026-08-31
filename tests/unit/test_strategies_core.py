"""
Unit tests for Indicators, Market Structure, Fibonacci, and SMC Engines.
"""

import pytest

from app.core.constants import MarketBias, TimeFrame
from app.data.csv_provider import CsvMarketDataProvider
from app.fibonacci.calculator import FibonacciEngine
from app.indicators.atr import calculate_atr
from app.indicators.ema import calculate_ema
from app.indicators.swings import detect_swings
from app.market_structure.detector import MarketStructureDetector
from app.smc.detector import SMCEngine


@pytest.mark.asyncio
async def test_indicators_and_market_structure():
    provider = CsvMarketDataProvider("data/raw/xauusd_15m_sample.csv")
    candles = await provider.get_ohlcv("XAUUSD", TimeFrame.M15, limit=200)

    # ATR & EMA
    atr = calculate_atr(candles)
    assert len(atr) == len(candles)
    assert atr[-1] > 0

    ema50 = calculate_ema(candles, 50)
    assert len(ema50) == len(candles)

    # Swings
    swings = detect_swings(candles)
    assert len(swings) > 0

    # Market Structure
    ms_detector = MarketStructureDetector()
    analysis = ms_detector.analyze(candles, TimeFrame.M15)
    assert analysis.trend in [MarketBias.BULLISH, MarketBias.BEARISH, MarketBias.RANGING, MarketBias.NEUTRAL]
    assert len(analysis.structure_points) > 0


@pytest.mark.asyncio
async def test_fibonacci_engine():
    provider = CsvMarketDataProvider("data/raw/xauusd_15m_sample.csv")
    candles = await provider.get_ohlcv("XAUUSD", TimeFrame.M30, limit=200)

    fib_engine = FibonacciEngine()
    setup = fib_engine.evaluate_setup(candles)

    assert setup is not None
    assert setup.price_range > 0
    assert 0.618 in setup.levels
    assert 0.500 in setup.levels
    assert setup.suggested_entry > 0
    assert setup.suggested_sl > 0


@pytest.mark.asyncio
async def test_smc_engine():
    provider = CsvMarketDataProvider("data/raw/xauusd_15m_sample.csv")
    candles = await provider.get_ohlcv("XAUUSD", TimeFrame.H1, limit=200)

    smc_engine = SMCEngine()
    analysis = smc_engine.analyze(candles, TimeFrame.H1)

    assert analysis.equilibrium_price > 0
    assert analysis.current_zone in ["PREMIUM", "DISCOUNT", "EQUILIBRIUM"]
    assert isinstance(analysis.active_fvgs, list)
    assert isinstance(analysis.active_order_blocks, list)


@pytest.mark.asyncio
async def test_market_structure_configurable_atr_period():
    from app.market_structure.detector import MarketStructureDetector
    provider = CsvMarketDataProvider("data/raw/xauusd_15m_sample.csv")
    candles = await provider.get_ohlcv("XAUUSD", TimeFrame.M15, limit=200)

    detector_short = MarketStructureDetector(atr_period=5)
    detector_long = MarketStructureDetector(atr_period=50)

    analysis_short = detector_short.analyze(candles, TimeFrame.M15)
    analysis_long = detector_long.analyze(candles, TimeFrame.M15)

    assert analysis_short.atr > 0
    assert analysis_long.atr > 0
    assert detector_short.atr_period == 5
    assert detector_long.atr_period == 50


@pytest.mark.asyncio
async def test_bos_choch_detects_breaks():
    from app.smc.bos_choch import detect_bos_choch
    provider = CsvMarketDataProvider("data/raw/xauusd_15m_sample.csv")
    candles = await provider.get_ohlcv("XAUUSD", TimeFrame.M30, limit=200)

    breaks = detect_bos_choch(candles, TimeFrame.M30)
    assert isinstance(breaks, list)
    for b in breaks:
        assert b.break_type.value.startswith(("BOS", "CHOCH"))
        assert b.broken_level > 0
        assert b.break_price > 0
