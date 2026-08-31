# RESEARCH METHODOLOGY — XAU/USD AI Signal Intelligence Terminal

## Principles
1. **Real data only.** No synthetic prices, candles, signals, or metrics.
2. **No look-ahead.** Only closed candles; no future regime/volatility/signal
   selection.
3. **Chronological validation.** Walk-forward train/val/test; never shuffled.
4. **Pooled OOS metrics.** Classification uses pooled OOS (not per-window
   averages) so a strategy is never upgraded on a single profitable window.
5. **Cost realism.** Every candidate reports gross AND net expectancy under
   round-trip execution costs (spread + slippage + fees).
6. **Version immutability.** Every strategy change is a new immutable version;
   historical results are never overwritten.
7. **No overfitting.** Parameter neighborhoods, robustness checks, minimum
   sample sizes, and multiple-testing awareness; never "best single peak".
8. **Honest daily target.** The 30-50 pts/day preference is a research
   objective, never a forced requirement.

## Pipeline
```
Real 5M/15M data
  → validate (gaps/dup/ooo/invalid/stale)
  → incremental MTF resample (5M/15M/30M/1H/4H)
  → signal collection (deterministic, closed-candle only)
  → candidate sweep (MTF × regime × confidence × TP × SL × selection)
  → pooled OOS + per-window consistency
  → cost robustness (0×…3×)
  → Monte Carlo (10k) + bootstrap CI
  → balanced risk-adjusted ranking
  → forward observation (live, immutable versions)
  → candidate health monitor (CI-gated GREEN/YELLOW/RED)
  → promotion review (human, evidence-gated)
```

## Reproducibility Controls
- Seeded Monte Carlo (`seed=42`) and bootstrap (`seed=42`) → identical results.
- Deterministic incremental collector verified byte-identical to the reference
  (O(n)) implementation.
- Cached signal sets per config (`data/research/mtf_signals/`) → repeated runs
  reuse identical inputs.

## Overfitting Funnel
Every research sweep should report the funnel (counts):
```
Candidates tested → passed initial filters → passed OOS → passed cost
robustness → passed stability → promoted (never automatic)
```

## Statistical Thresholds
- Minimum OOS trades for a classification: 10 (classification config).
- Forward promotion minimum: 100 closed signals, 4-8 weeks.
- Candidate health: MIN_SAMPLE_YELLOW=10, MIN_SAMPLE_GREEN=30; decisions use
  bootstrap 95% CIs, never single losses.

## Honest Reporting
- If a candidate's bootstrap CI crosses zero → INCONCLUSIVE, not promoted.
- If forward sample is insufficient → "INSUFFICIENT FORWARD DATA — CONTINUE OBSERVATION."
- If daily 30-50 pts is unsupported → "TARGET NOT STATISTICALLY SUPPORTED."
