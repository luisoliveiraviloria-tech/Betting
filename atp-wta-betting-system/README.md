# ATP / WTA Value Betting System

A data-driven betting framework for professional tennis that optimizes **long-term ROI**
(not win rate) by only betting when the model's probability meaningfully exceeds the
bookmaker's vig-free implied probability.

## Contents

| Path | Purpose |
|---|---|
| `docs/SYSTEM.md` | Full system design: variables, weights, entry criteria, bankroll management, ATP/WTA filters, daily workflow, KPIs, data sources, refinement loop |
| `model/value_model.py` | Weighted scoring model → confidence score (0–100) → calibrated probability → edge vs. bookmaker → fractional Kelly stake. Runnable CLI with a worked example |
| `model/kpi.py` | Reads the bet log CSV and computes ROI, yield, CLV, hit rate, max drawdown, and calibration |
| `tracker/bet_log_template.csv` | The tracking spreadsheet structure (one row per bet) |
| `data/fetch_data.py` | Downloads historical ATP/WTA match data — tennis-data.co.uk (results **with closing odds**, the default) and Jeff Sackmann's GitHub datasets (detailed match stats) — for backtesting and weight re-fitting |

## Quick start

```bash
# 1. See a worked example (Player A vs Player B factor sheet -> bet/no-bet decision)
python3 model/value_model.py --example

# 2. Score your own match (edit a JSON factor sheet, see docs/SYSTEM.md §3)
python3 model/value_model.py --match my_match.json

# 3. Pull historical data for backtesting / recalibration
python3 data/fetch_data.py --tour atp --years 2022-2025

# 4. Compute KPIs from your bet log
python3 model/kpi.py tracker/bet_log_template.csv
```

## Core principles

1. **EV+ only.** A bet is placed only when model probability exceeds the vig-free
   bookmaker probability by the minimum edge (5% ATP / 6% WTA).
2. **Low frequency, high discipline.** 0–3 bets/day. Most days the correct action is no bet.
3. **Fractional Kelly (25%), capped at 2% of bankroll.** Survives variance; compounds edge.
4. **Closing Line Value is the truth signal.** If you don't consistently beat the closing
   line, the model has no edge regardless of short-term profit.
5. **No emotion.** Every bet must pass the written entry checklist; every skip is logged too.

Read `docs/SYSTEM.md` before placing a single bet.
