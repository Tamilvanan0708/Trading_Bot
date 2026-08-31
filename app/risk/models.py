"""
Risk Management Pydantic Models.
"""

from pydantic import BaseModel, Field

from app.core.constants import SignalDirection


class RiskCalculationRequest(BaseModel):
    """Parameters for position sizing calculation."""
    account_balance: float = Field(gt=0)
    risk_percent: float = Field(gt=0, le=10.0)
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit_1: float = Field(gt=0)
    take_profit_2: float = Field(gt=0)
    take_profit_3: float = Field(gt=0)
    direction: SignalDirection
    contract_size: float = 100.0  # 100 troy oz per standard lot for XAU/USD


class PositionSizeResult(BaseModel):
    """Calculated position sizing and risk breakdown."""
    symbol: str = "XAUUSD"
    direction: SignalDirection
    lot_size: float  # e.g., 0.12 lots
    risk_amount_usd: float  # e.g., $100.00
    stop_loss_distance: float  # in dollars/points
    potential_loss_usd: float
    potential_profit_tp1_usd: float
    potential_profit_tp2_usd: float
    potential_profit_tp3_usd: float
    risk_reward_tp1: float
    risk_reward_tp2: float
    risk_reward_tp3: float
    is_valid: bool = True
    rejection_reason: str = ""
