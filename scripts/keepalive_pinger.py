"""
Render 24/7 Keep-Alive Background Pinger.
Pings the Render web service health endpoint every 4 minutes to prevent
Render's 15-minute free tier idle sleep.
"""

import time
import urllib.request
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")

URL = "https://trading-bot-pt0d.onrender.com/health"
INTERVAL_SECONDS = 240  # 4 minutes (well below Render's 15-minute idle cutoff)

def ping():
    try:
        req = urllib.request.Request(
            URL,
            headers={"User-Agent": "Render-KeepAlive-Bot/1.0", "Accept-Encoding": "gzip"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            logging.info("Pinged %s -> HTTP %s (OK)", URL, resp.status)
    except Exception as exc:
        logging.warning("Keep-alive ping failed: %s", exc)

def main():
    logging.info("Render Keep-Alive Pinger started for %s (interval: %ds)", URL, INTERVAL_SECONDS)
    while True:
        ping()
        time.sleep(INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
