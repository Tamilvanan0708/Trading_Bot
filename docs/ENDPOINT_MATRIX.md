# ENDPOINT MATRIX — XAU/USD AI Signal Intelligence Terminal

Maps every frontend page → API call → backend service → data source → error handling.

## Legend
- **Freq:** polling interval (DASHBOARD_REFRESH_SECONDS, default 30s)
- **Offline:** what the UI displays when the endpoint errors

## 1. Overview (Command Center)
| View | API call | Service | Source | Freq | Offline |
|---|---|---|---|---|---|
| Live price | `/market/system-status` | `Scheduler.live_service` | WS-tick + REST | 30s | "—" |
| Signal hero | `/analysis/live/{symbol}` | `AnalysisPipeline` | engine + confluence | 30s | "NO DATA" |
| Confluence | `/analysis/live/{symbol}` | `ConfluenceEngine` | same response | 30s | "—" |
| MTF matrix | `/analysis/live/{symbol}` | engine → `market_bias` | same response | 30s | "—" |
| Feed health | `/market/live/health` | `LiveMarketDataService` | WS registry | 30s | "OFFLINE" |
| Data quality | `/market/data-quality` | `DataQualityValidator` | candle history | 30s | "DEGRADED" |

## 2. Live Market
| View | API call | Service | Source | Freq | Offline |
|---|---|---|---|---|---|
| Price/timestamp | `/market/{symbol}/live` | `LiveMarketDataService` | WS + REST | 30s | "WAITING" |
| Candlestick chart | `/market/{symbol}/live` | `LiveMarketDataService` | closed candles | 30s | "WAITING" |
| Levels | `/analysis/live/{symbol}` | `SignalEngine` | signal | 30s | "—" |

## 3. Signals
| View | API call | Service | Source | Freq | Offline |
|---|---|---|---|---|---|
| List | `/signals?limit=N` | `Repository` | signals table | 30s | "No signals" |
| Detail drawer | `/signals/{id}` | `Repository` | signals + ai | click | "Error" |

## 4. Market Structure
| View | API call | Service | Source | Freq | Offline |
|---|---|---|---|---|---|
| Structure | `/structure/{symbol}` | `MarketStructureDetector` | live snapshot | 30s | "No data" |

## 5. SMC Intelligence
| View | API call | Service | Source | Freq | Offline |
|---|---|---|---|---|---|
| SMC | `/smc/{symbol}` | `SMCEngine` | live snapshot | 30s | "No data" |

## 6. Fibonacci
| View | API call | Service | Source | Freq | Offline |
|---|---|---|---|---|---|
| Fib | `/fibonacci/{symbol}` | `FibonacciEngine` | live snapshot | 30s | "No data" |

## 7. AI Validation
| View | API call | Service | Source | Freq | Offline |
|---|---|---|---|---|---|
| AI status | `/analysis/live/{symbol}` | `AIValidator` | analysis response | 30s | "PENDING" |

## 8. Candidates
| View | API call | Service | Source | Freq | Offline |
|---|---|---|---|---|---|
| Summary | `/research/candidates` | `Repository` | signals table | 30s | "No candidates" |
| Ranking | `/research/mtf-report` | MTF research | cached report | 30s | "No data" |

## 9. Research
| View | API call | Service | Source | Freq | Offline |
|---|---|---|---|---|---|
| Grade/metrics | `/research/classification` | `classify_strategy()` | OOS/mc/boot | 30s | "INCONCLUSIVE" |
| Candidates | `/research/mtf-report` | MTF research | cached report | 30s | "No data" |

## 10. Forward Observation
| View | API call | Service | Source | Freq | Offline |
|---|---|---|---|---|---|
| Stats | `/research/signal-outcomes` | `Repository` | signals table | 30s | "No data" |
| Progress | `/research/observation` | `ObservationStore` | JSON store | 30s | "No data" |

## 11. Paper Trading
| View | API call | Service | Source | Freq | Offline |
|---|---|---|---|---|---|
| Account | `/performance/account` | `PaperTradingService` | DB | 30s | "—" |
| Trades | `/paper-trades` | `Repository` | paper_trades table | 30s | "No trades" |
| Safety | `/market/system-status` | settings + grade | env + report | 30s | "BLOCKED" |

## 12. Notifications
| View | API call | Service | Source | Freq | Offline |
|---|---|---|---|---|---|
| History | `/notifications?limit=50` | `Repository` | notification_logs | 30s | "No notifications" |
| Telegram | `/telegram/status` | settings | env | 30s | "DISABLED" |

## 13. System Health
| View | API call | Service | Source | Freq | Offline |
|---|---|---|---|---|---|
| All cards | `/market/system-status` | multi-service | multi | 30s | "DOWN" |
| Feed | `/market/live/health` | `LiveMarketDataService` | WS | 30s | "DISCONNECTED" |
| Data quality | `/market/data-quality` | validator | history | 30s | "DEGRADED" |

## 14. Settings
| View | API call | Service | Source | Freq | Offline |
|---|---|---|---|---|---|
| Safety | `/market/system-status` | settings | env | 30s | "—" |
| Telegram | `/telegram/status` | settings | env | 30s | "DISABLED" |

## Dashboard / Terminal parity
Both `/dashboard` and `/terminal` serve the identical SPA (`serve_terminal_index()`).
All API calls above are made by the shared `api.js`. Verified: identical HTML
content, same asset references, same refresh injection.