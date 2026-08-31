"""
Custom Domain Exceptions for the Trading Agent System.
"""


class TradingAgentError(Exception):
    """Base exception for all trading agent domain errors."""


class InsufficientDataError(TradingAgentError):
    """Raised when OHLCV history is insufficient for calculations."""


class InvalidTimeframeError(TradingAgentError):
    """Raised when an unsupported timeframe is requested."""


class InvalidRiskParametersError(TradingAgentError):
    """Raised when risk configuration or stop-loss calculations are invalid."""


class SignalGenerationError(TradingAgentError):
    """Raised when signal calculation encounters an unexpected failure."""


class DataProviderError(TradingAgentError):
    """Raised when market data ingestion or retrieval fails."""


class AIValidationError(TradingAgentError):
    """Raised when AI validation fails to process or parse response."""
