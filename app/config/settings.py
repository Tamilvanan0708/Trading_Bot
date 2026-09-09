"""
Application Settings & Configuration.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # App Environment
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Market Configuration
    DEFAULT_SYMBOL: str = "XAUUSD"
    LOT_CONTRACT_SIZE: float = 100.0  # 100 troy oz per lot
    PIP_SIZE: float = 0.1             # $0.10 per pip in XAU/USD (0.01 per point)

    # Risk Management Configuration
    RISK_PERCENT: float = Field(default=1.0, ge=0.1, le=10.0)
    MAX_RISK_PERCENT: float = Field(default=3.0, ge=0.5, le=10.0)
    MAX_OPEN_TRADES: int = Field(default=3, ge=1, le=10)
    ACCOUNT_BALANCE: float = Field(default=10000.0, ge=100.0)
    MIN_RISK_REWARD: float = Field(default=1.5, ge=1.0)

    # Position Sizing (broker contract specifications)
    MIN_LOT: float = Field(default=0.01, gt=0)
    MAX_LOT: float = Field(default=100.0, gt=0)
    LOT_STEP: float = Field(default=0.01, gt=0)

    # ATR & Volatility Configuration
    ATR_PERIOD: int = Field(default=14, ge=2, le=100)
    ATR_FALLBACK: float = Field(default=2.5, gt=0)

    # Backtesting Configuration
    BACKTEST_SPREAD_POINTS: float = Field(default=0.5, ge=0, description="Spread in price points (e.g., 0.5 = $0.50)")
    BACKTEST_SLIPPAGE_PCT: float = Field(default=0.0001, ge=0, le=1.0, description="Slippage as % of price (0.01% = $0.40 on gold)")
    BACKTEST_TRANSACTION_COST_USD: float = Field(default=0.0, ge=0, description="Fixed cost per trade in USD")
    BACKTEST_ENTRY_ON_NEXT_OPEN: bool = Field(default=False, description="If True, entries execute at next bar's open instead of signal bar's close")

    # Confluence Scoring Weights (Total = 100)
    WEIGHT_HTF_BIAS: int = 20
    WEIGHT_MARKET_STRUCTURE: int = 20
    WEIGHT_SMC_CONFIRMATION: int = 20
    WEIGHT_FIB_CONFIRMATION: int = 15
    WEIGHT_LIQUIDITY: int = 10
    WEIGHT_ENTRY_CONFIRMATION: int = 10
    WEIGHT_RISK_REWARD: int = 5

    # Confluence Signal Thresholds
    THRESHOLD_VERY_STRONG: int = 90
    THRESHOLD_STRONG: int = 75
    THRESHOLD_MODERATE: int = 60
    THRESHOLD_WEAK: int = 40

    # AI Validation — multi-provider with free-provider fallback
    # AI_PROVIDER: "mock" (heuristic only) | "auto" (chain) | "groq" | "gemini"
    #              | "openrouter" | "ollama" | "bai"    # AI Provider Selection
    AI_PROVIDER: Literal["mock", "auto", "groq", "gemini", "openrouter", "ollama", "bai", "openai"] = "auto"
    AI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    AI_API_BASE_URL: str = ""
    AI_MODEL: str = "gpt-4o-mini"
    AI_TEMPERATURE: float = Field(default=0.1, ge=0.0, le=2.0)
    AI_REQUEST_TIMEOUT_SECONDS: float = Field(default=15.0, gt=0)
    AI_MAX_RETRIES: int = Field(default=1, ge=0, le=5)
    AI_RETRY_DELAY: float = Field(default=1.0, ge=0)

    # Groq (primary free provider) — load strictly from environment / .env
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "openai/gpt-oss-120b"

    # Google Gemini (secondary fallback) — load strictly from environment / .env
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # OpenRouter (tertiary fallback)
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "deepseek/deepseek-r1-distill-qwen-32b:free"

    # Local Ollama (final AI fallback)
    OLLAMA_BASE_URL: str = ""
    OLLAMA_MODEL: str = "llama3.2:3b"

    # B.AI (optional OpenAI-compatible provider)
    # NOTE: B.AI does NOT offer `gpt-4o-mini`.  Valid OpenAI-family model IDs
    # are GPT-5.x (e.g. `gpt-5-4-mini`).  Using an unsupported model name
    # makes B.AI return HTTP 400 "Invalid request body".
    BAI_API_KEY: str = ""
    BAI_MODEL: str = "gpt-5-4-mini"
    BAI_BASE_URL: str = "https://api.b.ai/v1"

    # Telegram — load strictly from environment / .env
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_ENABLED: bool = True

    # Paper Trading
    PAPER_TRADING_ENABLED: bool = True
    AUTO_TRADE_ON_CAUTION: bool = False  # Open paper trade on AI CAUTION (not only APPROVE)
    PAPER_FEES_USD: float = Field(default=0.0, ge=0, description="Fixed fee per paper trade in USD")
    PAPER_SPREAD_POINTS: float = Field(default=0.5, ge=0, description="Spread applied to paper fills (price points)")
    PAPER_SLIPPAGE_PCT: float = Field(default=0.0001, ge=0, le=1.0, description="Slippage as % of price (0.01% = $0.40 on gold)")

    # Safety: observation mode / failed-strategy block
    OBSERVATION_MODE: bool = Field(default=False, description="Run analysis and signals but NEVER open paper trades; store hypothetical outcomes.")
    BLOCK_PAPER_TRADING_ON_FAILED_STRATEGY: bool = Field(default=True, description="Block auto paper trading while the research classification is FAILED.")

    # Overtrading / Duplicate Protection
    MAX_OPEN_POSITIONS: int = Field(default=3, ge=1, le=20)
    MAX_DAILY_TRADES: int = Field(default=10, ge=0, description="0 = unlimited")
    MAX_DAILY_LOSS_PCT: float = Field(default=3.0, ge=0, le=100, description="0 = unlimited; halt new trades after daily loss %")
    MAX_CONSECUTIVE_LOSSES: int = Field(default=5, ge=0, description="0 = unlimited; pause new trades after N consecutive losses")
    MAX_TOTAL_DRAWDOWN_PCT: float = Field(default=30.0, ge=0, le=100, description="Halt new trades when total account drawdown from starting balance reaches this % (0 = unlimited)")

    # Research signal limits (signal-only system): even though no real trades
    # are placed, limit daily signal output to avoid alert/notification fatigue.
    MAX_SIGNALS_PER_DAY: int = Field(default=0, ge=0, description="0 = unlimited; cap tradable signals per UTC day")
    COOLDOWN_AFTER_LOSS_MINUTES: int = Field(default=0, ge=0, description="0 = off; suppress new signals N minutes after a losing outcome")
    CANDIDATE_TELEGRAM_ALERTS_ENABLED: bool = Field(default=False, description="Send Telegram alerts for forward-observation candidate signals (A/B/C).")

    # Analysis Scheduler
    ANALYSIS_OFFSET_SECONDS: int = Field(default=10, ge=0, le=120, description="Delay after candle close before analysis runs")
    PROCESS_LAST_CLOSED_ON_START: bool = Field(default=False, description="If True, analyze the most recent closed candle on startup")
    SCHEDULER_POLL_INTERVAL: int = Field(default=15, ge=1, le=60, description="Scheduler wake-up poll interval in seconds")
    MARKET_HOURS_UTC_ENABLED: bool = Field(default=True, description="Skip scheduler analysis when the XAUUSDT market is closed (weekend)")

    # Market Regime Filter
    REGIME_FILTER_ENABLED: bool = Field(default=False, description="Reject signals when market regime is unsuitable")
    ALLOWED_REGIMES: str = Field(default="TRENDING", description="Comma-separated allowed regimes (TRENDING, RANGING)")

    # News Risk Filter
    NEWS_FILTER_ENABLED: bool = Field(default=False)
    NEWS_PROVIDER: str = Field(default="none", description="none | forexfactory | generic_calendar")
    NEWS_BLACKOUT_BEFORE_MINUTES: int = Field(default=30, ge=0)
    NEWS_BLACKOUT_AFTER_MINUTES: int = Field(default=30, ge=0)

    # MetaTrader 5 Bridge
    MT5_ENABLED: bool = False
    MT5_LOGIN: int = 0
    MT5_SERVER: str = ""
    MT5_PASSWORD: str = ""
    MT5_MAGIC: int = 0
    MT5_SYMBOL: str = "XAUUSD"
    MT5_TZ_OFFSET_MINUTES: int = 0

    # Live Market Feed Configuration
    LIVE_FEED_PROVIDER: Literal["mock", "binance", "ctrader", "tradingview", "mt5"] = "binance"
    BINANCE_WS_URL: str = "wss://fstream.binance.com/stream"
    BINANCE_REST_BASE_URL: str = "https://fapi.binance.com"
    BINANCE_HISTORY_TIMEOUT_SECONDS: float = Field(default=15.0, gt=0)
    BINANCE_HISTORY_MAX_RETRIES: int = Field(default=3, ge=0, le=10)
    BINANCE_HISTORY_RETRY_BACKOFF: float = Field(default=2.0, gt=0)
    LIVE_HISTORY_MAX_AGE_MINUTES: int = Field(default=30, ge=1, le=1440)
    LIVE_HISTORY_MIN_CANDLES: int = Field(default=50, ge=1, le=2000)
    MAX_CANDLE_GAP_COUNT: int = Field(default=5, ge=0, description="Gap count above which data quality is degraded")
    LIVE_HISTORY_REFRESH_INTERVAL_MINUTES: int = Field(default=15, ge=1, le=1440)
    LIVE_HISTORY_REFRESH_ON_DEGRADED: bool = Field(default=True)
    LIVE_HISTORY_REFRESH_LOOKBACK_HOURS: int = Field(default=48, ge=1, le=720)
    LIVE_HISTORY_EMERGENCY_REFRESH_COOLDOWN_SECONDS: int = Field(default=60, ge=5)
    CTRADER_WS_URL: str = "wss://connect.spotware.com/apps/"
    CTRADER_ACCESS_TOKEN: str = ""
    CTRADER_ACCOUNT_ID: int = 0
    CTRADER_SYMBOL_ID: int = 0
    TICK_RING_BUFFER_CAPACITY: int = 10000
    ANALYSIS_INTERVAL_SECONDS: int = Field(default=300, ge=15, description="Live analysis cycle interval")
    DASHBOARD_REFRESH_SECONDS: int = Field(default=30, ge=5, description="Dashboard auto-refresh interval")

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/xauusd_agent.db"

    # Security
    CORS_ALLOW_ORIGINS: list[str] = Field(
        default_factory=lambda: ["http://localhost:8000", "http://127.0.0.1:8000"],
        description="Allowed CORS origins. Set to [\"*\"] only for non-authenticated local/LAN use.",
    )
    # Absolute safety: real-money execution is permanently disabled.
    # There is NO broker order API in this codebase; this flag is an
    # explicit, auditable declaration and is always False.
    REAL_MONEY_EXECUTION: bool = Field(default=False, description="MUST remain False. Real-money execution is permanently disabled.")


@lru_cache
def get_settings() -> Settings:
    """Returns cached singleton application settings."""
    return Settings()
