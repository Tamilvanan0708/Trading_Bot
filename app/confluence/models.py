"""
Confluence Scoring Models and Breakdown Data Structures.
"""

from pydantic import BaseModel, Field

from app.core.constants import SignalDirection, SignalQuality


class ConfluenceItem(BaseModel):
    """Component score item with description."""
    category: str
    points_awarded: float
    max_points: float
    passed: bool
    details: str


class ConfluenceBreakdown(BaseModel):
    """Detailed category-by-category score report."""
    htf_bias: ConfluenceItem
    market_structure: ConfluenceItem
    smc_confirmation: ConfluenceItem
    fib_confirmation: ConfluenceItem
    liquidity_confirmation: ConfluenceItem
    entry_confirmation: ConfluenceItem
    risk_reward: ConfluenceItem


class ConfluenceScore(BaseModel):
    """Complete aggregated confluence assessment."""
    total_score: float = Field(ge=0.0, le=100.0)
    quality: SignalQuality
    direction: SignalDirection
    is_tradable: bool
    breakdown: ConfluenceBreakdown
    reasons: list[str]
    conflicts: list[str]
