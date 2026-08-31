# FORWARD VALIDATION — XAU/USD AI Signal Intelligence Terminal

## Design
- `OBSERVATION_MODE=true`: live closed candles → MTF → confluence → Candidates
  A/B/C evaluated side-by-side → signals persisted (versioned) → forward
  outcomes tracked by `SignalOutcomeTracker` (MAE/MFE/TP/SL/final R / time).
- **No trades** are opened in observation mode; paper trading is blocked while
  the production strategy is FAILED.
- Restart-safe: open signals are restored from DB; the tracker advances from
  the last processed candle (no double-processing, timezone-safe).

## Tracked per signal
entry · SL · TP1/TP2/TP3 · strategy_version · candidate · regime · session ·
confluence · timestamp · MFE · MAE · TP1/TP2/TP3/SL hit · final R ·
time-to-outcome · data-quality status.

## Current status
- Forward closed candidate signals: **~0** (candidates are rare by design;
  RANGING + conf≥75/85 setups have not yet occurred in the live window).
- The correct current state is **INSUFFICIENT FORWARD DATA — CONTINUE
  OBSERVATION.**

## Monitoring
`app/research/candidate_health.py` assigns GREEN/YELLOW/RED using bootstrap
95% CIs and the cost-adjusted OOS reference:
- GREEN: ≥30 closed + CI strictly positive
- YELLOW: <10 closed (informational) or CI crosses zero
- RED: CI upper bound < 0 (statistically significant deterioration)

A single loss never marks RED. Exposed at `GET /research/health`.

## Promotion requirement (manual, evidence-gated)
100+ forward closed signals, 4-8 weeks, positive pooled forward expectancy,
acceptable PF/drawdown, cost robustness, bootstrap + Monte Carlo support,
multiple-regime validation, complete safety audit, human approval.
Until then: **FORWARD_OBSERVATION** (never auto-promote).

## Reports
- `scripts/daily_forward_report.py` → `reports/daily/`
- `scripts/weekly_forward_report.py` → per-candidate OOS-vs-forward, ranking,
  promotion status
- `scripts/monthly_forward_report.py` → `reports/monthly/`
