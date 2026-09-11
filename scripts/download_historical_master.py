"""
Download complete historical XAUUSDT 15m and 5m candles from Binance REST API
covering 2025-12-11 to 2026-09-11 (full 9 months / 275 days).
"""

import asyncio
from datetime import datetime, timezone
import json
import os
import httpx

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RESEARCH_DIR = os.path.join(ROOT_DIR, "data", "research")
os.makedirs(RESEARCH_DIR, exist_ok=True)

START_DT = datetime(2025, 12, 11, 0, 0, tzinfo=timezone.utc)
END_DT = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)
START_MS = int(START_DT.timestamp() * 1000)
END_MS = int(END_DT.timestamp() * 1000)


async def download_timeframe(interval: str, output_filenames: list[str]):
    print(f"Downloading {interval} candles from {START_DT} to {END_DT}...")
    url = "https://fapi.binance.com/fapi/v1/klines"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    
    current_start = START_MS
    all_raw = []
    
    async with httpx.AsyncClient(timeout=25.0, headers=headers) as client:
        while current_start < END_MS:
            params = {
                "symbol": "XAUUSDT",
                "interval": interval,
                "startTime": current_start,
                "endTime": END_MS,
                "limit": 1500,
            }
            
            resp = None
            for attempt in range(4):
                try:
                    resp = await client.get(url, params=params)
                    if resp.status_code == 200:
                        break
                    elif resp.status_code == 429:
                        await asyncio.sleep(2.0 * (attempt + 1))
                except Exception as e:
                    await asyncio.sleep(1.0 * (attempt + 1))
            
            if resp is None or resp.status_code != 200:
                print(f"Failed at {current_start}: status {getattr(resp, 'status_code', 'ERR')}")
                break
                
            klines = resp.json()
            if not klines or not isinstance(klines, list):
                break
                
            all_raw.extend(klines)
            last_open = int(klines[-1][0])
            if last_open <= current_start:
                break
            current_start = last_open + 1
            print(f"  [{interval}] Fetched {len(all_raw)} candles so far (latest: {datetime.fromtimestamp(last_open/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')})")
            if len(klines) < 1500:
                break
            await asyncio.sleep(0.08)

    # Parse and clean
    seen_ts = set()
    candles = []
    for k in all_raw:
        ts_ms = int(k[0])
        if ts_ms in seen_ts:
            continue
        seen_ts.add(ts_ms)
        ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        o = float(k[1])
        h = float(k[2])
        l = float(k[3])
        c = float(k[4])
        v = float(k[5]) if len(k) > 5 else 0.0
        h = max(h, o, c)
        l = min(l, o, c)
        if o > 0 and h >= l and l > 0:
            candles.append({
                "timestamp": ts.isoformat(),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            })
            
    candles.sort(key=lambda x: x["timestamp"])
    print(f"[{interval}] Total clean candles: {len(candles)} (from {candles[0]['timestamp']} to {candles[-1]['timestamp']})")
    
    payload = {
        "symbol": "XAUUSD",
        "timeframe": interval,
        "start_dt": candles[0]["timestamp"],
        "end_dt": candles[-1]["timestamp"],
        "count": len(candles),
        "candles": candles,
    }
    
    for fname in output_filenames:
        out_path = os.path.join(RESEARCH_DIR, fname)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        print(f"Saved {fname} ({os.path.getsize(out_path)/1024/1024:.2f} MB)")


async def main():
    await download_timeframe("15m", ["xauusd_15m_real.json", "xauusd_15m_full.json", "cache_xauusdt_15m_20251211_0000_20260911_0000.json"])
    await download_timeframe("5m", ["xauusd_5m_2yr.json", "cache_xauusdt_5m_20251211_0000_20260911_0000.json"])

if __name__ == "__main__":
    asyncio.run(main())
