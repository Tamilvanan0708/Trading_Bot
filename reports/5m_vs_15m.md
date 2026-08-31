# 5M vs 15M Entry Trigger — Real-Data Comparison
Generated: 2026-08-25T05:01:17.958858+00:00

| Metric | A (15M trigger) | B (5M trigger) |
|---|---|---|
| Signals/day | 1.45 | 0.8 |
| Median ATR (pts) | 1.86 | 0.69 |
| No cost Exp | +0.567R | +0.442R |
| No cost PF | 2.32 | 1.93 |
| No cost WR | 56.88% | 52.44% |
| Cost 0.5pt RT Exp | +0.278R | -0.217R |
| Cost 0.5pt RT PF | 1.65 | 0.61 |
| Cost 0.5pt RT WR | 55.05% | 31.1% |
| Cost 1.0pt RT Exp | +0.120R | -0.386R |
| Cost 1.0pt RT PF | 1.27 | 0.37 |
| Cost 1.0pt RT WR | 52.6% | 31.1% |

## Conclusion
15M trigger (Candidate A) is the more robust entry: it remains positive after realistic round-trip costs while the 5M trigger (Candidate B) turns negative.  The 5M trigger produces more signals but each carries a smaller risk, so fixed costs eat a larger fraction of the edge.

5M produces more signals but is NOT automatically better: its tighter risk makes it fragile to realistic spread/slippage/commission.