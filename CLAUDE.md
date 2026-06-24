# Betting analysis — conventions and known mistakes

## Reading odds movement direction (do not repeat this error)

Decimal odds and implied probability move in **opposite** directions:

- Odds **shortening** (decimal number going DOWN, e.g. 2.55 → 2.20) means the
  market now thinks that outcome is MORE likely. Implied probability = 1/odds,
  so a lower decimal number is a HIGHER probability.
- Odds **lengthening** (number going UP) means the market thinks it's LESS
  likely.

When comparing a live odds snapshot to an earlier one for the same
bookmaker/selection, always restate the comparison in implied-probability
terms before drawing a conclusion about "the market moving for/against" a
bet. Do not eyeball the raw decimal numbers and assume a falling number is
bad news for a backer — it is the opposite. This caused a real error on
2026-06-24 (Bosnia-Herzegovina vs Qatar, Under 2.5 goals): a drop in the
Under price from ~2.55 to ~2.20 across multiple bookmakers was initially
(incorrectly) reported as the market moving "against" the Under bet, when it
was actually moving in its favor. Always sanity-check by computing/comparing
de-vigged implied probabilities (already done by `value_finder.py`'s
`market_prob` field) rather than reasoning from raw odds deltas alone.
