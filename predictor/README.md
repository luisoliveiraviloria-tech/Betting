# Elo predictor

Predicts national-team match outcomes from real World Football Elo ratings,
instead of the crude goals-for/against model used previously (which ignored
opponent strength and ended up badly overrating teams with lopsided friendly
results, e.g. Canada after a 6-0 friendly win over a weak Qatar side).

## Data sources (free, no API key required)

- [`eloratings.net`](https://www.eloratings.net/) — current World Football
  Elo rating and rank for every national team (`data/elo_world.tsv`,
  `data/elo_teams.tsv` for name/code lookup).
- [`martj42/international_results`](https://github.com/martj42/international_results)
  (CC0) — full international match history since 1872, kept as a local
  snapshot for reference/sanity-checking.

Run `./fetch_data.sh` to refresh both snapshots in `data/`.

## Method

1. Look up each team's current Elo rating.
2. Convert the rating difference (plus an optional home-advantage bonus, 0
   for neutral-venue matches) into an expected goal supremacy
   (`diff / ELO_POINTS_PER_GOAL`, calibrated by `calibrate.py`).
3. Split a baseline total-goals expectation (also calibrated, scaling up for
   mismatched teams) into home/away Poisson scoring rates accordingly.
4. Build the joint home/away scoreline grid with a Dixon-Coles (1997)
   tau adjustment (`RHO` in `elo_predict.py`, also calibrated) that corrects
   independent Poisson's known under-prediction of low-score draws
   (0-0/1-1) and over-prediction of 1-0/0-1, then sum it into Home/Draw/Away
   win probabilities. `value_finder.py`'s Over/Under markets use the same
   joint grid, so the two markets stay consistent with each other.

## Usage

```bash
python3 elo_predict.py --home Canada --away Switzerland --home-adv 100
python3 elo_predict.py --home "Bosnia and Herzegovina" --away Qatar
```

`--home-adv` is an Elo-point bonus added to the home team's rating before
computing goal supremacy — use ~100 for a genuine home fixture, 0 (default)
for a neutral-venue match (the usual case at a World Cup).

## Live fixtures + odds (requires free API keys)

Three more data sources, gated by API keys you provide yourself:

- [football-data.org](https://www.football-data.org/) — live World Cup
  fixtures, scores, and group standings. Free tier does not include odds.
- [the-odds-api.com](https://the-odds-api.com/) — live bookmaker odds
  (h2h/1X2 market), including Betfair (`betfair_ex_uk`), across many regions.
- [api-football (api-sports.io)](https://dashboard.api-football.com/register) —
  starting lineups and injury reports, free tier (100 req/day, no card).
  Neither of the two sources above expose this, which matters: a key
  striker ruled out an hour before kickoff changes the model's implied
  goals more than most Elo-gap noise does.
  **Free-tier limitation (confirmed by testing, not in their docs):** the
  `date` filter only accepts roughly yesterday/today/tomorrow — fine for
  checking a match you're about to bet on, useless for browsing other
  matchdays in advance. `fetch_lineups.py` also deliberately never sends
  `&season=`, since the free plan rejects that param for the live season
  even though the same fixtures come back fine from a plain date query.

Setup: `cp .env.example .env` and fill in `FOOTBALL_DATA_API_KEY`,
`ODDS_API_KEY`, and `API_FOOTBALL_KEY`. `.env` is gitignored — never commit
real keys.

```bash
python3 fetch_fixtures.py --matchday 3
python3 fetch_odds.py --bookmaker betfair_ex_uk
python3 fetch_lineups.py --home Brazil --away Argentina --date 2026-06-25
python3 live_report.py --matchday 3 --bookmaker betfair_ex_uk --check-injuries
python3 value_finder.py --check-injuries
```

`live_report.py` fetches fixtures and odds, runs the Elo model on each
fixture, and prints model probabilities next to the market's de-vigged
implied probabilities so you can spot where the model and the market
disagree. `--check-injuries` adds an API-Football lookup per fixture shown
(lineups are usually only published ~1h before kickoff; injury reports are
available earlier) — pair it with `--matchday` to avoid burning the free
quota across a whole tournament's fixtures. `value_finder.py
--check-injuries` does the same but only for the fixtures that make the
final top-N list, since that's already a much smaller set.
