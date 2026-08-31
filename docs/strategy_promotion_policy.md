# Strategy Promotion Policy

This document defines the **only** acceptable path for a strategy candidate to
move from *research* to *production* in this system.

It exists to prevent the single most common failure in algorithmic trading:
promoting a strategy because a backtest (or a handful of backtests) looked
profitable.

> **Current status: the production strategy is classified FAILED/UNPROVEN and
> remains blocked. No candidate has been promoted. This policy is the gate.**

---

## 1. Definitions

| Term | Meaning |
|---|---|
| **Candidate** | A research configuration (exit geometry + signal-selection filter) evaluated out-of-sample in `run_candidate_oos.py` / `run_full_research.py`. |
| **Forward observation** | Real-time recording of signal → outcome data by the SignalOutcomeTracker while `OBSERVATION_MODE=true`. No paper trade is opened. |
| **Promotion** | Changing the production strategy configuration (weights, thresholds, exits, filters) so that live signals use the candidate's logic. |
| **Production strategy** | The strategy currently generating live signals. Config-driven, version-stamped. |

## 2. Absolute Rules

1. **No promotion without forward evidence.** Backtest / walk-forward / OOS
   results are *necessary but never sufficient*.
2. **No promotion while the candidate's pooled OOS is negative.**
3. **No promotion on a single window, single period, or cherry-picked regime.**
4. **No tuning on the final test set.** Parameters are locked before the OOS
   window is evaluated.
5. **No hiding negative results.** The classification reasons and all research
   artifacts remain visible in `data/research/`.
6. **No real-money execution. Ever.** This system has no broker order path and
   none will be added.

## 3. Promotion Requirements

A candidate **must** satisfy ALL of the following:

### A. Statistical evidence
- Minimum **200 forward signals** collected in observation mode
  (or a statistically justified smaller sample, documented).
- Minimum **4 weeks** of continuous observation; **6-8 weeks preferred**.
- **Positive pooled expectancy** in R (95% bootstrap CI must not include a
  strongly negative mean).
- **Profit factor > 1.0** on the forward sample.
- No **catastrophic regime dependency**: the edge must survive in at least two
  distinct market regimes / volatility quartiles.

### B. Walk-forward stability (research prerequisite)
- **> 60% positive OOS windows** across ≥ 4 windows on real data.
- Pooled OOS expectancy positive with bootstrap CI excluding negative territory.
- Monte Carlo: median final equity positive; probability of a ruinous
  drawdown (e.g. > 25%) below a documented threshold.
- **No parameter cliff**: expectancy remains positive across a ±20% perturbation
  of every strategy-critical parameter (TP, SL, confidence threshold,
  spacing, regime filter).

### C. Data & integrity
- No look-ahead bias (verified by the no-look-ahead test suite).
- No data-quality violations during the observation window.
- No duplicate-candle or forming-candle processing.
- The signal set is versioned; every forward signal carries the candidate's
  `strategy_version`.

### D. Risk acceptability
- Max drawdown during observation below the configured
  `MAX_TOTAL_DRAWDOWN_PCT`.
- Monthly performance is not driven by a single week or a single large trade.

## 4. Decision Procedure

1. Candidate defined in a research script (exit + filter) and **locked**.
2. Backtest / walk-forward / OOS evaluated. If it fails any *research
   prerequisite* → **REJECTED** (documented).
3. If it passes the research prerequisites, run in **OBSERVATION_MODE** for the
   full observation period.
4. After the minimum sample is reached, compute forward metrics (from the
   `signals` outcome columns via `/research/signal-outcomes`).
5. Evaluate against Section 3. All items must pass.
6. A human review must approve. The classification changes to a positive grade
   **only after** promotion is explicitly approved.
7. If evidence is insufficient → classification stays **INCONCLUSIVE**,
   production stays FAILED, candidate stays un-promoted.

## 5. What Promotion Does NOT Mean

- Promotion does **not** enable real-money trading.
- Promotion does **not** bypass the admission gate, risk limits, drawdown
  breaker, or data-quality gates.
- Promotion **only** changes which configuration produces signals; the paper
  block remains active while the strategy grade is FAILED.

## 6. Escalation

If a candidate fails forward validation, the failure is recorded and the
candidate is retired or modified. Failed candidates are kept in the research
history for auditability.

---

*This policy is enforced by engineering discipline, not code. The system will
never self-promote.*
