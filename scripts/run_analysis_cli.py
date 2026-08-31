"""
CLI Runner for Live/Historical Market Analysis.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Ensure UTF-8 stdout on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

import asyncio

from app.ai.models import AIValidationResult
from app.data.csv_provider import CsvMarketDataProvider
from app.notifications.formatter import format_telegram_signal
from app.services.pipeline import AnalysisPipeline
from app.signals.models import SignalPayload


async def main():
    print("=" * 70)
    print("XAU/USD Quantitative Multi-Timeframe Analysis Pipeline")
    print("=" * 70)

    provider = CsvMarketDataProvider("data/raw/xauusd_15m_sample.csv")
    pipeline = AnalysisPipeline(provider)

    res = await pipeline.run_full_analysis("XAUUSD")

    print(f"Instrument:     {res['symbol']}")
    print(f"Current Price:  ${res['current_price']:.2f}")
    print(f"Timestamp:      {res['timestamp']}")
    print("-" * 70)
    print(f"4H Macro Bias:  {res['market_bias']['4h']['trend']}")
    print(f"1H Structure:   {res['market_bias']['1h']['trend']}")
    print(f"15M Trigger:    {res['market_bias']['15m']['trend']}")
    print("-" * 70)
    print(f"Confluence:     {res['confluence']['total_score']}/100 ({res['confluence']['quality']})")
    print(f"Signal:         {res['signal']['direction']} ({res['signal']['strategy']})")
    print(f"Entry:          ${res['signal']['entry']:.2f}")
    print(f"Stop Loss:      ${res['signal']['stop_loss']:.2f}")
    print(f"Take Profit:    ${res['signal']['take_profit_2']:.2f} (TP2)")
    print(f"Risk/Reward:    1:{res['signal']['risk_reward']:.2f}")
    print("-" * 70)
    print("[AI Sanity Layer]")
    print(f"Status:         {res['ai_validation']['status']} ({res['ai_validation']['confidence']}%)")
    print(f"Explanation:    {res['ai_validation']['explanation']}")
    print("-" * 70)

    if res['signal']['direction'] != "NO_TRADE":
        sig_obj = SignalPayload(**res['signal'])
        ai_obj = AIValidationResult(**res['ai_validation'])
        print("Telegram Formatted Alert Preview:")
        print(format_telegram_signal(sig_obj, ai_obj))
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
