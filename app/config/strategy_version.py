"""
Deterministic strategy-version derivation.

The live strategy is fully described by its configuration.  Any change to a
strategy-critical setting must produce a DIFFERENT version string so that
signals, paper trades and research outcomes can always be traced back to the
exact strategy configuration that produced them.

This module derives a stable hash from the strategy-critical settings.  It is
NOT a runtime check of code — it is an auditability key.  Bumping
STRATEGY_VERSION_BASE also bumps the version when the strategy logic itself
changes (e.g. a changed confluence formula) without a config change.
"""

import hashlib

from app.config.settings import Settings

# Bump this whenever the *code* of the deterministic strategy engines changes
# in a way that would alter generated signals (not merely reporting).
STRATEGY_VERSION_BASE = "2026.08.24.1"

_STRATEGY_FIELDS = (
    "WEIGHT_HTF_BIAS",
    "WEIGHT_MARKET_STRUCTURE",
    "WEIGHT_SMC_CONFIRMATION",
    "WEIGHT_FIB_CONFIRMATION",
    "WEIGHT_LIQUIDITY",
    "WEIGHT_ENTRY_CONFIRMATION",
    "WEIGHT_RISK_REWARD",
    "THRESHOLD_VERY_STRONG",
    "THRESHOLD_STRONG",
    "THRESHOLD_MODERATE",
    "THRESHOLD_WEAK",
    "MIN_RISK_REWARD",
    "ATR_PERIOD",
    "ATR_FALLBACK",
    "MAX_OPEN_TRADES",
    "BACKTEST_SPREAD_POINTS",
    "BACKTEST_SLIPPAGE_PCT",
    "BACKTEST_TRANSACTION_COST_USD",
    "PAPER_FEES_USD",
    "PAPER_SPREAD_POINTS",
    "PAPER_SLIPPAGE_PCT",
)


def derive_strategy_version(settings: Settings | None = None, mtf_name: str | None = None) -> str:
    """Compute a stable version string for the current strategy configuration.

    Args:
        settings: Current settings (defaults to fresh ``Settings()``).
        mtf_name: Multi-timeframe config name (e.g. ``PROD_4H_1H_30M_15M``).
                  When ``None`` the production default is used.
    """
    s = settings or Settings()
    payload = [STRATEGY_VERSION_BASE]
    payload.append(f"MTF={mtf_name or 'PROD_4H_1H_30M_15M'}")
    for field in _STRATEGY_FIELDS:
        payload.append(f"{field}={getattr(s, field, '')}")
    digest = hashlib.sha256("|".join(payload).encode("utf-8")).hexdigest()[:12]
    return f"{STRATEGY_VERSION_BASE}:{digest}"


def strategy_version_short(version: str) -> str:
    """Short, human-friendly identifier (e.g. for the dashboard)."""
    if ":" in version:
        return version.split(":", 1)[1]
    return version
