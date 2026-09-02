# XAU/USD MTF + Candidate Comparison
Generated: 2026-09-01T06:12:47.581106+00:00
Data: 2025-12-11T08:05:00+00:00 -> 2026-08-24T15:40:00+00:00 (5M 73820 candles / 15M 24607)

## Best exit per configuration (ALL signals, pooled OOS)
| Config | Exit | Trades | Expectancy |
|---|---|---|---|
| PROD_4H_1H_30M_15M | TP1.75|SL1.5 | 2456 | +0.127R |
| 4H_1H_15M_5M | TP2.0|SL1.5 | 9652 | +0.046R |
| 4H_1H_30M_5M | TP1.75|SL1.5 | 7014 | +0.101R |
| 1H_30M_15M_5M | TP2.0|SLbase | 14210 | +0.024R |

## Ranked candidates (balanced score)
| Rank | Config | Regime | Conf | TP | Trades | WR | PF | Exp | DD | OOS+ |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | PROD_4H_1H_30M_15M | RANGING | 75 | 1.75 | 327 | 55.05% | 2.14 | +0.511R | 18.73% | 9/13 |
| 2 | PROD_4H_1H_30M_15M | ALL | 80 | 1.75 | 213 | 45.54% | 1.44 | +0.225R | 11.11% | 0/13 |
| 3 | PROD_4H_1H_30M_15M | ALL | 80 | 1.75 | 255 | 45.88% | 1.45 | +0.229R | 13.17% | 0/13 |
| 4 | 4H_1H_15M_5M | RANGING | 85 | 2.0 | 203 | 49.75% | 1.91 | +0.453R | 18.35% | 9/13 |
| 5 | 4H_1H_30M_5M | RANGING | 85 | 1.75 | 164 | 59.15% | 2.53 | +0.627R | 18.21% | 7/13 |
| 6 | 4H_1H_15M_5M | TRENDING | 80 | 2.0 | 119 | 46.22% | 1.63 | +0.338R | 4.9% | 11/13 |
| 7 | 4H_1H_15M_5M | TRENDING | 80 | 2.0 | 105 | 43.81% | 1.49 | +0.277R | 6.43% | 9/13 |
| 8 | 4H_1H_15M_5M | RANGING | 80 | 2.0 | 287 | 43.55% | 1.48 | +0.269R | 20.8% | 7/13 |
| 9 | 4H_1H_30M_5M | RANGING | 80 | 1.75 | 265 | 52.45% | 1.93 | +0.436R | 22.43% | 8/13 |
| 10 | 4H_1H_30M_5M | RANGING | 75 | 1.75 | 898 | 51.22% | 1.84 | +0.407R | 40.92% | 8/13 |
| 11 | 4H_1H_30M_5M | ALL | 85 | 1.75 | 1023 | 47.02% | 1.52 | +0.271R | 32.72% | 0/13 |
| 12 | 4H_1H_30M_5M | ALL | 80 | 1.75 | 2000 | 45.4% | 1.43 | +0.230R | 41.4% | 0/13 |

## Top 3 detail

### #1 PROD_4H_1H_30M_15M R:RANGING C:75 TP:1.75
- Trades: 327 | WR: 55.05% | PF: 2.14 | Exp: +0.511R | DD: 18.73%
- OOS windows positive: 9/13
- Monte Carlo: P(neg)=0.0% p95DD=10.8%
- Bootstrap mean-R CI: [0.36, 0.66]
- TP-perturbation expectancy: [0.5, 0.511, 0.628]

### #2 PROD_4H_1H_30M_15M R:ALL C:80 TP:1.75
- Trades: 213 | WR: 45.54% | PF: 1.44 | Exp: +0.225R | DD: 11.11%
- OOS windows positive: 0/13

### #3 PROD_4H_1H_30M_15M R:ALL C:80 TP:1.75
- Trades: 255 | WR: 45.88% | PF: 1.45 | Exp: +0.229R | DD: 13.17%
- OOS windows positive: 0/13

## Conclusion
No candidate is promoted. All candidates must pass forward observation (100+ signals, 4-8 weeks) before promotion review per docs/strategy_promotion_policy.md.