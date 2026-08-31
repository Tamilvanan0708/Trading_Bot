# CANDIDATE RESEARCH — XAU/USD AI Signal Intelligence Terminal

## Current Candidates (frozen versions)
| Version | MTF | Regime | Conf ≥ | TP | SL | Raw OOS Exp | Cost-adjusted (1×) | Fragility |
|---|---|---|---|---|---|---|---|---|
| CANDIDATE_A_V1 | 4H→1H→30M→15M | RANGING | 75 | 1.75R | 1.5 ATR | +0.511R | **+0.278R** | ROBUST |
| CANDIDATE_B_V1 | 4H→1H→30M→5M | RANGING | 85 | 1.75R | 1.5 ATR | +0.627R | −0.217R | FRAGILE |
| CANDIDATE_C_V1 | 4H→1H→15M→5M | RANGING | 85 | 2.0R | 1.5 ATR | +0.453R | −0.057R | FRAGILE |

**Key finding:** Candidate A (15M trigger) is the only executionally viable
candidate at realistic round-trip costs. Candidates B/C (5M triggers) carry
risk too small (1.5×5M ATR ≈ 5-6 pts) for 0.5pt costs to be survivable.

## Research dimensions supported
- MTF combinations (4 configs researched; more documented in NEXT_RESEARCH_AUDIT)
- Regime filters (ALL/RANGING/TRENDING/HIGH_VOL/LOW_VOL)
- Confidence thresholds (60-90)
- TP geometry (0.75-2.0R)
- SL geometry (base / 1.0-2.0 ATR)
- Signal selection (strongest-per-4H/session/day, min-gap, confidence-top)
- Transaction-cost assumptions (0×…3×)
- Volatility (ATR quartiles), session, direction, confluence buckets

## How new candidates are created
1. Define a new `ForwardCandidate` (new version) in `app/research/candidates.py`.
2. Historically validate: backtest → walk-forward → OOS → cost → MC → bootstrap.
3. Only if it passes research prerequisites, add to forward observation.
4. Parameters are frozen once observed; improvements require a new version.

## Reports
- `reports/mtf_comparison.md` — ranked cost-aware comparison
- `reports/cost_robustness.md` — per-candidate cost sweep + fragility
- `reports/signal_quality.md` — regime/session/DOW/direction/confluence breakdown
- `reports/5m_vs_15m.md` — entry-timeframe comparison
- `reports/monthly/` + `reports/daily/` — forward reports
- `data/research/mtf_report.json`, `latest_report.json` — machine-readable
