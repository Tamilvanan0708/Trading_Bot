# PROMOTION AUDIT — XAU/USD AI Signal Intelligence Terminal

## Lifecycle
```
RESEARCH
→ BACKTESTED
→ OOS_VALIDATED
→ FORWARD_OBSERVATION
→ PROMISING
→ QUALIFIED
→ HUMAN_REVIEW
→ PROMOTED

Also: FAILED · REJECTED · DEPRECATED
```

All transitions are tracked (not auto-promoted). The `PromotionStateMachine`
class in `app/research/ranking.py` enforces valid transitions; any invalid
transition returns `False`.

## Current State
| Candidate | State |
|---|---|
| Production (4H→1H→30M→15M, TP2@2.41R) | **FAILED** |
| CANDIDATE_A_V1 | **OOS_VALIDATED → FORWARD_OBSERVATION** |
| CANDIDATE_B_V1 | OOS_VALIDATED → FORWARD_OBSERVATION |
| CANDIDATE_C_V1 | OOS_VALIDATED → FORWARD_OBSERVATION |

No candidate is promoted. Forward observation is actively collecting evidence.

## Promotion Requirements (minimum)
1. **100+ closed forward signals** (per candidate)
2. **4-8 weeks** of continuous observation
3. **Positive pooled forward expectancy** (bootstrap CI must not be negative)
4. **Acceptable PF** (≥1.0 forward)
5. **Acceptable drawdown** (< configured MAX_TOTAL_DRAWDOWN_PCT)
6. **Cost robustness** (1× cost-adjusted expectancy positive; survives 1.5×)
7. **Bootstrap + Monte Carlo support** (CI not strongly negative, MC P(neg)
   below documented threshold)
8. **Multiple-regime validation** (edge survives in ≥2 distinct regime types)
9. **No catastrophic degradation** vs OOS cost-adjusted reference
10. **Complete safety audit** (no look-ahead, no data-quality violations,
    no duplicate/forced signals, real-money disabled)
11. **Human approval** (not autopromoted)

Until these are met: **FORWARD_OBSERVATION**.

If evidence is insufficient or contradictory: **INCONCLUSIVE** (not PROMISING).

## What promotion does NOT mean
- Does not enable real-money trading (permanently disabled).
- Does not bypass any safety gate (admission, limits, breakers, data quality).
- Does not change the signal-only / manual-execution operating model.
- Only changes which configuration the production signal engine uses.

## What the system does with promoted candidates (when applicable)
1. Human approval records the promotion decision.
2. The candidate's configuration replaces the production engine's parameters.
3. The new production version is stamped on all subsequent signals.
4. Paper validation may begin (separate gate, still no real money).
5. Real-money execution remains impossible (no broker path).

## Key documents
- `docs/strategy_promotion_policy.md` — detailed promotion policy
- `docs/SAFETY_AUDIT.md` — permanent safety constraints