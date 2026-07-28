# The rating → probability method, and its limits

## What the model does

For each runner `rate.py` computes three probabilities:

- **market_prob** — the de-vigged market price. `1/odds` for every runner,
  renormalized so they sum to 1 (stripping the bookmaker overround). This is a
  strong, well-calibrated prior: markets are hard to beat.
- **figure_prob** — a softmax over each runner's *handicapping rating*. The
  rating starts from the recent speed figure and applies capped, modest
  adjustments (in figure points) for the factors below; softmax turns rating
  gaps into probability gaps, with `--temp` setting how decisively. Run
  `rate.py --explain` to see each runner's rating broken down.

  | Factor | Input field | Effect (capped) |
  |--------|-------------|-----------------|
  | Speed figure | `speed` | the base rating |
  | Recent form | `form` (last finishes) | +/- vs mid-pack |
  | Layoff | `days` | small penalty for long breaks |
  | Going / track suitability | `going_suit` (-2..+2) | ±5 |
  | Distance & surface suitability | `dist_suit` (-2..+2) | ±5 |
  | Jockey strike rate | `jockey_sr` | ±4 |
  | Trainer strike rate | `trainer_sr` | ±4 |
  | Weight carried | `weight` | ±5 (lighter = better) |
  | Pace shape | `pace` (E/EP/P/S) + field | lone speed +, duel penalises early, aids closers |
  | Draw bias | `draw` + race `draw_bias` | scaled to declared bias |

  Every adjustment is deliberately small and capped so no soft factor can
  outrun the speed figure, and the market anchor keeps the final number honest.
  Missing fields are simply skipped — a runner with only `speed` and `odds` is
  handled exactly as before.
- **model_prob** — the blend that we actually bet from:

  ```
  model_prob = market_weight * market_prob + (1 - market_weight) * figure_prob
  ```
  renormalized to sum to 1. Default `market_weight = 0.70` (market-heavy).

**Edge** for a runner is `model_prob * odds - 1`. Positive edge = the model
thinks the price is too big. The stake-sizer then applies the odds band and
Kelly.

## Why it's built this way

A pure figure model, unanchored, is a menace: it hands a 30/1 shot a 20% chance
because one past figure looked fine, producing hallucinated 500% edges. Anchoring
to the de-vigged market keeps every probability sane and makes real edges small
and rare — which is what genuine edges actually are.

## The dials

- `--market-weight` (default 0.70). Higher → trust the market, smaller/rarer
  edges, less model risk. Lower → trust your figures, bigger/more frequent
  edges, more risk of betting your own blind spots. Move it *down* only when you
  have evidence (from the journal) that your figures beat the market.
- `--temp` (default 8, in speed-figure points). Lower → the figure model is more
  decisive and, usefully, keeps clearly-slower horses near zero. Higher →
  flatter, which *re-inflates* longshots toward a false ~uniform chance. Keep it
  low unless you have a reason.

## The limitation you must not forget

The model can *use* form, going, connections, weight and pace — but only when
that data is actually gathered. For minor tracks it often isn't freely
available, and **an unfed factor is a blind spot, not a neutral**. If all you
have is speed + odds, the model is effectively "speed figure vs market price"
however many factor slots exist. `--explain` shows exactly which factors fired;
if it says `(none)`, do not pretend the pick reflects form or pace.

Never fabricate a factor to fill a slot. A guessed jockey strike rate or an
invented run style is worse than leaving it blank, because it launders a guess
into a confident-looking number. Gather it or omit it.

Even fully fed, the model can't see everything — trip trouble, intent, market
moves. So when a longshot shows a big "edge" purely because its lone figure is
close to a fancied rival's, that is almost always the **model's blindness, not
value**.

Guardrails that catch this:
- The **odds band** in stake-sizer skips runners outside your 2/1–6/1 zone,
  where most of these mirages live.
- A **double-digit edge on a liquid market is a red flag**, not a gift —
  sanity-check the probability before repeating it with confidence.
- The **journal** is the real referee: if the method has an edge, it shows up as
  positive CLV and ROI over dozens of bets, not in any single race.

## How to actually make it better

This is a transparent starting prior, not a trained model. The honest path to a
real, durable edge:

1. **Log every prediction** with `journal.py` (model_prob, odds taken, edge).
2. After the off, **settle** with the result and the **closing odds**.
3. Watch **CLV** first: consistently beating the closing line is the earliest
   sign the method finds real value, long before ROI is significant.
4. Check **calibration** in `journal.py report`: do ~40% calls win ~40%? If the
   model is over-confident (favourites winning less than predicted), raise
   `--temp` or `--market-weight`.
5. Once enough races are logged, **backtest** parameter choices against recorded
   results instead of tuning on a single card.
6. Only then consider replacing the heuristic with a model fit on historical
   data (surface, class, pace, connections) — the features the current model is
   blind to.
