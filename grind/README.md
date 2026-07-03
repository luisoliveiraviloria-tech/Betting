# grind — slow-and-steady football value betting

£100 starting bankroll. Betfair Sportsbook/Exchange. Odds ≥1.50. Leagues:
World Cup, EPL, La Liga, Brazil Série A, Argentina Primera.

**Edge source** (the only one that survived our backtests): a bettable book
paying MORE than Pinnacle's de-vigged fair price. We do not out-predict the
market — we shop mispriced lines against the sharp consensus.

## Workflow

```
python3 scanner.py                        # base scan: 1X2, goals O/U, Asian handicap
python3 scanner.py --deep --hours 36      # + BTTS, cards O/U, corners O/U (near games)
python3 scanner.py --check "Mexico" --market spreads --side "England -0.25" --price 2.05
python3 scanner.py --check "Mexico" --market btts --side Yes --price 1.85
python3 scanner.py --check "Mexico" --market cards --line 3.5 --side Over --price 3.0
python3 context.py --team "Canada" --date 2026-07-04   # referee, stadium, weather
python3 ledger.py add ...                 # log what you actually placed
python3 ledger.py settle --id N --result win|loss|void
python3 ledger.py status
```

Markets: 1X2, goals totals, **Asian handicap** in the base scan; **BTTS,
cards O/U, corners O/U** via `--deep` / `--check`. The feed only carries
Betfair *Exchange* 1X2, so for AH/BTTS/cards/corners compare your Betfair app
price with `--check` (Pinnacle fair exists for all of them).

`context.py` adds referee / stadium / kickoff weather (API-Football +
open-meteo). Context is a veto/sanity layer, not a signal: referee card-rate
is the only context factor our backtests validated (cards markets); wind/rain/
altitude are flags, mostly already priced into lines. API-Football free plan
only serves a ±few-day fixture window.

Rules baked in: EV ≥3% vs Pinnacle fair, odds 1.50–6.00, quarter-Kelly
stakes capped at 5% of bankroll, and **check the price at placement** — if it
has shortened below +EV, skip. No bet is a position.

## Honest expectations — read this

The target of 10%/week or 20%/month is **not achievable sustainably**, and
chasing it is how bankrolls die. The math:

- 10%/week compounds to ~142x in a year. Nobody does this at fixed odds.
- 20%/month is ~9x/year. World-class syndicates make 2–10% ROI *per bet
  turnover*, not per month on bankroll, and they're capped by liquidity.
- Our real, tested edge is +3–6% EV on occasional overlays, quarter-Kelly
  staked (~£0.25–£3 bets on £100). Realistic outcome: **single-digit % growth
  per month with heavy variance**, including losing months. On a £100 roll,
  luck dominates skill for hundreds of bets.
- Soft books (Betfair Sportsbook) limit winning accounts. The Exchange
  doesn't, but its prices are sharper, so overlays are rarer and smaller.

What compounds a small bankroll fastest is not bigger stakes — it's bonuses/
free bets converted at +EV (your £2.90→£31 fourfold did more than any model
here), plus disciplined overlay betting as the base.

## Files

- `config.py` — bankroll/odds/EV/staking parameters
- `scanner.py` — 5-league scan vs Pinnacle fair (h2h + goal totals, ~20 API
  credits per full scan; 500/month on the free the-odds-api plan ≈ 1 scan/day)
- `ledger.py` — bankroll state + bet log + settlement
- Cards markets: use `../cards-corners/value_scanner.py` (same method)
