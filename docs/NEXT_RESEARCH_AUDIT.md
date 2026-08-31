# NEXT RESEARCH AUDIT — XAU/USD AI Signal Intelligence Terminal

## Current Research Capabilities (verified)
| Capability | Module / Script | Status |
|---|---|---|
| Backtesting | `app/backtesting/engine.py` | ✅ event-driven, chronological, SL-priority |
| Walk-forward | `app/research/walkforward.py` | ✅ train/val/test chronological windows |
| OOS classification | `app/research/classification.py` | ✅ conservative pooled OOS |
| Bootstrap CI | `app/research/bootstrap.py` | ✅ mean/median R, win rate, seedable |
| Monte Carlo | `app/research/monte_carlo.py` | ✅ P(neg), DD distribution, ruin |
| Candidate generation | `app/core/mtf_config.py`, `app/research/replay_engine.py` | ✅ MTF roles + fast collector |
| Candidate ranking | `app/research/ranking.py` | ✅ cost-aware, balanced score |
| Transaction-cost model | `app/research/replay_engine.py` (cost_points) | ✅ round-trip, half-per-side |
| Observation tracker | `app/research/candidate_observation.py` | ✅ A/B/C side-by-side |
| Outcome tracker | `app/research/outcome_tracker.py` | ✅ MAE/MFE/TP/SL/final R |
| Promotion state machine | `app/research/ranking.py` | ✅ RESEARCH→…→PROMOTED (manual) |
| MTF research | `scripts/run_mtf_research.py` | ✅ 4 configs + sweeps |
| Cost robustness | `scripts/cost_robustness.py` | ✅ 0×…3× cost |
| Signal quality analysis | `scripts/signal_quality_analysis.py` | ✅ regime/session/DOW/direction/confluence |
| 5M vs 15M | `scripts/5m_vs_15m_report.py` | ✅ 15M more robust at cost |
| Daily opportunity | `app/research/replay_engine.daily_opportunity` | ✅ honest distribution |
| **Candidate health monitor** | `app/research/candidate_health.py` | ✅ NEW — GREEN/YELLOW/RED, CI-gated |
| **Signal quality score** | `app/research/quality_score.py` | ✅ NEW — deterministic 0-100 |
| **Research APIs** | `/research/ranking`, `/research/health`, `/research/daily-opportunity` | ✅ NEW |

## Missing / Weak Capabilities
1. **Full MTF combination matrix** — only 4 of the requested 7+ configurations
   are researched (4H→1H→30M→15M/5M, 4H→1H→15M→5M, 1H→30M→15M→5M). The
   combinations 4H→30M→15M, 4H→1H→15M, 1H→15M→5M are not yet covered as
   separate research runs (data + runtime cost).
2. **Signal-spacing correlation** — signal clustering is measured, but the
   correlation of consecutive-signal outcomes and the spacing-gap trade-off
   (no-spacing vs 1h/2h/4h) is not yet a standalone reproducible report.
3. **MFE/MAE distribution analysis** — averages are reported; the full
   distribution, time-to-MFE/MAE, and MFE-derived TP/SL geometry studies are
   not yet produced as reports.
4. **Volatility-quartile research** — ATR quartiles exist in the diagnosis but
   a dedicated LOW/NORMAL/HIGH/EXTREME classification with per-candidate
   performance is not yet a report.
5. **Research-run reproducibility ledger** — research runs are reproducible
   (seeded MC/bootstrap, deterministic collector) but run_id + parameter
   fingerprint persistence is not yet implemented.
6. **Regime-diversity gate** — promotion does not yet require an explicit
   multiple-regime validation report.

## Data Limitations
- Only ~8.5 months of Binance XAUUSDT history (symbol launch limit). This
  bounds the number of walk-forward windows and the statistical power of
  forward validation.
- 5M data is available for the same period (73,820 candles).

## Statistical Limitations
- Candidate A has only 327 OOS signals; candidates B/C ~164-203. Bootstrap CI
  is positive but modest.
- At realistic 0.5pt round-trip costs, only Candidate A remains positive
  (+0.278R at 1×); B/C are negative (5M risk too tight).
- Forward closed-sample is ~0 — no statistically meaningful forward conclusion
  can be drawn yet (the correct state is INCONCLUSIVE / CONTINUE OBSERVATION).

## Recommended Next Research Stages
1. **Signal-spacing report** — outcome correlation vs gap (5m/15m/30m/1h/2h).
2. **MFE/MAE distribution report** — per candidate: MFE/MAE deciles,
   time-to-outcome, and a static MFE-derived TP study (new versions only).
3. **Volatility-quartile report** — per candidate performance under
   LOW/NORMAL/HIGH/EXTREME ATR.
4. **MTF combination extension** — add the remaining requested MTF stacks as
   new research runs (versioned, cost-adjusted).
5. **Reproducibility ledger** — persist run_id + parameter fingerprint for
   every research run.
6. **Regime-diversity gate** — report whether each candidate's edge holds in
   ≥2 regimes before any promotion review.
