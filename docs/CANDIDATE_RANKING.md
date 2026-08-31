# CANDIDATE RANKING — XAU/USD AI Signal Intelligence Terminal

## Principle
Rank by **balanced risk-adjusted score**, never primarily by win rate. A
candidate with 80% WR but poor expectancy must not outrank a lower-WR
candidate with superior risk-adjusted returns.

## Weights (`app/research/ranking.DEFAULT_WEIGHTS`)
| Component | Weight |
|---|---|
| OOS expectancy (cost-adjusted when available) | 25% |
| Profit factor | 20% |
| Drawdown (inverted) | 15% |
| OOS window consistency | 10% |
| Robustness (parameter + cost, split) | 10% |
| Bootstrap CI positivity | 10% |
| Sample size | 5% |
| Monte Carlo stability | 5% |

## Cost-awareness
`rank_candidates` prefers the **1× cost-adjusted expectancy** over the raw OOS
expectancy when a `cost_sweep` is attached. This reversed the earlier ranking:
Candidate B (highest raw +0.627R) dropped below Candidate A when cost-adjusted
(−0.217R vs +0.278R) — the 5M entries are unviable at real costs.

## WHY is #1 ranked #1? (current)
**CANDIDATE_A_V1** (PROD 4H→1H→30M→15M, RANGING, conf≥75, TP1.75R/SL1.5ATR):
- Highest cost-adjusted 1× expectancy (+0.278R) and the only candidate that
  stays positive at 3× cost
- 9/13 positive OOS windows
- Bootstrap CI [0.36, 0.66] entirely positive
- MC P(negative)=0%
- Broadly positive across ASIA/LONDON sessions and SHORT direction

## Forward-adjusted bonus
When a candidate accumulates ≥10 closed forward signals, a forward-expectancy
bonus can be applied (`forward_weight_bonus`), so ranking reflects real
forward evidence as it accumulates.

## Exposed
- `GET /research/ranking` — machine-readable ranked list with `rank_score`
- Dashboard/Terminal Candidates page — rank table + per-candidate cards
  with forward CI + sample (from `GET /research/health`)
