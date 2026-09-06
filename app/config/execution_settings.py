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
    sizing_mode: Literal["fixed", "broker_risk", "pure_risk"] = Field(
        default="broker_risk",
        description="fixed | broker_risk | pure_risk"
    )
    target_risk_usd: float = Field(
        default=10.0,
        ge=1.0,
        le=500.0,
        description="Target dollar risk per trade in USD"
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
        description="Auto-raise L1 SL to entry 0.500 when L2/L3 TP hits"
    )


_CURRENT_SETTINGS: ExecutionSettings | None = None


def get_execution_settings() -> ExecutionSettings:
    """Retrieve current runtime execution settings, loading from file if available."""
    global _CURRENT_SETTINGS
    if _CURRENT_SETTINGS is not None:
        return _CURRENT_SETTINGS

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                _CURRENT_SETTINGS = ExecutionSettings(**data)
                return _CURRENT_SETTINGS
        except Exception:
            pass

    _CURRENT_SETTINGS = ExecutionSettings()
    return _CURRENT_SETTINGS


def save_execution_settings(new_settings: ExecutionSettings) -> ExecutionSettings:
    """Save execution settings to disk and update cache."""
    global _CURRENT_SETTINGS
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(new_settings.model_dump(), f, indent=2)
    _CURRENT_SETTINGS = new_settings
    return _CURRENT_SETTINGS


def calculate_lot_size(
    entry_px: float,
    sl_px: float,
    sizing_mode: str = "broker_risk",
    target_risk_usd: float = 10.0,
    fixed_lot_size: float = 0.01,
    min_lot: float = 0.01,
    max_lot: float = 5.0,
) -> float:
    """
    Calculate lot size based on configured sizing mode:
    - 'fixed': Returns fixed_lot_size (e.g. 0.01).
    - 'broker_risk': Dynamic sizing respecting broker limits (lot step 0.01, min 0.01).
    - 'pure_risk': Exact mathematical fractional lot size.
    """
    mode = (sizing_mode or "broker_risk").lower().strip()
    if mode == "fixed":
        return max(min_lot, round(fixed_lot_size, 2))

    risk_pts = max(0.2, abs(entry_px - sl_px))
    raw_lot = target_risk_usd / (risk_pts * 100.0)

    if mode == "broker_risk":
        # Round to standard broker lot step 0.01 with minimum lot floor
        broker_lot = round(raw_lot, 2)
        broker_lot = max(min_lot, min(max_lot, broker_lot))
        return broker_lot
    elif mode == "pure_risk":
        # Precision lot size (e.g. 0.004)
        return max(0.001, min(max_lot, round(raw_lot, 4)))

    return max(min_lot, round(fixed_lot_size, 2))
