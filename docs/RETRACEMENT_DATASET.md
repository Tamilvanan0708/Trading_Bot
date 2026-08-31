# RETRACEMENT_DATASET — RETRACEMENT_BOS_V1 Training & Research Dataset

## Source

`scripts/build_retracement_dataset.py` builds a deterministic CSV dataset from
**real persisted XAU/USD candles** (`data/research/xauusd_5m_2yr.json`). No
fabricated candles are used.

## Datasets

### 15M (24,607 candles, ~Dec 2025 – Aug 2026)

| Metric | Value |
|--------|-------|
| Candles | 24,607 |
| Setups | 314 |
| Event rows | 3,296 |
| State distribution | BOS_DETECTED:314, POINT_2_IDENTIFIED:314, FIB_ACTIVE:314, TP_DYNAMIC:1402, ENTRY_TOUCHED:307, TP_FROZEN:332, COMPLETED:296, INVALIDATED:17 |

### 5M (73,820 candles, ~Dec 2025 – Aug 2026)

| Metric | Value |
|--------|-------|
| Candles | 73,820 |
| Setups | 906 |
| Event rows | ~9,000 |
| State distribution | BOS_DETECTED:906, POINT_2_IDENTIFIED:906, FIB_ACTIVE:906, TP_DYNAMIC:4058, ENTRY_TOUCHED:884, TP_FROZEN:964, COMPLETED:849, INVALIDATED:56 |

## Training Labels

Labels are generated from the **exact deterministic strategy rules**:

1. Bullish BOS detection (close > previous confirmed swing high)
2. Point 2 identification (low of the BOS move)
3. Valid high progression (confirmed swing highs update the dynamic TP)
4. Fibonacci level construction (0.000/0.236/0.618/1.000/1.618 from Point 2
   anchor + current valid high)
5. Entry-touch detection (0.618 touched by a candle)
6. TP freeze event (entry touch freezes locked TP)
7. Post-entry TP immutability (new highs after entry are ignored)

## Label format

Each row captures one event with:
- `timestamp`, `candle_index`, `setup_id`
- `event_type` (BOS_DETECTED, POINT_2_FOUND, NEW_VALID_HIGH, TP_UPDATED,
  ENTRY_TOUCHED, TP_LOCKED, POST_ENTRY_HIGH_IGNORED, SL_HIT, TP_HIT,
  INVALIDATED, COMPLETED, SETUP_CREATED)
- `state_before`, `state_after`
- All five Fibonacci levels (`fib_0` through `fib_1_618`)
- Entry, SL, TP, locked TP, dynamic TP

## Usage

```bash
.venv\Scripts\python.exe scripts\build_retracement_dataset.py 15m
.venv\Scripts\python.exe scripts\build_retracement_dataset.py 5m
```

Output files are written to `data/retracement_dataset/retracement_bos_v1_{timeframe}.csv`.