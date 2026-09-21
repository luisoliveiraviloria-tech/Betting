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

## Stage 2 result: market-prior blend

`models/market_prior_blend.py` blends the Dixon-Coles probability with the
de-vigged market probability in log-odds space, with the blend weight `w`
fit by **nested** walk-forward validation (tuned on the season immediately
before the test season, never on the test season itself or a future one) —
per the spec's rule that weights must be validated, not hand-picked.

**Result: the tuned weight collapses to `w≈0` in almost every season** —
i.e. the validation procedure itself concludes "ignore the Dixon-Coles
output, trust the market," because the goal-only model has no incremental
predictive information over the market's own closing price. This is not a
bug; it's the honest output of doing Model 6 properly, and it reinforces
the Stage 1 finding rather than contradicting it. `backtest.py --full`
also reports commission-adjusted (5%, Betfair headline rate — verify
current rate before relying on it) ROI per edge bucket, and a COVID-period
exclusion (seasons 2019-20/2020-21, behind closed doors) as a sensitivity
check — results are qualitatively unchanged with or without those seasons.

**What this means for next steps:** more goal-model variants (xG, ML
classifiers on the same historical-results data) are unlikely to close
this gap, because the constraint isn't model family, it's that the market
closing price already encodes everything derivable from historical
results. A genuine informational edge, if one exists, more plausibly comes
from **timing/microstructure** (spec's "Information Advantage" and
"Timing" sections) — price reaction speed to confirmed team news, or
patterns in Betfair's own order flow/liquidity — not from a better curve
fit to final scores. This is the argument for prioritising the Betfair
historical order-book data (streaming format, Basic plan) over building
Models 3/4/7 next.

## Stage 3: Betfair historic order-book data (in progress)

`betfair_auth.py` / `betfair_historic.py` wire up the Betfair Historic Data
service (historicdata.betfair.com) client per the Stage-2 conclusion above.
`BETFAIR_USERNAME`/`BETFAIR_PASSWORD`/`BETFAIR_APP_KEY` are set as env vars.

```bash
.venv/bin/python -m quant.betfair_historic --options   # valid filter values
.venv/bin/python -m quant.betfair_historic --my-data    # subscribed plan(s)
.venv/bin/python -m quant.betfair_historic --from-date 01-08-2024 \
    --to-date 31-05-2025 --list                          # list, don't download
```

**Blocked from this sandbox, untested end-to-end:** interactive login
(`identitysso.betfair.com/api/login`) returns HTTP 403 with a Cloudflare
HTML challenge page from this environment's IP — a datacenter-IP block at
Betfair's edge, confirmed independent of credentials (same result with a
browser User-Agent) and reproducible as of 2026-09-21. Run the three
commands above from a residential IP (your own machine, or RunPod's login
node if it egresses residentially) to actually authenticate. If that still
403s, switch to `betfair_auth.login_cert()` (Betfair's recommended
"bot login" flow — needs a self-signed client cert generated and its
public half uploaded under My Account > API Keys first).

Once `--list` confirms the file-listing shape and plan coverage, drop
`--list` to download into `quant/data/betfair_historic/` (gitignored,
`.bz2` streaming-format files — one per market). Not yet built: a parser
from that streaming format into the `markets` table's schema (or a sibling
table) for the backtest to consume.

## Not yet built

- Betfair streaming-file parser (bz2 -> price ladder time series -> DB)
- xG-based model, ML model, Bayesian/market-prior model, ensemble weighting
- Betfair API-NG live price + liquidity retrieval
- Bankroll/staking module (flat £2 floor until Kelly stake clears £2, per
  project decision)
- CLV tracking, bet ledger, dashboard, LLM council/contrarian agent
