"""
CSV Market Data Provider implementation.
"""

import os
from datetime import datetime

import pandas as pd

from app.core.constants import TimeFrame
from app.core.exceptions import DataProviderError, InsufficientDataError
from app.core.logging import logger
from app.data.models import Candle, MultiTimeframeSnapshot
from app.data.provider import MarketDataProvider
from app.data.timeframe_resampler import resample_candles


class CsvMarketDataProvider(MarketDataProvider):
    """
    Market Data Provider backed by local CSV file or pre-loaded DataFrame.
    Expects columns: timestamp/time/Date, open, high, low, close, volume (optional).
    """

    def __init__(self, file_path_or_df: str | pd.DataFrame, default_symbol: str = "XAUUSD", validate: bool = False):
        self.default_symbol = default_symbol
        self.candles_m15: list[Candle] = []

        if isinstance(file_path_or_df, pd.DataFrame):
            self._load_from_df(file_path_or_df, validate)
        elif isinstance(file_path_or_df, str):
            if not os.path.exists(file_path_or_df):
                # Ensure directory exists and create fallback data if missing
                os.makedirs(os.path.dirname(file_path_or_df) or ".", exist_ok=True)
                df = pd.DataFrame([
                    {"timestamp": "2026-08-31 00:00:00", "open": 2750.0, "high": 2755.0, "low": 2748.0, "close": 2752.0, "volume": 100}
                ])
                df.to_csv(file_path_or_df, index=False)
            else:
                df = pd.read_csv(file_path_or_df)
            self._load_from_df(df, validate)
        else:
            raise DataProviderError("Invalid data source provided to CsvMarketDataProvider.")

    def _load_from_df(self, df: pd.DataFrame, validate: bool = False) -> None:
        """Parses and normalizes DataFrame into 15M candles."""
        df_clean = df.copy()
        col_map = {c.lower().strip(): c for c in df_clean.columns}

        # Identify timestamp column
        ts_col = None
        for candidate in ["timestamp", "time", "date", "datetime"]:
            if candidate in col_map:
                ts_col = col_map[candidate]
                break

        if not ts_col:
            raise DataProviderError("CSV must contain a timestamp/time/date column.")

        df_clean["timestamp"] = pd.to_datetime(df_clean[ts_col], utc=True)
        df_clean.sort_values("timestamp", inplace=True)

        candles = []
        for _, row in df_clean.iterrows():
            o = float(row[col_map.get("open", "open")])
            h = float(row[col_map.get("high", "high")])
            l = float(row[col_map.get("low", "low")])
            c = float(row[col_map.get("close", "close")])
            v = float(row[col_map["volume"]]) if "volume" in col_map else 0.0

            # Guard against erroneous data
            h = max(h, o, c, l)
            l = min(l, o, c, h)

            candles.append(
                Candle(
                    timestamp=row["timestamp"].to_pydatetime() if isinstance(row["timestamp"], pd.Timestamp) else row["timestamp"],
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                    volume=v,
                )
            )

        self.candles_m15 = candles

        if validate:
            from app.data.ingestion import validate_candles
            vresult = validate_candles(candles, TimeFrame.M15)
            for w in vresult.warnings:
                logger.warning("CSV data warning: %s", w)

    async def get_latest_price(self, symbol: str = "XAUUSD") -> float:
        if not self.candles_m15:
            raise InsufficientDataError("No market data available.")
        return self.candles_m15[-1].close

    async def get_ohlcv(
        self,
        symbol: str = "XAUUSD",
        timeframe: TimeFrame = TimeFrame.M15,
        limit: int = 200,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Candle]:
        # Resample from the FULL series first, then apply the time window so
        # the first resampled bucket's open reflects the true bucket open
        # (trimming before resampling would corrupt the boundary candle).
        if timeframe != TimeFrame.M15:
            candles = resample_candles(self.candles_m15, timeframe)
        else:
            candles = list(self.candles_m15)

        if start_time:
            candles = [c for c in candles if c.timestamp >= start_time]
        if end_time:
            candles = [c for c in candles if c.timestamp <= end_time]

        return candles[-limit:] if limit > 0 else candles

    async def get_multi_timeframe_snapshot(
        self,
        symbol: str = "XAUUSD",
        as_of_time: datetime | None = None,
        m15_limit: int = 400,
    ) -> MultiTimeframeSnapshot:
        """Constructs an aligned multi-timeframe snapshot as of `as_of_time`."""
        all_m15 = self.candles_m15
        if as_of_time:
            all_m15 = [c for c in all_m15 if c.timestamp <= as_of_time]

        if len(all_m15) < 30:
            raise InsufficientDataError(f"Insufficient historical candles ({len(all_m15)} available, need >= 30).")

        sliced_m15 = all_m15[-m15_limit:]
        latest_candle = sliced_m15[-1]

        # Resample all available historical data up to this point
        m30 = resample_candles(all_m15, TimeFrame.M30)[-200:]
        h1 = resample_candles(all_m15, TimeFrame.H1)[-150:]
        h4 = resample_candles(all_m15, TimeFrame.H4)[-100:]

        return MultiTimeframeSnapshot(
            symbol=symbol,
            timestamp=latest_candle.timestamp,
            current_price=latest_candle.close,
            m15=sliced_m15,
            m30=m30,
            h1=h1,
            h4=h4,
        )
