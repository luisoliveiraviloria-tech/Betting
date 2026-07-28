---
name: race-predictor
description: >-
  End-to-end horse-racing prediction workflow: the user gives a TIME and a PLACE
  (e.g. "who do you fancy in the 21:10 at Fairmount Park?", "find me a winner for
  the 4:20 Ascot", "21:10 Fairmount") and this produces the most probable
  outcome, a market-anchored value screen, a Kelly-sized stake, and a journal
  entry. Use this WHENEVER the user names a race by time and course and wants a
  selection, a prediction, a tip, a "who wins", or asks you to look at a card —
  even casually. It orchestrates racecard gathering, the rating→probability
  model (rate.py), the stake-sizer, and the bet journal (journal.py). Do not use
  it to invent picks without data, and it is for horse racing, not other sports.
---

# Race Predictor

The user's loop: **they give a time + place, you return the most probable
outcome, then you decide the stake together.** This skill is the harness that
makes that repeatable and honest. It chains four pieces:

1. **Gather** the racecard (runners, live odds, recent figures/form).
2. **Rate** them into win probabilities — `scripts/rate.py`.
3. **Size** the bet with Kelly + the odds band — the sibling `stake-sizer` skill.
4. **Record** the prediction and settle the result — `scripts/journal.py`.

## Ground rules (read first)

- **Nobody predicts winners reliably, including this skill.** You produce the
  *most probable* runner and whether it's *value* — not a guarantee. Say so.
  Favourites at 7/5 lose ~40% of the time.
- **Never fabricate data.** No runners, figures, or odds you didn't actually
  pull. Missing data is fine; invented data poisons everything downstream. If
  you cannot get a real card, say so and stop.
- **Most probable ≠ value.** The likeliest winner is very often too short to
  bet. That's expected and correct — surface it plainly.
- The model is a **transparent heuristic anchored to the market**, not a trained
  model. Real edges are small and rare. Big edges are usually its blind spots —
  see `references/method.md`.

## Workflow

### 1. Gather the card
Resolve the time + place to a specific race and extract, per runner: `name`,
current `odds`, and as many of `speed`, `form`, `days` as the sources provide.
Read `references/sources.md` for where to look and how to handle the UK/US time
labels (UK betting sites list US evening cards in UK time). Cross-check race
number, distance, surface and field size across two sources.

Write a card JSON:
```json
{"runners": [
  {"name": "Steampunk",   "odds": "7/5",  "speed": 117, "form": [1,2,1], "days": 21},
  {"name": "Crushed Ice", "odds": "3/2",  "speed": 91}
]}
```

### 2. Rate → probabilities
```bash
python3 scripts/rate.py --race card.json --out race.json
```
Prints each runner's market prob, figure prob, blended **model prob**, and
**edge**, names the **most probable** runner, and writes `race.json` for staking.
Dials (`--market-weight`, `--temp`) and their trade-offs are in
`references/method.md`. Defaults are deliberately market-heavy; don't loosen them
without evidence from the journal.

### 3. Size the bet
```bash
python3 ../stake-sizer/scripts/stake.py --bankroll <£> --race race.json \
    --kelly-fraction 0.25
```
This applies the user's 2/1–6/1 odds band and Kelly. If nothing clears both the
edge test and the band, the honest output is **no bet** — passing is a position.
Present full and scaled (fractional-Kelly) stakes; lead with the scaled figure
unless the edge is rock-solid, because full Kelly overstakes on any probability
error.

### 4. Present, then record
Give the user: the most probable runner (with model vs market prob), whether
it's a value bet in their band, and the suggested stake — then let them decide.
Once they've placed a bet, log it:
```bash
python3 scripts/journal.py log --track "Fairmount Park" --time 21:10 --race 5 \
    --selection "Steampunk" --odds 7/5 --stake 5 --model-prob 0.49 --edge 0.06
```
After the result, settle it (add closing odds when you can — CLV is the best
early signal of a real edge):
```bash
python3 scripts/journal.py settle --id 1 --result win --closing-odds 6/4
python3 scripts/journal.py report      # strike rate, ROI, CLV, calibration
```

## What "good" looks like over time

Judge the method by the **journal**, never by one race. You want positive
**closing-line value** first (you keep taking bigger prices than the market
settles on), then positive **ROI** across dozens of bets, and **calibration**
that holds (things called 40% win about 40%). `journal.py report` shows all
three. If favourites win less than the model predicts, it's over-confident —
raise `--temp` or `--market-weight`. Read `references/method.md` for the full
improvement loop.

## Bundled resources
- `scripts/rate.py` — racecard → win probabilities → `race.json`.
- `scripts/journal.py` — log / settle / report bets (`<repo>/journal/bets.csv`).
- `references/sources.md` — where to get cards, odds, form; time-zone handling.
- `references/method.md` — the model, its dials, its blind spots, how to improve.
- Staking lives in the sibling **stake-sizer** skill.
