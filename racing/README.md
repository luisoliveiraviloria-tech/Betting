# UK & Irish Horse Racing — probability, value and staking engine

A quantitative system that scores every runner in UK and Irish racing, compares
its probability against the Betfair Exchange price, and proposes only those bets
with positive expected value after commission — with a readable case for each
based on form, ratings, jockey/trainer, course and the rest of the field.

> **Status: working and validated out of sample.**
> On 2024–2026 data the model never saw: **911 bets, 42.3% strike, +24.3% ROI
> after commission (95% CI +14.5% to +34.0%, t = 4.88)**, ~1 bet/day, positive in
> all three years. See [Results](#4-results) and
> [Bankroll](#5-bankroll-how-big-does-it-need-to-be).
>
> The one thing missing for daily live use is a racecard feed — see
> [Limitations](#7-honest-limitations) item 4.

---

## 1. Quick start

```bash
quant/.venv/Scripts/python.exe -m racing.daily --date 2025-12-03 --bankroll 250
```

```
  15:12 Haydock  20.0f Chase  (field 9)
  >> Fat Harry (GB)
     price    2.60   model  42.6%   fair odds   2.35   market  38.5%
     EV  +7.4% after 5% commission   edge +4.2%   BET DOWN TO 2.56
     stake £3.04  (1.22% of bankroll, 1/4 Kelly)
     case: Ratings +0.34; Class & weight +0.03; Recent form -0.03
      drivers: RPR above official mark (+0.28); OR rank in field (+0.02)
```

Full rebuild from raw data (~45 min):

```bash
python -m racing.bsp_data --start 2016-01-01   # Betfair prices -> racing.db
python -m racing.clean                         # Kaggle form -> runs table
python -m racing.features                      # as-of features
python -m racing.check_features                # leakage gates (MUST pass)
python -m racing.market                        # join prices to form
python -m racing.calibrate                     # market baseline
python -m racing.model                         # walk-forward kill test
python -m racing.backtest --split-year 2024    # validated strategy
python -m racing.train_final                   # production model
```

## 2. How it works

| Module | Job |
|---|---|
| `bsp_data.py` | Downloads Betfair's free daily BSP CSVs (2016→today) into `data/racing.db`. Throttled, resumable, WAL. |
| `clean.py` | Parses the Kaggle form archive into a typed UK+IRE `runs` table. |
| `features.py` | 74 **as-of** features — nothing that was not knowable before the off. |
| `check_features.py` | Safety gates: parser tests, truncation leakage test, target guard, automated leak scan. |
| `market.py` | Joins BSP prices to form (99.5% coverage, 0 course mismatches, 99.96% winner agreement). |
| `calibrate.py` | Measures how good the market itself is — the bar to beat. |
| `model.py` | The model and the kill test. |
| `ev.py` | EV, minimum acceptable price, Kelly staking, qualification gates. |
| `backtest.py` | Threshold selection on early years, validation on later ones, ruin analysis. |
| `explain.py` | SHAP contributions grouped into form / ratings / jockey / course / rivals. |
| `daily.py` | The product: a bet card for a given day. |

### The model is a correction to the market, not a replacement

The Betfair price is treated as a **prior**, not an opponent. LightGBM is trained
with the market's log-odds as `init_score`, so every tree is a *correction* to the
price, and early stopping decides how much correction is justified. With zero
trees the output is exactly the market probability. Typically only **20–70 trees**
survive — the model may disagree with the market only where the form data earns it.

Probabilities are normalised within each race so they sum to 1, because exactly
one horse wins.

The corrections it actually learns, by importance: **RPR above official mark**
(an improving horse), change in official rating, last run's finishing position,
rating rank within the field, draw, days since last run, trainer/jockey recent
form. These are the things a form analyst looks at, which is a reassuring sign
the model is picking up racing signal rather than noise.

## 3. Why the market is a hard benchmark

From `calibrate.py`, over 708k runners:

- The Betfair BSP book sums to **1.0018** — a 0.18% margin. (Bookmaker SP: **1.168**.)
- It is **almost perfectly calibrated**: in 11 of 12 price bands the actual win
  rate sits inside the 95% CI of the implied probability.
- **No segment is profitable** — handicaps, jumps, all-weather, Ireland, every
  field size, favourites-only: all lose ~4–7%.

A subtlety that is easy to get wrong: **break-even is not 0% ROI.** Commission is
charged only on winnings, so a perfectly fair price still returns
`-commission × (1 − strike)` ≈ **−4.5%**. Backing everything at BSP returns
−4.91%, so the market's genuine edge over a naive bettor is only ~0.45%.

## 4. Results

### 4a. The kill test (walk-forward log loss)

Train on earlier seasons only, test on the held-out year. The blend beats the
market in **every properly-trained year** (2018–2026), but by a small margin
(e.g. 2024: market 0.28820 vs blend 0.28783). Form features **alone** are much
worse than the market (~0.305 vs ~0.288) — exactly what honest features look
like, because the market already prices in nearly everything public.

That small average improvement is not the product. It concentrates into something
usable on the minority of runners where model and market disagree — which is what
the betting gates select.

### 4b. The decisive diagnostic

On the runners the system actually bets, whose probability matches reality?

| | model says | market says | **actual** |
|---|---|---|---|
| selection 2017–23 (n=6,418) | 0.406 | 0.343 | **0.396** |
| validation 2024–26 (n=911) | 0.372 | 0.329 | **0.423** |

The market **under-prices these runners by 6–9 percentage points**, and the model
captures most of that gap. Note the model is *under*-confident on validation
(0.372 against 0.423 actual). Overfitting produces the opposite sign — model
above actual — so this asymmetry is the strongest single piece of evidence that
the edge is real rather than fitted noise.

### 4c. Validated strategy

Gates: `EV ≥ 6%`, `price ≤ 4.0`, `traded volume ≥ £500`. Chosen by a rule fixed
**before** looking at validation ROI: highest selection-period compound growth
among configurations giving ≥2 bets/day on selection with a price cap ≤4 (the cap
is variance control — a 42% strike survives drawdowns an 18% strike does not).

| | n | per day | strike | mean price | ROI | 95% CI | t |
|---|---|---|---|---|---|---|---|
| selection 2017–23 | 6,418 | 2.51 | 39.6% | 3.08 | +12.18% | +8.7% … +15.7% | 6.76 |
| **validation 2024–26** | **911** | **1.03** | **42.3%** | **3.19** | **+24.28%** | **+14.5% … +34.0%** | **4.88** |

Per validation year: 2024 **+22.2%** (n=516) · 2025 **+23.2%** (n=322) · 2026
**+43.4%** (n=73). Positive in all three — not one lucky year.

Robustness: **all 23** threshold configurations tested were positive on
validation, t-statistics 3.1 to 5.8. The result does not hinge on one lucky
choice of gates.

## 5. Bankroll: how big does it need to be?

Ruin is a stake-size problem, not an edge problem. Betfair's minimum stake is £2,
so on a £20 bank every bet is ≥10% of the roll.

Flat 2% staking (£2 floor), simulated on the validation bets:

| bankroll | stake | P(ruin) | end of validation period | max drawdown |
|---|---|---|---|---|
| £20 | £2 (10%) | **9.4%** | £462 | **82%** |
| £50 | £2 (4%) | ~0% | £492 | 33% |
| £100 | £2 (2%) | ~0% | £542 | 21% |
| £250 | £5 (2%) | ~0% | £1,356 | 21% |

Compounding at 1/4 Kelly: £100 → £1,543; £250 → £6,152 over the same 2.4 years.

**£20 does survive about 90% of the time** — but through an 82% drawdown, meaning
at the worst point the bank is down to roughly £3.50 and you need the discipline
to keep betting. **£100+ is strongly recommended**: same edge, a fifth of the pain.

## 6. What the daily card gives you

For every qualifying runner: model probability, fair odds, the price available,
EV after commission, the edge over the market, a recommended stake, and the
reasoning split into form / ratings / jockey & trainer / course & going / rivals.

`BET DOWN TO` is the point of the whole exercise: below that price the bet stops
being worth making, so a stale recommendation cannot be acted on blindly.

**`NO BET` is a normal output.** At ~1 qualifying bet per day across all UK and
Irish racing, many days produce nothing. The card then shows the near-misses and
which gate each failed.

## 7. Honest limitations

1. **Sparse.** ~1 bet/day across all UK and Irish racing.
2. **The CI is wide.** +24.3% has a 95% interval of +14.5% to +34.0%. The true
   long-run edge is likely lower than the point estimate, and selection optimism
   from the threshold sweep means the honest planning figure is nearer the bottom
   of that range.
3. **Racing Post ratings are decaying at source.** `rp_rpr_minus_or` is the
   model's single strongest feature, and RPR coverage has fallen from ~95% to
   ~75–80% since Oct 2025 (removed from The Racing API in June 2026). **This is
   the biggest threat to the system's future.** Benchmark with and without the
   `rp_` features before relying on it going forward.
4. **No live data feed yet — the one thing standing between this and daily use.**
   The form archive ends 2026-06-03, so `--date` only replays historical days.
   Going live needs today's racecards (The Racing API Basic, £27.99/mo) and
   current prices. `daily.py --prices` already accepts a `horse,price` CSV, so
   the remaining work is the racecard feed.
5. **Replaying a past date is in-sample.** The production model is trained on all
   data to 2026-06-03, so a card for an earlier date is not an out-of-sample
   result. The honest numbers are in §4c, from walk-forward.
6. **Prices are BSP.** Betting earlier means a worse-informed price; the morning
   price scores measurably worse than BSP (11.2% vs 13.4% of no-information log
   loss removed).
7. **2017 fold is unreliable** — too little prior data to early-stop, so it
   trained unvalidated and overfit. Now capped at 40 rounds; it sits in the
   selection period only and does not affect validation.
8. **Licensing.** The Betfair CSVs and the Kaggle archive are both for private
   research. Do not redistribute.
9. **Nothing here is financial advice, and past performance is not a guarantee.**

## 8. Data provenance

- **Prices:** Betfair free daily BSP CSVs — result, BSP, pre-race and morning
  weighted averages, traded volume. 2016→today, 7,832 files.
- **Form:** Kaggle `deltaromeo` archive — 1.35M UK+IRE runners, 2015→2026-06-03,
  with going, distance, class, draw, weight, official rating, RPR, Topspeed,
  jockey, trainer, pedigree.
- Joined on race date + normalised horse name, validated by course (0 mismatches)
  and winner agreement (99.96%).

## 9. Guard rails (do not remove)

`check_features.py` must pass before any model is trusted:

- **Truncation test** — rebuilding features from truncated data must not change a
  single earlier value. Catches future-information leaks.
- **Automated leak scan** — picks the best runner in each race by *every* feature
  in turn; nothing knowable pre-race should beat the SP favourite (~34%).

This scan exists because a hand-written check missed `prize` — which is the prize
money the runner *won*, a post-race outcome. The biggest prize in a race belongs
to the winner 99.8% of the time, and including it made the model look **96%
better than the market**. A curated checklist will not catch the next one; the
automated scan over all features is the real defence.
