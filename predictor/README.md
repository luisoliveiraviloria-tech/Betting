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
   (`diff / 200`, a standard Elo-to-goals approximation).
3. Split a baseline total-goals expectation (2.6, typical for a competitive
   international match) into home/away Poisson scoring rates accordingly.
4. Sum the Poisson scoreline grid into Home/Draw/Away win probabilities.

## Usage

```bash
python3 elo_predict.py --home Canada --away Switzerland --home-adv 100
python3 elo_predict.py --home "Bosnia and Herzegovina" --away Qatar
```

`--home-adv` is an Elo-point bonus added to the home team's rating before
computing goal supremacy — use ~100 for a genuine home fixture, 0 (default)
for a neutral-venue match (the usual case at a World Cup).
