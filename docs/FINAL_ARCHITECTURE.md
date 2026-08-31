# FINAL ARCHITECTURE — XAU/USD AI Signal Intelligence Terminal

## System Overview

A **signal-only** XAU/USD (gold) intelligence system: live market data →
deterministic multi-timeframe analysis → confluence → signal → AI advisory →
safety gates → Telegram/dashboard → forward-outcome tracking → research.
**No broker execution. Real money permanently disabled. Manual decision only.**

## Layer Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                        FRONTEND (2 routes, 1 app)             │
│   /dashboard ──┐                                              │
│                ├─► shared SPA (app/static/terminal/)          │
│   /terminal ───┘   api.js · ui.js · charts.js · app.js        │
│   /dashboard-legacy (preserved legacy HTML)                   │
└──────────────────────────────────────────────────────────────┘
                          │  HTTP (polling, DASHBOARD_REFRESH_SECONDS)
┌──────────────────────────────────────────────────────────────┐
│                        API LAYER (FastAPI)                    │
│   system · market · analysis · signals · research ·           │
│   paper · terminal · notifications · telegram-status · webhook│
└──────────────────────────────────────────────────────────────┘
                          │
┌──────────────────────────────────────────────────────────────┐
│                     APPLICATION LAYER                         │
│   AnalysisScheduler (singleton, single worker)                │
│     ├─ CandidateObservationService (A/B/C side-by-side)       │
│     ├─ SignalOutcomeTracker (forward MAE/MFE/TP/SL)           │
│     └─ AdmissionGate (data-quality/AI/limits/duplicates)      │
│   AnalysisPipeline · PaperTradingService · RiskManager        │
└──────────────────────────────────────────────────────────────┘
                          │
┌──────────────────────────────────────────────────────────────┐
│                       SIGNAL LAYER                            │
│   SignalEngine (MTF-configurable)                             │
│     ├─ ConfluenceEngine (0-100, weighted, MTF roles)          │
│     ├─ MarketStructureDetector (HH/HL/LH/LL, BOS/CHoCH)       │
│     ├─ SMCEngine (FVG, Order Blocks, Liquidity, zones)        │
│     └─ FibonacciEngine (golden pocket, retracements)          │
│   Strategy candidates: FibStrategy, SMCStrategy, ATR fallback │
└──────────────────────────────────────────────────────────────┘
                          │
┌──────────────────────────────────────────────────────────────┐
│                        DATA LAYER                             │
│   Binance WebSocket (live ticks → forming candles)            │
│   Binance REST history (paginated, backfill, refresh)         │
│   IncrementalResampler (5M/15M/30M/1H/4H, O(n), verified)     │
│   DataQualityValidator (gaps/dup/ooo/stale/invalid)           │
└──────────────────────────────────────────────────────────────┘
                          │
┌──────────────────────────────────────────────────────────────┐
│                     PERSISTENCE / RESEARCH                    │
│   SQLite WAL (signals · outcomes · paper · research ·         │
│               notifications · system_state)                   │
│   Research: walk-forward · Monte Carlo · bootstrap ·          │
│             classification · MTF research · cost robustness · │
│             signal quality · 5M-vs-15M · candidate OOS        │
└──────────────────────────────────────────────────────────────┘
```

## Key Architectural Decisions

1. **Single worker scheduler** — SQLite + singleton scheduler are not
   multi-worker safe; `uvicorn --workers 1` enforced.
2. **Shared frontend** — `/dashboard` and `/terminal` serve the identical SPA
   (`serve_terminal_index()`), guaranteeing parity; no duplicated business
   logic.
3. **Incremental resampling** — O(n) multi-timeframe aggregation verified
   byte-identical to the pandas reference (determinism tests).
4. **Cost-aware research** — realistic round-trip execution costs (spread +
   slippage + commission) are modeled; candidates are ranked on
   cost-adjusted expectancy.
5. **Conservative classification** — pooled OOS metrics (not per-window
   averages); FAILED is never hidden or weakened.
6. **Versioned candidates** — every candidate is immutable once in forward
   observation (CANDIDATE_B_V1, V2, …); outcomes never mix.

## Current Runtime State
- Production strategy: **FAILED** (4H→1H→30M→15M, TP2@2.41R, pooled OOS −0.05R)
- Candidates A/B/C: **OOS_VALIDATED → FORWARD_OBSERVATION**
- Observation mode: **ACTIVE** · Paper trading: **BLOCKED**
- Real-money: **DISABLED** · No broker API exists
