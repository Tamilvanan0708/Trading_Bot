"""
Sync recent Binance historical candles to Render live server.
Bypasses cloud IP bans on Binance REST API by pushing clean residential-fetched candles.
"""

import urllib.request
import json
import time

RENDER_URL = "https://trading-bot-pt0d.onrender.com"

def fetch_binance(interval: str, limit: int):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol=XAUUSDT&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        rows = json.loads(resp.read().decode("utf-8"))
    return [
        {
            "time": int(r[0] / 1000),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
        }
        for r in rows
    ]

def ingest(timeframe: str, candles: list):
    url = f"{RENDER_URL}/market/candles/ingest"
    payload = json.dumps({
        "symbol": "XAUUSD",
        "timeframe": timeframe,
        "candles": candles,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def reset_retracement():
    url = f"{RENDER_URL}/retracement/reset"
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}

def get_5m_status():
    url = f"{RENDER_URL}/retracement/strategy/fib-retracement/XAUUSD?timeframe=5m"
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode("utf-8"))

def run_sync():
    print("1. Fetching 120 5M candles from Binance...")
    c5 = fetch_binance("5m", 120)
    print(f"   Fetched {len(c5)} 5M candles. First: {time.ctime(c5[0]['time'])}, Last: {time.ctime(c5[-1]['time'])}")

    print("2. Fetching 100 15M candles from Binance...")
    c15 = fetch_binance("15m", 100)
    print(f"   Fetched {len(c15)} 15M candles. First: {time.ctime(c15[0]['time'])}, Last: {time.ctime(c15[-1]['time'])}")

    print("\n3. Ingesting 5M candles into Render...")
    r5 = ingest("5m", c5)
    print(f"   Render response: {r5}")

    print("4. Ingesting 15M candles into Render...")
    r15 = ingest("15m", c15)
    print(f"   Render response: {r15}")

    print("\n5. Checking 5M status on Render...")
    status = get_5m_status()
    tf5 = status.get("timeframes", {}).get("5m", {})
    print(f"   5M State: {tf5.get('state')}")
    print(f"   Direction: {tf5.get('direction')}")
    print(f"   Entry: {tf5.get('entry', {}).get('price')}")
    print(f"   SL: {tf5.get('sl', {}).get('price')}")
    print(f"   TP: {tf5.get('tp', {}).get('dynamic')}")
    print(f"   Live Price: {status.get('live_price')}")

def main():
    import sys
    if "--loop" in sys.argv:
        print("Starting continuous candle sync daemon (every 15s)...")
        while True:
            try:
                run_sync()
            except Exception as e:
                print("Sync error:", e)
            time.sleep(15)
    else:
        run_sync()

if __name__ == "__main__":
    main()
