"""
Runtime Execution & Risk Sizing Settings.

Provides user-configurable toggles for:
- Position Sizing Mode (fixed, broker_risk, pure_risk)
- Target Risk USD per trade
- Fixed Lot Size
- Fib Retracement Active Timeframes (default: 5m, 15m, 30m, 1h)
- Smart Shield Loss Protection
"""

import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field

CONFIG_FILE = Path("data/execution_settings.json")


class ExecutionSettings(BaseModel):
    sizing_mode: Literal["fixed", "broker_risk"] = Field(
        default="broker_risk",
        description="fixed | broker_risk"
    )
    account_currency: Literal["cent", "usd"] = Field(
        default="cent",
        description="cent (USC / ₹ INR) | usd ($ USD)"
    )
    risk_mode: Literal["percent", "fixed_amount"] = Field(
        default="percent",
        description="percent of balance | fixed_amount"
    )
    risk_percent: float = Field(
        default=5.0,
        ge=0.1,
        le=10.0,
        description="Risk percentage of account balance per trade (e.g. 5.0 = 5%)"
    )
    account_balance: float = Field(
        default=10000.0,
        ge=10.0,
        le=10000000.0,
        description="Base capital / balance for risk sizing (₹10,000 in cent mode)"
    )
    target_risk_usd: float = Field(
        default=100.0,
        ge=1.0,
        le=5000.0,
        description="Target dollar/cent risk when risk_mode is fixed_amount"
    )
    fixed_lot_size: float = Field(
        default=0.01,
        ge=0.01,
        le=10.0,
        description="Fixed lot size when sizing_mode is 'fixed'"
    )
    fib_retracement_timeframes: list[str] = Field(
        default=["5m", "15m", "30m", "1h"],
        description="Active timeframes for Fib Retracement strategy (4h excluded)"
    )
    smart_shield_enabled: bool = Field(
        default=True,
        description="Auto-move L1 SL when L2/L3 TP hits"
    )
    smart_shield_level: Literal["0.618", "0.500"] = Field(
        default="0.500",
        description="Smart shield L1 SL target: 0.500 (Buffer with breathing room) or 0.618 (Entry Breakeven)"
    )
    account_leverage: int = Field(
        default=500,
        description="Broker account leverage: 100, 200, 500, 1000, 2000"
    )
    strategy_fib_retracement: bool = Field(
        default=True,
        description="Enable Fib With Retracement strategy execution"
    )
    strategy_smc_fib: bool = Field(
        default=False,
        description="Enable SMC With Fib strategy execution"
    )
    strategy_fib_trend: bool = Field(
        default=False,
        description="Enable Fib Go With Trend strategy execution"
    )
    mt5_bridge_enabled: bool = Field(
        default=True,
        description="Enable live execution to MetaTrader 5 via MQL5 EA bridge"
    )
    mt5_symbol: str = Field(
        default="XAUUSD-VIP",
        description="Broker symbol name for Gold (e.g. XAUUSD-VIP, XAUUSD, XAUUSDm)"
    )
    mt5_magic_number: int = Field(
        default=777888,
        description="Magic number for MT5 orders"
    )
    mt5_allowed_strategy: str = Field(
        default="Fib Retracement",
        description="Strictly allowed strategy for MT5 execution (only Fib Retracement)"
    )
    min_impulse_filter_enabled: bool = Field(
        default=False,
        description="Filter out micro sideways chop when setup impulse range is below threshold"
    )
    trend_filter_enabled: bool = Field(
        default=False,
        description="Filter counter-trend setups against macro trend alignment (default False for pure BOS Fib)"
    )
    cross_tf_dedup_enabled: bool = Field(
        default=False,
        description="De-duplicate concurrent trades across different timeframes with close entry & SL"
    )
    fib_engine_mode: Literal["classic", "experimental"] = Field(
        default="classic",
        description="classic (Sept 8 proven anchor + rolling span + continuation BOS) | experimental (macro locked origin)"
    )
    higher_tf_l1_only: bool = Field(
        default=True,
        description="When True, 15M/30M/1H use L1 only (no L2/L3 layers). 5M keeps all 3 layers."
    )
    higher_tf_l1_only_timeframes: list[str] = Field(
        default=["15m", "30m", "1h"],
        description="Timeframes where only L1 is allowed (L2/L3 disabled). Default: 15m, 30m, 1h."
    )
    spread_filter_enabled: bool = Field(
        default=True,
        description="Block MT5 orders when live spread exceeds max_spread_points"
    )
    max_spread_points: float = Field(
        default=2.0,
        ge=0.1,
        le=10.0,
        description="Maximum allowed spread in points. Orders blocked when spread exceeds this value."
    )
    chase_filter_enabled: bool = Field(
        default=True,
        description="Block MT5 orders if live price has drifted too far away from the intended Fib entry level"
    )
    max_chase_points: float = Field(
        default=2.0,
        ge=0.5,
        le=10.0,
        description="Maximum allowed distance in points from intended Fib entry. Orders blocked if gap exceeds this."
    )


_CURRENT_SETTINGS: ExecutionSettings | None = None
_LAST_CONFIG_MTIME: float = 0.0


def get_execution_settings() -> ExecutionSettings:
    """Retrieve current runtime execution settings, loading from file if available with mtime reload."""
    global _CURRENT_SETTINGS, _LAST_CONFIG_MTIME

    if CONFIG_FILE.exists():
        try:
            mtime = CONFIG_FILE.stat().st_mtime
            if _CURRENT_SETTINGS is not None and mtime == _LAST_CONFIG_MTIME:
                return _CURRENT_SETTINGS
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("smart_shield_level") == "0.618":
                    data["smart_shield_level"] = "0.500"
                    try:
                        with open(CONFIG_FILE, "w", encoding="utf-8") as fw:
                            json.dump(data, fw, indent=2)
                    except Exception:
                        pass
                _CURRENT_SETTINGS = ExecutionSettings(**data)
                _LAST_CONFIG_MTIME = CONFIG_FILE.stat().st_mtime
                return _CURRENT_SETTINGS
        except Exception:
            pass

    if _CURRENT_SETTINGS is not None:
        return _CURRENT_SETTINGS

    _CURRENT_SETTINGS = ExecutionSettings()
    return _CURRENT_SETTINGS


def save_execution_settings(new_settings: ExecutionSettings) -> ExecutionSettings:
    """Save execution settings to disk and update cache."""
    global _CURRENT_SETTINGS, _LAST_CONFIG_MTIME
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(new_settings.model_dump(), f, indent=2)
    _CURRENT_SETTINGS = new_settings
    if CONFIG_FILE.exists():
        try:
            _LAST_CONFIG_MTIME = CONFIG_FILE.stat().st_mtime
        except Exception:
            pass
    return _CURRENT_SETTINGS


def calculate_lot_size(
    entry_px: float,
    sl_px: float,
    sizing_mode: str = "broker_risk",
    target_risk_usd: float = 100.0,
    fixed_lot_size: float = 0.01,
    min_lot: float = 0.01,
    max_lot: float = 0.50,
    risk_mode: str = "percent",
    risk_percent: float = 1.0,
    account_balance: float = 10000.0,
    account_currency: str = "cent",
) -> float:
    """
    Calculate lot size based on configured sizing mode:
    - 'fixed': Returns fixed_lot_size (e.g. 0.01).
    - 'broker_risk': Dynamic sizing respecting broker limits (lot step 0.01, min 0.01, max 0.50).
      If risk_mode == 'percent', scales dynamically with account_balance * (risk_percent / 100.0).
    """
    mode = (sizing_mode or "broker_risk").lower().strip()
    if mode == "fixed":
        return max(min_lot, round(fixed_lot_size, 2))

    # Calculate effective risk amount in base units (₹ / $ / Cents)
    if risk_mode == "percent":
        effective_risk = max(1.0, account_balance * (risk_percent / 100.0))
    else:
        effective_risk = max(1.0, target_risk_usd)

    # Minimum SL calculation floor: at least 2.0 points to protect against runaway lots on tight wicks
    risk_pts = max(2.0, abs(entry_px - sl_px))
    raw_lot = effective_risk / (risk_pts * 100.0)

    # broker_risk: Round to standard broker lot step 0.01 with minimum floor 0.01 and safety cap 0.50
    broker_lot = round(raw_lot, 2)
    return max(min_lot, min(max_lot, broker_lot))


def calculate_margin_required(
    lot_size: float,
    gold_price: float = 4435.0,
    leverage: int = 500,
    account_currency: str = "cent",
) -> float:
    """
    Calculate required margin for gold (XAUUSD):
    1 Standard Lot = 100 oz of Gold.
    Notional Value = lot_size * 100 * gold_price.
    Margin = Notional Value / leverage.
    """
    notional = max(0.01, lot_size) * 100.0 * max(100.0, gold_price)
    lev = max(1, leverage)
    margin = notional / lev
    return round(margin, 2)

