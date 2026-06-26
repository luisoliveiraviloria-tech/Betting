# Club-league backtest of our engine

Our predictor is built for **national teams** (World Cup) off eloratings.net.
But its maths — Elo difference → goal supremacy → Poisson scoreline →
Dixon-Coles 1X2 probabilities — is league-agnostic. So we pointed the *exact
same model* (`club_backtest.py` imports `match_probabilities` and the
calibrated constants straight from `elo_predict.py`) at **club football** to
see, honestly, how it does against real results and real closing odds.

## Data (free, regenerable via `fetch_club_data.py`)

- **Results + closing odds**: football-data.co.uk, top-5 leagues (EPL, La
  Liga, Serie A, Bundesliga, Ligue 1), 2018/19–2024/25. We keep market-average
  (`Avg*`) and Pinnacle **closing** (`PSC*`) 1X2 odds.
- **Point-in-time club Elo**: clubelo.com per-club history (each match uses the
  rating valid on its date — no lookahead).

12,533 matches downloaded; ~12,100 scored (≈3% skipped for unmatched
clubs/missing Elo).

## Method

No lookahead. Parameters refit for clubs on 2018–2022, evaluated on a
**2023+ hold-out** (`--tune`): home-advantage 50 Elo pts, 190 Elo pts/goal
(basically identical to the international 190.7), total-goals baseline 2.4
(higher than internationals' 2.09 — club football outscores it, as expected).

## Result: a *good* model that still doesn't beat the market

On the 2023+ hold-out (4,480 matches), per league — model vs market 1X2
log-loss (lower = better) and value-bet ROI at Pinnacle closing odds:

| League | Model LL | Market LL | Model acc | ROI @ Pinnacle (edge≥0) |
|---|---|---|---|---|
| Premier League | 0.961 | 0.943 | 55.9% | −12.1% |
| La Liga | 0.980 | 0.965 | 54.4% | −3.7% |
| Serie A | 0.998 | 0.990 | 52.0% | −7.2% |
| Bundesliga | 0.976 | 0.953 | 52.6% | −1.8% |
| Ligue 1 | 1.019 | 1.004 | 51.1% | −6.8% |
| **All** | **0.985** | **0.969** | **53.3%** | **−5.9%** |

Takeaways:

1. **The engine is strong.** It lands within ~1.5–2% of the *sharpest*
   available line (Pinnacle closing) on log-loss, and on full-sample EPL its
   raw pick accuracy (55.3%) even edged the market (54.1%). Far closer to its
   market than the tennis Elo was to tennis markets (~7% behind there).
2. **It still does not beat the market.** ROI is negative in every league on
   the hold-out, and — the same signature we found in tennis — demanding a
   *bigger* model-vs-market edge makes ROI *worse*, not better. The
   disagreements are mostly the model being wrong, not finding value. After the
   bookmaker margin, "near the line" isn't "beating the line."
3. **Bundesliga (−1.8%) is the closest to break-even**; EPL the furthest
   (−12%), i.e. the EPL market is the hardest to disagree with profitably.

## Does it beat the EARLY line? (Closing Line Value test)

"Can't beat the *closing* line" isn't the whole question — winners beat the
*opening/soft* line and let it move to them. So we also bet the model's value
picks at the earlier Bet365 price and scored them against the sharp Pinnacle
**close** (Closing Line Value). Positive CLV = the model grabs prices the sharp
market later shortens = a real edge, and it's far less noisy than ROI.

2023+ hold-out, all leagues, ~6,600 value picks:

| edge ≥ | CLV | beat-close rate | realised ROI (Bet365 early) |
|---|---|---|---|
| 0pp | **−5.7%** | 33.2% | −8.0% |
| 3pp | −6.1% | 32.3% | −12.1% |
| 5pp | −6.4% | 30.9% | −18.7% |

**CLV is negative across the board**, and only ~1 in 3 picks beat the close —
i.e. when the model calls the early price "value", the sharp market more often
moves *against* us by kickoff. That's independent confirmation the
disagreements are the model being wrong, not early value. (Caveat: football-
data's Bet365 column is an *early-ish* snapshot, not the literal minute-one
opening price, so the very softest opening lines aren't captured here.)

## Bottom line

A well-built public Elo+Poisson model reproduces top-5-league prices closely
but beats neither the closing line nor the early line — negative ROI and
negative CLV. To actually profit you'd need an input the market lacks (team
news/lineups before they're priced, in-play, genuinely soft markets) or a
non-predictive edge (arbitrage, promotions) — not a better fit on public data.
Same lesson the tennis backtest taught, now confirmed two ways on football.

## Usage

```bash
python3 fetch_club_data.py                       # download results+odds & club Elo
python3 club_backtest.py                          # full-sample, our intl constants
python3 club_backtest.py --tune                   # refit for clubs, 2023+ hold-out
python3 club_backtest.py --league E0 --test-only  # one league, hold-out only
```
