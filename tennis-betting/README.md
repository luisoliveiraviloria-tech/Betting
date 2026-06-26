# tennis-betting

ATP/WTA value-betting tool. Builds surface-aware Elo ratings from free match
history, fetches live h2h odds, de-vigs them, and ranks bets by edge between
the Elo model's win probability and the market's implied probability.

Kept as a separate project from the football/World Cup betting system on
purpose: different sport, different model (binary Elo vs. Poisson/Dixon-Coles),
no shared fixtures API, and the football repo was already getting crowded.

## Why tennis

No draws (binary outcome, simpler than football's three-way market), Elo
ratings are a much better fit than for football, year-round volume across
ATP/WTA tour events, and free match-history data is available to build
ratings from scratch.

## Setup

```
cp .env.example .env   # fill in ODDS_API_KEY (same key works for any
                        # sport on the-odds-api.com)
./fetch_data.sh         # downloads ATP/WTA match history into data/
```

## Usage

```
python3 elo_predict.py --tour atp "Carlos Alcaraz" "Novak Djokovic" --surface Grass
python3 fetch_odds.py                      # list active tennis tournaments
python3 value_finder.py                    # scan all active tournaments for value bets
python3 value_finder.py --top 5 --min-odds 1.5 --max-odds 3.0
```

`value_finder.py` skips matches that have already started by default
(`--include-live` overrides this) — the-odds-api returns live in-play prices
once a match begins, and those aren't comparable to a pre-match Elo
probability.

## Data

- Match history: community mirror of Jeff Sackmann's ATP/WTA datasets
  (`LuckyLoser91/TennisCourtLog` — the original `JeffSackmann/tennis_atp` and
  `tennis_wta` repos are no longer public as of 2026). Re-verify periodically
  that this mirror is still kept current.
- Odds: the-odds-api.com, `h2h` market.

## Model

FiveThirtyEight-style Elo: `K = 250 / (matches_played + 5) ** 0.4`, with
separate overall and surface (Hard/Clay/Grass/Carpet) ratings blended at
prediction time, weighted toward the surface rating as surface-specific
sample size grows (up to 70% weight). See `elo_predict.py` for details.
