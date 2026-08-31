# Remaining Work — Architecture & Execution Plan

## Dependency Graph (execution order)

```
[1] Signal-quality analysis engine (Phase 2)
        └─ needs: cached MTF signals (data/research/mtf_signals/*.json)
        └─ produces: reports/signal_quality.md
        └─ feeds:   daily/weekly/monthly reports, ranking

[2] Cost/execution robustness (Phase 5)
        └─ needs: replay_variant cost model (app/research/replay_engine.py)
        └─ produces: reports/cost_robustness.md
        └─ feeds:   promotion gate (execution-cost robustness requirement)

[3] 5M vs 15M focused comparison (Phase 4)
        └─ needs: mtf_report.json + cached signals (A vs B)
        └─ produces: reports/5m_vs_15m.md

[4] Parameter neighborhood / cooldown (Phase 6)
        └─ needs: cached signals + replay
        └─ produces: reports/parameter_plateaus.md

[5] AI effectiveness (Phase 11)
        └─ needs: production walk-forward + AI-mock gate
        └─ produces: reports/ai_effectiveness.md (honest: mock AI = no filter)

[6] Monthly report (Phase 17)
        └─ needs: signals table + weekly logic
        └─ produces: reports/monthly/

[7] Final audit update (Phase 19) + concise final report
```

## Already Complete (frozen)

- Forward observation engine (Candidate A/B/C on every closed candle, versioned, restart-safe, dedup)
- Candidate framework + ranking (balanced risk-adjusted) + promotion state machine
- Walk-forward, Monte Carlo, bootstrap, classification (pooled OOS)
- MTF research (4 configs, exit/regime/conf/selection sweeps)
- Telegram signal + system alerts (dedup, persistence)
- Dashboard, Docker, systemd, backups, CI, 274→291 tests
- `docs/final_system_audit.md`

## Detailed Tasks

### T1 — replay_variant cost model
Add `cost_points` (spread+slip+commission in price points) to `replay_variant`:
effective entry/TP/SL shifted adversely; pnl_r computed on effective risk.
Default 0.0 → byte-identical to existing results (equivalence-tested).

### T2 — Signal quality analysis script
For each candidate (A/B/C), using cached OOS signals + replay at the
candidate's frozen TP/SL: metrics + breakdowns by regime, session, day-of-week,
direction, confluence bucket. Output `reports/signal_quality.md` + json.

### T3 — Cost robustness script
Sweep cost = 0 / 1x / 1.5x / 2x / 3x of a realistic XAUUSDT baseline
(spread 0.3 + slippage 0.2 ≈ 0.5 pts + commission). Report expectancy/PF/WR/DD
per level per candidate. Flag candidates whose edge dies at 1.5-2x.

### T4 — 5M vs 15M focused report
Compare Candidate B (5M trigger) vs Candidate A (15M trigger): signal frequency,
entry quality (MFE/MAE), TP/SL reachability, spread/slippage sensitivity, OOS
consistency. Conclusion must NOT assume 5M is better.

### T5 — Parameter neighborhood
Cooldown sweep (1h/2h/4h/8h) + confidence neighborhood (75-90) + TP/SL
neighborhood. Identify stable plateaus (expectancy stays positive across a
±20% region), not single peaks.

### T6 — AI effectiveness
Since AI is mock (approve ~everything), measure whether adding the AI gate
changes the production walk-forward expectancy. Honest finding expected:
mock AI ≈ no filter; document that a real LLM would need separate evaluation.

### T7 — Monthly report
Extend weekly_forward_report.py logic into `scripts/monthly_forward_report.py`
writing reports/monthly/.

### T8 — Audit + final report
Update docs/final_system_audit.md with new evidence; deliver concise final report.

## Safety invariants (unchanged)
- Signal-only, no broker API, real-money disabled.
- Candidates frozen; no tuning on forward data.
- Promotion requires 100+ forward signals, 4-8 weeks, human approval.
