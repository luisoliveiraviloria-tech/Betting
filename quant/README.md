# EPL Quant — Stage 0/1 (data + baseline goal models)

Part of the £20 → £1,000 EPL value-betting research project. See the root
project brief for full scope; this covers only Stages 0-1.

## Setup

```bash
cd quant
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Pipeline

1. **`data.py`** — downloads EPL match results + odds from
   football-data.co.uk (free, no key) into `quant/epl.db` (SQLite).
   - `matches`: date, teams, final score, result.
   - `markets`: closing/pre-close odds from two venues:
     - `bookmaker_avg` — average odds across multiple bookmakers, all
       seasons 2012+ (5,320 matches).
     - `betfair_exchange` — **only present for the 2024-25 and 2025-26
       seasons** (740 matches) in this free source. Deeper Betfair-specific
       history requires Betfair's paid Historical Data service
       (historicdata.betfair.com) — not yet integrated.
   ```bash
   .venv/bin/python quant/data.py --start-year 2012 --end-year 2025
   ```

2. **`models/poisson.py`** — independent Poisson goal model (Maher, 1982):
   team attack/defense ratings + home advantage, fit by MLE.

3. **`models/dixon_coles.py`** — adds the Dixon & Coles (1997) low-score
   correlation adjustment (tau term on 0-0/1-0/0-1/1-1) and optional
   exponential time-decay weighting.

4. **`market.py`** — de-vigs raw decimal odds into implied probabilities.
   Implements both naive multiplicative normalisation and Shin's method
   (Shin, 1992), which corrects for the favourite-longshot bias. Used as
   the benchmark every model must beat out-of-sample — never assume the
   model is right just because it disagrees with the market.

5. **`backtest.py`** — walk-forward evaluation (train on seasons < Y, test
   on season Y, roll forward). Reports Brier score, log loss, and an
   edge-bucket ROI table (model-vs-market edge bucketed into <0%/0-2%/
   2-5%/5-10%/10%+, realised ROI per bucket).
   ```bash
   .venv/bin/python -m quant.backtest
   ```

## Result (2012–2025, 14 seasons)

Both Poisson and Dixon-Coles are **worse calibrated than the de-vigged
market** (Brier 0.608 vs market's 0.572; log loss 1.018 vs 0.964), and
their edge-bucket ROI is **non-monotonic and worst in the 10%+ claimed-edge
bucket** (-18.7% Dixon-Coles, -16.9% Poisson) — the signature of model
overconfidence rather than real edge. Full numbers in git history / rerun
`backtest.py`.

**Conclusion:** a goals-only structural model with no exogenous
information cannot compete with a market that already prices in injuries,
form, lineups, etc. Adding more goal-model variants will not close this
gap. The next stage needs to bring in information the market benchmark
doesn't already have baked in — team-strength time-decay tuning, then a
market-as-prior Bayesian update (spec's "Model 6"), before any live-betting
Filter (5-9) can be trusted.

## Not yet built

- xG-based model, ML model, Bayesian/market-prior model, ensemble weighting
- Betfair API-NG live price + liquidity retrieval (credentials available,
  not yet wired — see root project notes)
- Bankroll/staking module (flat £2 floor until Kelly stake clears £2, per
  project decision)
- CLV tracking, bet ledger, dashboard, LLM council/contrarian agent
