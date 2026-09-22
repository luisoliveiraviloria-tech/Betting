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
> The live path is now built (`racing/live.py`) and gated by
> `python -m racing.test_live`. What remains before real money is an API key and
> the two acceptance checks in [Going live](#6a-going-live).

---

## 1. Quick start

Today, live:

```bash
python -m racing.racingapi --probe                  # does this key still carry RPR?
python -m racing.backfill --today                   # yesterday's results -> archive
python -m racing.features                           # rebuild as-of features
python -m racing.live --source racingapi --prices betfair --bankroll 100
```

Replaying a past day from the archive:

```bash
python -m racing.daily --date 2025-12-03 --bankroll 250
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
python -m racing.backfill --from 2026-06-04    # close the gap to today (Standard plan)
python -m racing.test_live                     # live-path gates (MUST pass)
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
| `daily.py` | A bet card for a past day, replayed from the archive. |
| `racingapi.py` | The Racing API client — racecards, results, and `--probe` (is RPR still populated on your plan?). |
| `sources.py` | Any feed → the archive's **raw** column shape → the same `clean_runs()` as training. No parser is re-implemented, so nothing can drift. |
| `exchange.py` | Betfair Exchange live prices: best back, depth, traded volume. |
| `backfill.py` | Extends the `runs` archive with new results, replacing races rather than duplicating them. |
| `live.py` | **The product**: today's card + today's prices → bets. |
| `test_live.py` | Gates for the live path (parsers, identity, as-of, backfill, book, scoring). |

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

## 6a. Going live

Two things feed a live card: **today's declarations** and **today's prices**.

**Declarations** come from The Racing API. The fields the model needs — official
rating, draw, weight, going, class, sire/dam, jockey, trainer — are all on the
**free** tier (`/v1/racecards/free`; verified against the vendor's public OpenAPI
spec, v1.4.4). Set `RACING_API_USERNAME` / `RACING_API_PASSWORD`.

**Prices** come from the Betfair Exchange (`racing/exchange.py`, reusing
`quant/betfair_auth.py`). The interactive login is blocked from datacenter IPs, so
run this from the machine you bet on or register a client certificate. Any other
price source works too: `--prices file.csv` with `horse,price`.

**The archive is the part that goes stale, and it is the part that matters.**
`rp_rpr_minus_or` is built from the horse's *previous* run, so a live card needs
RPR on races **already run**, not on today's card. Every day the archive is behind
is a day of runners whose strongest feature is missing. `racing.live` prints how
stale it is; `racing.backfill --today` closes it.

Two acceptance checks before any real money:

```bash
python -m racing.test_live          # 6 gates on the live path — must be 6/6
python -m racing.racingapi --probe  # is `rpr` still populated on your plan?
```

`--probe` is the one that decides whether the system is bettable at all. If RPR
coverage on the results feed is low, the archive is being extended with rows that
cannot support the feature carrying 100% of the measured edge, and the strategy
must be re-validated against whatever rating the feed does supply before betting.

What the live card prints, and why each line is there:

| line | the failure it is watching for |
|---|---|
| identity breakdown | a feed spelling sires differently turns proven horses into first-timers — silently |
| `rp_rpr_minus_or: n% of runners have it` | the edge feature missing from the card |
| `race_prize: n.nx the archive median` | a feed advertising the winner's share rather than the fund, shifting a feature with no error |
| `prices: n% priced` / fully-priced races | a partial book cannot be normalised, so those races are dropped rather than guessed |
| `priced from: …` | the strategy was validated at BSP; a pre-off back price is worse-informed |

`--require-edge-feature` is **on by default**: a runner with no rating history is
not bet at all. Note this is a deliberate divergence from the backtest, which
selected its 911 bets without that filter — such a runner is almost always a
debutant at a price the `<= 4.0` cap rejects anyway, but that has not been
measured. `--no-require-edge-feature` reproduces the backtested selection.

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
4. **The live path is built but has never made a real call.** No API key was
   available when it was written, so `racing/live.py` is validated end to end
   against synthetic form through the real parsers and the real production
   booster (`racing/test_live.py`, 6/6), not against a live feed. Treat the first
   `--probe` and the first `backfill` run as the acceptance tests — see
   [Going live](#6a-going-live). The archive still ends 2026-06-03 and must be
   backfilled before a live card means anything.
5. **Replaying a past date is in-sample.** The production model is trained on all
   data to 2026-06-03, so a card for an earlier date is not an out-of-sample
   result. The honest numbers are in §4c, from walk-forward.
6. **The validated numbers are at BSP, but a live bet is placed pre-off.**
   Betting earlier means a worse-informed price: the morning price removes 11.2%
   of no-information log loss against BSP's 13.4%. Prefer Betfair's "Take SP"
   where the market offers it; the live card prints which basis it used.
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

`test_live.py` must pass before any live card is acted on:

- **As-of, again, on the live path** — today's features must be bit-identical when
  a later month of racing is appended, and a card runner's own result must not
  reach its own features.
- **Identity** — the live-only failure: a feed spelling a sire differently makes
  every runner a first-timer and the model answers confidently from nothing.
- **Book normalisation** — a raw pre-off `1/price` book sums to ~1.02–1.30;
  feeding that to the model as its prior manufactures an edge on every runner.

This scan exists because a hand-written check missed `prize` — which is the prize
money the runner *won*, a post-race outcome. The biggest prize in a race belongs
to the winner 99.8% of the time, and including it made the model look **96%
better than the market**. A curated checklist will not catch the next one; the
automated scan over all features is the real defence.
