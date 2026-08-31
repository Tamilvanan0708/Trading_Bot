# COST MODEL — XAU/USD AI Signal Intelligence Terminal

## Model
`app/research/replay_engine.replay_variant(cost_points=...)` models **round-trip
execution cost** in price points (spread + slippage + commission):

- Effective entry = `entry ± cost/2` (adverse)
- SL/TP fills degraded by `cost/2`
- Effective risk = `risk + cost`
- An SL exit is always **−1R by definition** (risk includes both half-costs);
  TP wins shrink with cost.

`cost_points=0` is byte-identical to the no-cost behaviour (verified by tests).

## Baseline
Realistic XAUUSDT round-trip ≈ **0.5 points**:
- spread ~0.30
- slippage ~0.15
- commission/fees ~0.05

Swept at 0×, 0.5×, 1×, 1.5×, 2×, 3× (i.e. 0, 0.25, 0.5, 0.75, 1.0, 1.5 pts RT).

## Results (pooled OOS, per candidate)
| Candidate | 0× | 1× (0.5pt) | 1.5× | 2× | 3× |
|---|---|---|---|---|---|
| A (15M) | +0.567R | **+0.278R** | +0.191R | +0.120R | +0.009R |
| B (5M) | +0.442R | **−0.217R** | −0.316R | −0.386R | −0.481R |
| C (5M) | +0.413R | **−0.057R** | −0.141R | −0.202R | −0.291R |

## Break-even cost (approximate)
- Candidate A: ~0.9 pts round-trip (edge survives 3× baseline)
- Candidate B: ~0.35 pts round-trip (edge gone at 1×)
- Candidate C: ~0.45 pts round-trip (edge gone at 1×)

## Interpretation
The 15M entry (Candidate A) has a **cost-robust edge**. The 5M entries (B/C)
do not — their tighter per-trade risk makes fixed costs proportionally larger.
**Cost robustness is a promotion gate**: a candidate is eligible only if its
1× cost-adjusted expectancy is positive and it survives 1.5×.

`scripts/cost_robustness.py` regenerates `reports/cost_robustness.{json,md}`.
