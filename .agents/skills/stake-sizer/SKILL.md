---
name: stake-sizer
description: >-
  Work out how much to stake on a horse-racing bet using the Kelly criterion,
  sized from model win-probabilities and market odds. Use this WHENEVER the
  user asks how much to bet/stake/back a horse for, mentions Kelly, bankroll
  allocation, unit or position sizing, "how much should I put on X", staking
  plans, or wants to turn a set of runner probabilities and prices into a
  concrete stake. Also use it to screen a race card for value (positive-EV)
  selections in a target odds range. Reach for this skill even when the user
  only implies sizing — e.g. "I fancy the 3/1 shot in the 4:20, my bank is
  £500" — because turning an edge into a correctly-sized, drawdown-aware stake
  is exactly what it is for. Not for football/other-sport accumulators unless
  the user frames them as single-outcome win bets.
---

# Stake Sizer (Kelly, horse racing)

This skill turns *an estimated edge* into *a correctly-sized stake*. It is the
staking half of a betting workflow — it does not pick horses. It assumes you
(or a model) can supply a win probability for each runner, and it tells you how
much of the bankroll to risk.

The aim it is tuned for: **finding and sizing steady 2/1–6/1 value bets**, not
chasing longshots. The default odds band reflects that.

## The mental model

A race is a set of **mutually exclusive** runners — exactly one wins. Two things
follow, and both are baked into the calculator so you don't have to think about
them each time:

- **Your model's probabilities should sum to ~1 across the field.** If they
  don't (e.g. they're raw ratings or speed figures), the tool normalizes them.
  Sizing off probabilities that secretly sum to 1.4 invents edge that isn't
  there.
- **The market is over-round.** Bookmakers price so that `Σ (1/odds) > 1` (a
  typical book is 110–130%). The excess is the vig. De-vigging the market gives
  its *own* fair probability estimate, which is the honest thing to compare your
  model against — if your number barely beats the de-vigged market number, the
  "edge" is probably just vig you haven't accounted for.

**Edge** (expected value per unit staked) for a runner is `p * o - 1`, where `p`
is your model win probability and `o` the decimal odds. Positive edge means the
bet is +EV *by your model*. No edge, no bet — passing a race is a position.

**Full Kelly** stakes `f = edge / (o - 1)` of the bankroll. It maximizes long-run
growth, but it is deliberately aggressive: it assumes your `p` is exactly right,
so a single over-estimated probability overstakes hard, and even when you're
right the equity curve swings through deep drawdowns. That is why every output
also shows a **scaled (fractional Kelly)** column. Treat full Kelly as the
ceiling; a quarter-to-half stake keeps most of the growth for a fraction of the
volatility. Recommend the scaled figure unless the edge is genuinely rock-solid.

## Workflow

1. **Gather inputs.** For each runner you need a model win probability (or a raw
   model score / rating — the tool will normalize scores into probabilities) and
   the current decimal or fractional market odds. Probabilities come from
   whatever handicapping model or ratings the user has; this repo does not yet
   ship a horse-racing model, so do not fabricate probabilities — if the user
   hasn't given you any and there's no model output to read, ask for their
   estimate or their ratings rather than guessing.

2. **Confirm the bankroll and, if the user has a preference, the Kelly
   fraction.** Default to full Kelly (`--kelly-fraction 1.0`) since that is what
   the user configured, but always surface the scaled column and, when the stake
   is large, actively suggest scaling down.

3. **Run the calculator.** Build a small JSON file for the race (preferred — it
   enables field normalization and de-vig) or pass a single bet directly:

   ```bash
   # whole race
   python3 scripts/stake.py --bankroll 500 --race race.json --kelly-fraction 1.0

   # single selection, fractional odds accepted
   python3 scripts/stake.py --bankroll 500 --prob 0.34 --odds 3/1
   ```

   `race.json`:
   ```json
   {"runners": [
     {"name": "Ballydoyle",  "model_prob": 0.34, "odds": 3.5},
     {"name": "Turf Master",  "model_prob": 0.22, "odds": "9/2"},
     {"name": "Steady Eddie", "model_prob": 0.20, "odds": 4.5}
   ]}
   ```
   Use `"model_score"` instead of `"model_prob"` to pass raw ratings that should
   be normalized across the field.

4. **Read the result to the user.** The tool prints, per runner: model prob,
   de-vigged market prob, edge %, full-Kelly %, full-Kelly stake, scaled stake,
   and a note (`VALUE`, `no edge — skip`, or `outside band — skip`). It then
   makes a single recommendation: the highest-edge runner that clears both the
   edge test and the odds band, with full and scaled stakes and a variance
   reminder.

## Options that matter

- `--kelly-fraction 0.25` — scale every stake (0.25 = quarter Kelly). The single
  most useful risk dial.
- `--min-odds` / `--max-odds` — the odds sanity band in **decimal** (2/1 = 3.0,
  6/1 = 7.0; these are the defaults). Runners priced outside it are flagged and
  skipped so you stay in the steady zone and don't get lured onto longshots.
  Widen or narrow it if the user's strategy differs, but don't remove it
  silently — the band is a guardrail the user explicitly asked for.
- `--simultaneous` — when more than one runner in a race shows value, compute the
  log-optimal *joint* stake. Runners are mutually exclusive, so naively applying
  each runner's individual Kelly stake overbets the race; this solves for the
  correct combined allocation.
- `--json` — machine-readable output, for piping into a journal/record step.

## Interpreting edge honestly

Full Kelly turns a small probability error into a large staking error, so the
edge number is only as trustworthy as the probability behind it. Guard against
false confidence:

- If the model edge and the de-vigged market barely differ, treat it as **no
  real edge** — you're likely just seeing noise or un-removed vig.
- Double-digit edges on a liquid market are almost always a modelling error, not
  a gift. Sanity-check the probability before repeating the stake back with
  confidence.
- When in doubt, quote the scaled (fractional-Kelly) stake as the headline
  number and mention full Kelly only as the ceiling.

The goal is a steady grind on genuine 2/1–6/1 value, sized so a normal losing
run doesn't cripple the bank — not the biggest theoretically-optimal bet.
