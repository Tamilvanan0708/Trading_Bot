# LIVE MARKET TIMEFRAME AUDIT — XAU/USD AI Signal Intelligence Terminal

## Root Cause
The Live Market page's timeframe buttons (5M/15M/30M/1H/4H) were visual only.
Two defects caused the chart not to change:

1. **Frontend: no click handler on timeframe buttons.** The buttons rendered
   with `data-tf` attributes but had no event listener, so clicking only toggled
   the CSS class — no data request, no redraw.

2. **Frontend: chart always read hardcoded `m.m15`.** The initial chart
   population used `const series = m.m15 || []` regardless of the selected
   timeframe, and the `redraw()` function read `dataset.candles` which was set
   once from 15M.

3. **Backend: `/market/{symbol}/live` returned only candle COUNTS**
   (`m15_count`, `m30_count`, …), not the actual candle arrays, and accepted no
   `timeframe` query parameter. The frontend had no candle series to render for
   any timeframe, and no way to request a specific one.

## Affected Files
- `app/api/routes/market.py` — live-market endpoint (counts only; no timeframe)
- `app/static/terminal/js/app.js` — `/live` view (no click handlers; hardcoded m15)
- `app/static/terminal/js/api.js` — `liveMarket()` (no params support)

## Why It Happened
The live-market endpoint was implemented to return the multi-timeframe snapshot
*costs* (counts) for status display, and the frontend chart was wired to the
default 15M series as a placeholder. Timeframe switching was never connected
end-to-end.

## Fix
1. **Backend:** `/market/{symbol}/live?timeframe=5m|15m|30m|1h|4h` now validates
   the timeframe (400 on invalid) and returns the real candle series for the
   requested timeframe (up to 200 candles, OHLCV, `data_status`).
2. **Frontend:** timeframe buttons are wired with click handlers that set the
   active timeframe, request the correct data via `API.liveMarket(sym,{timeframe})`,
   and redraw the canvas chart. A request-sequence id (`state.seq`) discards
   stale responses when switching timeframes rapidly. The selected timeframe
   persists in UI state and refreshes on the dashboard polling interval.

## Tests Added
`tests/unit/test_live_market_timeframe.py` (9 passed, 2 skipped):
- 5m / 15m / 30m / 1h / 4h each return the corresponding data
- Invalid timeframe → 400
- Default timeframe = 15m
- OHLC integrity (high ≥ open/close, low ≤ open/close, non-negative volume)
- Different timeframes return different candle counts
- Frontend `app.js` uses `loadTF`, `data-tf`, click handlers, and a stale-response
  guard (`seq`)
- Frontend `api.js` supports the timeframe param

## Data Integrity
- Strategy/backtest/candidate/outcome logic continues to use **closed candles
  only**; the Live Market chart may show the forming candle via
  `include_forming` (explicitly labeled) — never used in analysis.
- Candles come from the live `_closed_15m` series resampled by the verified
  `IncrementalResampler` (deterministic, UTC-aligned).
- When the feed/history is unavailable the endpoint returns an explicit
  `404 / NO_DATA` state (never fake candles).

## Verification
- Full suite: **323 passed / 2 skipped / 0 failed**
- `99m` → HTTP 400 (validation)
- Live feed currently disconnected (transient network to Binance); endpoint
  correctly returns NO_DATA — it will return real candles when the feed
  reconnects and backfills (proven earlier: 800+ closed candles, gaps=0).
- Dashboard == Terminal (identical shared SPA, parity verified).
