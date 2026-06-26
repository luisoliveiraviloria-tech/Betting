# tennis-betting

ATP/WTA tennis modelling tool. Builds surface-aware Elo ratings from free match
history, fetches live h2h odds, de-vigs them, and ranks the gap between the
Elo model's win probability and the market's implied probability.

Kept as a separate project from the football/World Cup betting system on
purpose: different sport, different model (binary Elo vs. Poisson/Dixon-Coles),
no shared fixtures API, and the football repo was already getting crowded.

## ⚠️ Honest result: this does NOT beat the market (yet)

`backtest.py` was run over 45k+ historical matches (2015–2026) with real
closing odds. The verdict, on a 2023+ hold-out the tuning never saw:

- The model is **less accurate than the closing line** — 64% favourite
  accuracy vs the market's 68%; log-loss 0.63 vs 0.59.
- Flat-staking its "value edges" **lost money in every odds band**, for both
  favourites and underdogs, at both market-average and sharp (Pinnacle)
  prices (roughly −5% to −13% ROI; longshots far worse).
- A *bigger* model-vs-market disagreement produced a *worse* result — the
  disagreements are the model being wrong, not finding value.

So `value_finder.py` is a **research tool, not a betting signal**. What the
backtest *did* fix: the raw model was overconfident in favourites, so a
calibration scale (`PREDICTION_SCALE = 0.8`, derived by `backtest.py --tune`)
now makes its probabilities track reality on the hold-out. A plain surface-Elo
isn't enough to beat efficient tennis markets; better features (recent form,
fatigue, head-to-head, surface speed, injuries) would be the next step before
any real staking.

## Why tennis

No draws (binary outcome, simpler than football's three-way market), Elo
ratings are a much better fit than for football, year-round volume across
ATP/WTA tour events, and free match-history data is available to build
ratings from scratch.

## Setup

```
cp .env.example .env    # fill in ODDS_API_KEY (same key works for any
                        # sport on the-odds-api.com)
./fetch_data.sh         # ATP/WTA match history -> data/ (live model)
python3 fetch_odds_history.py   # results+odds -> data/ (backtest; needs openpyxl)
```

## Usage

```
python3 elo_predict.py --tour atp "Carlos Alcaraz" "Novak Djokovic" --surface Grass
python3 fetch_odds.py                      # list active tennis tournaments
python3 value_finder.py                    # scan active tournaments (research only)
python3 backtest.py --tour atp --tune      # calibration + ROI over history
```

`value_finder.py` skips matches that have already started by default
(`--include-live` overrides this) — the-odds-api returns live in-play prices
once a match begins, and those aren't comparable to a pre-match Elo
probability.

## Data

- Match history (live model): community mirror of Jeff Sackmann's ATP/WTA
  datasets (`LuckyLoser91/TennisCourtLog` — the original
  `JeffSackmann/tennis_atp` and `tennis_wta` repos are no longer public as of
  2026). Re-verify periodically that this mirror is still kept current.
- Historical results + closing odds (backtest): tennis-data.co.uk per-year
  spreadsheets (Pinnacle `PSW/PSL`, market average `AvgW/AvgL`).
- Live odds: the-odds-api.com, `h2h` market.

## Model

FiveThirtyEight-style Elo: `K = 250 / (matches_played + 5) ** 0.4`, with
separate overall and surface (Hard/Clay/Grass/Carpet) ratings blended at
prediction time, weighted toward the surface rating as surface-specific
sample size grows (up to 70% weight). Win probability is then scaled by a
calibration factor (`PREDICTION_SCALE`, see above). See `elo_predict.py` and
`backtest.py` for details.
