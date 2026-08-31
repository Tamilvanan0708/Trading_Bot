# Execution-Cost Robustness (XAUUSDT)
Baseline round-trip cost: 0.5 pts
Generated: 2026-08-25T04:58:58.716710+00:00

| Candidate | n | Cost | Exp | WR | PF | DD |
|---|---|---|---|---|---|---|
| CANDIDATE_A_V1 | 327 | 0.0pts (0x) | +0.567R | 56.88% | 2.32 | 15.19% |
| CANDIDATE_A_V1 | 327 | 0.5pts (1x) | +0.278R | 55.05% | 1.65 | 24.56% |
| CANDIDATE_A_V1 | 327 | 0.75pts (1.5x) | +0.191R | 52.6% | 1.44 | 27.38% |
| CANDIDATE_A_V1 | 327 | 1.0pts (2x) | +0.120R | 52.6% | 1.27 | 29.14% |
| CANDIDATE_A_V1 | 327 | 1.5pts (3x) | +0.009R | 51.99% | 1.02 | 32.04% |
| CANDIDATE_B_V1 | 164 | 0.0pts (0x) | +0.442R | 52.44% | 1.93 | 17.38% |
| CANDIDATE_B_V1 | 164 | 0.5pts (1x) | -0.217R | 31.1% | 0.61 | 42.15% |
| CANDIDATE_B_V1 | 164 | 0.75pts (1.5x) | -0.316R | 31.1% | 0.47 | 49.53% |
| CANDIDATE_B_V1 | 164 | 1.0pts (2x) | -0.386R | 31.1% | 0.37 | 53.96% |
| CANDIDATE_B_V1 | 164 | 1.5pts (3x) | -0.481R | 28.66% | 0.25 | 59.15% |
| CANDIDATE_C_V1 | 203 | 0.0pts (0x) | +0.413R | 47.29% | 1.79 | 18.38% |
| CANDIDATE_C_V1 | 203 | 0.5pts (1x) | -0.057R | 34.48% | 0.9 | 39.44% |
| CANDIDATE_C_V1 | 203 | 0.75pts (1.5x) | -0.141R | 33.99% | 0.76 | 47.31% |
| CANDIDATE_C_V1 | 203 | 1.0pts (2x) | -0.202R | 33.99% | 0.66 | 52.16% |
| CANDIDATE_C_V1 | 203 | 1.5pts (3x) | -0.291R | 32.51% | 0.53 | 57.97% |

## Fragility
- CANDIDATE_A_V1: ROBUST
- CANDIDATE_B_V1: FRAGILE
- CANDIDATE_C_V1: FRAGILE

Conclusion: a candidate is promotion-eligible only if expectancy stays positive at >= 1.5x baseline cost.