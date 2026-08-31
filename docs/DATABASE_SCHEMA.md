# DATABASE SCHEMA — XAU/USD AI Signal Intelligence Terminal

SQLite (WAL mode) via async SQLAlchemy. PRAGMAs: `journal_mode=WAL`,
`busy_timeout=5000`, `foreign_keys=ON`. Persisted under `data/xauusd_agent.db`.

## Tables

### signals
| Column | Type | Notes |
|---|---|---|
| id | String(36) PK | signal_id |
| created_at | DateTime idx | signal timestamp (UTC) |
| symbol | String(20) idx | XAUUSD |
| strategy | String(50) | CONFLUENCE |
| strategy_version | String(64) idx | e.g. `2026.08.24.1:hash` or `CANDIDATE_B_V1` |
| direction | String(10) | LONG/SHORT/NO_TRADE |
| timeframe | String(10) | 15m / 5m |
| entry_price | Float | null for NO_TRADE |
| stop_loss / take_profit_1..3 | Float | null for NO_TRADE |
| risk_reward | Float | R:R |
| confidence_score | Float | confluence |
| signal_quality | String(20) | VERY_STRONG…NO_TRADE |
| market_bias | String(20) | BULLISH/BEARISH/NEUTRAL |
| reasons | JSON | confluence reasons |
| invalidation_conditions | JSON | |
| metadata_payload | JSON | regime, session, HTF bias, smc, fib, admission, candidate |
| outcome | String(20) idx | OPEN/TP1/TP2/TP3/SL/EXPIRED |
| max_favorable_excursion_r / max_adverse_excursion_r | Float | MFE/MAE in R |
| tp1_hit/tp2_hit/tp3_hit/sl_hit | Boolean | |
| time_to_outcome_hours | Float | |
| max_r_achieved / final_r | Float | |
| regime / session | String(20) | |
| outcome_updated_at | DateTime | |

### ai_validations
`id`, `created_at`, `signal_id (FK signals, cascade)`, `status`, `confidence`,
`explanation`, `identified_risks (JSON)`, `missing_confirmations (JSON)`.

### paper_trades
`id`, `created_at`, `opened_at`, `signal_id (FK)`, `direction`, `entry_price`,
`stop_loss`, `take_profit_1..3`, `status` (state machine), `exit_price`,
`exit_reason`, `pnl_usd`, `pnl_r`, `fees_usd`, `spread_points`,
`slippage_pct`, `account_balance_at_open/close`.

### backtest_runs
`id`, `created_at`, `symbol`, `start/end`, `initial_balance`, `final_balance`,
`total_trades`, `win_rate`, `profit_factor`, `expectancy_r`, `max_drawdown_pct`,
`trades (JSON)`, `equity_curve (JSON)`, `summary (JSON)`.

### notification_logs
`id`, `created_at`, `channel`, `recipient`, `message_content`, `status`
(SENT/FAILED/SKIPPED), `error_message`.

### system_state
`key` (PK), `value`, `updated_at`. Restart-safe runtime state: cooldown keys,
dedup keys, scheduler `_last_processed_ts`, limits.

## Migrations
`app/database/connection.py::_MIGRATIONS` applies missing columns on startup
(additive, non-destructive). New tables are created by `create_all`.

## Restart Safety
- WAL ensures crash-consistency (integrity_check passes after simulated
  crashes).
- Signals/outcomes/paper/system-state survive restart; the scheduler's
  `_last_processed_ts` (in memory + status) prevents duplicate candle
  analysis after restart.
- `backup_db.py` uses the SQLite online backup API (WAL-safe); `restore_db.py`
  snapshots before restore and clears stale `-wal`/`-shm`.
