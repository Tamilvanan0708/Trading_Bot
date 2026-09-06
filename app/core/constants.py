"""
System Constants, Enums and Domain Definitions.
"""

from enum import Enum


class TimeFrame(str, Enum):
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H2 = "2h"
    H4 = "4h"
    D1 = "1d"


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"


class StrategyType(str, Enum):
    FIBONACCI = "FIBONACCI_RETRACEMENT"
    SMC = "SMART_MONEY_CONCEPTS"
    CONFLUENCE = "MULTI_TF_CONFLUENCE"


class MarketBias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    RANGING = "RANGING"


class SignalQuality(str, Enum):
    NO_TRADE = "NO_TRADE"
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"
    VERY_STRONG = "VERY_STRONG"


class TradeState(str, Enum):
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    PENDING = "PENDING"
    ENTRY_HIT = "ENTRY_HIT"
    TP1_HIT = "TP1_HIT"
    TP2_HIT = "TP2_HIT"
    TP3_HIT = "TP3_HIT"
    STOP_LOSS_HIT = "STOP_LOSS_HIT"
    INVALIDATED = "INVALIDATED"
    CLOSED = "CLOSED"


class AIValidationStatus(str, Enum):
    APPROVE = "APPROVE"
    CAUTION = "CAUTION"
    REJECT = "REJECT"
    UNAVAILABLE = "UNAVAILABLE"


class StructureType(str, Enum):
    HIGHER_HIGH = "HH"
    HIGHER_LOW = "HL"
    LOWER_HIGH = "LH"
    LOWER_LOW = "LL"
    BOS_BULLISH = "BOS_BULLISH"
    BOS_BEARISH = "BOS_BEARISH"
    CHOCH_BULLISH = "CHOCH_BULLISH"
    CHOCH_BEARISH = "CHOCH_BEARISH"


class LiquidityType(str, Enum):
    BUY_SIDE = "BUY_SIDE_LIQUIDITY"
    SELL_SIDE = "SELL_SIDE_LIQUIDITY"
    EQUAL_HIGHS = "EQUAL_HIGHS"
    EQUAL_LOWS = "EQUAL_LOWS"


class ZoneType(str, Enum):
    PREMIUM = "PREMIUM"
    DISCOUNT = "DISCOUNT"
    EQUILIBRIUM = "EQUILIBRIUM"
