"""Strategy research and validation framework."""

from app.research.ai_outcomes import ai_outcome_analysis
from app.research.bootstrap import bootstrap_confidence_intervals
from app.research.classification import ClassificationResult, classify_strategy
from app.research.confluence import confluence_bucket_analysis
from app.research.forensics import trade_forensics
from app.research.metrics import compute_extended_metrics
from app.research.monte_carlo import monte_carlo_simulation
from app.research.regime import regime_analysis
from app.research.rmultiple import r_multiple_distribution
from app.research.session import session_analysis
from app.research.walkforward import run_walk_forward, split_windows

__all__ = [
    "ClassificationResult",
    "ai_outcome_analysis",
    "bootstrap_confidence_intervals",
    "classify_strategy",
    "compute_extended_metrics",
    "confluence_bucket_analysis",
    "monte_carlo_simulation",
    "r_multiple_distribution",
    "regime_analysis",
    "run_walk_forward",
    "session_analysis",
    "split_windows",
    "trade_forensics",
]