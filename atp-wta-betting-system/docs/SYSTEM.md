# ATP / WTA Value Betting System — Full Design

> Objective: maximize long-term ROI through positive expected value (EV+) bets on
> ATP and WTA match winners, with disciplined bankroll management and continuous
> recalibration. Win rate is explicitly **not** the target — a 45% hit rate at
> average odds of 2.40 is far more profitable than a 65% hit rate at 1.45.

---

## 1. Key variables that drive match outcomes

Ordered by predictive power, based on published tennis-forecasting research
(surface-specific Elo consistently outperforms ATP/WTA rankings and H2H; see
Kovalchik 2016, "Searching for the GOAT of tennis win prediction") and on what
is observable *before* the market closes.

| # | Variable | What to measure | Why it matters |
|---|----------|-----------------|----------------|
| 1 | **Surface-specific rating** | Surface-weighted Elo (blend: 70% surface Elo + 30% overall Elo) | Single strongest predictor. Rankings lag reality by months; Elo updates per match and separates clay/grass/hard specialists |
| 2 | **Recent form** | Last 8–10 matches, weighted by recency and opponent quality; wins/losses alone are misleading — use dominance ratio (return pts won % / opponent return pts won %) | Captures trajectory Elo hasn't absorbed yet (new coach, post-injury return, confidence runs) |
| 3 | **Serve/return matchup** | Hold %, break %, 1st-serve pts won, 2nd-serve pts won, ace/df rates — on this surface, last 52 weeks. Compare A's serve vs B's return and vice versa | Tennis is a serve/return duel; a big server vs an elite returner plays very differently than the ratings gap suggests |
| 4 | **Fatigue & schedule** | Sets/time-on-court last 7 & 14 days, 3-setters (Bo3) or 4/5-setters (Bo5) in current event, back-to-back weeks, late-night finishes | Well-documented next-match performance decay after long matches, especially Bo5 and hot conditions |
| 5 | **Injury / physical state** | Retirements/walkovers last 3 months, medical timeouts in current event, taping visible, press-conference remarks | Binary veto power — no model number survives a bad hamstring |
| 6 | **Travel & environment** | Time zones crossed since last event, altitude (e.g., Bogotá, Madrid ball-flight), indoor/outdoor, heat/humidity, wind, ball brand change | Conditions shift serve dominance and rally length; altitude and slow-to-fast transitions punish grinders |
| 7 | **Motivation & context** | Tournament stage/points defended, ranking cutoffs (Slam seeding, Finals race), home crowd, dead-week 250s after a Slam, Davis/BJK Cup interruptions | Top players visibly manage effort at small events; qualifiers fighting for main-draw money often overperform prices |
| 8 | **Head-to-head** | Surface-specific H2H, recency-weighted, style-of-matchup notes | Deliberately **low** weight: small samples, often stale, and already priced in. Useful mainly as a style-clash flag (e.g., lefty serve vs weak backhand return) |
| 9 | **Weather (day-of)** | Forecast wind/heat/rain interruptions for outdoor events | Wind neutralizes big first serves and flat hitters; heat amplifies fatigue edges |

**Not used:** official ATP/WTA ranking gap (redundant vs Elo, slower), career
titles, media narratives, "revenge" storylines.

---

## 2. Weighted scoring model → confidence score (0–100)

Each factor is scored **from Player A's perspective** on a −10…+10 scale
(0 = neutral, +10 = maximal advantage to A), then combined with these weights:

| Factor | Weight |
|---|---|
| Surface-specific rating gap (Elo) | **30%** |
| Recent form (dominance-ratio trend) | **15%** |
| Serve/return matchup | **15%** |
| Fatigue & schedule | **10%** |
| Injury / physical state | **10%** |
| Conditions (travel, altitude, weather, balls) | **8%** |
| Motivation & tournament context | **7%** |
| Head-to-head (surface, recency-weighted) | **5%** |

`raw = Σ (weight × factor_score)` → raw ∈ [−10, +10] →
**confidence = 50 + 5 × raw**, clipped to [0, 100].

Confidence 50 = coin flip; 65 = solid edge to A; 80+ = heavy favorite.

### From confidence to probability (calibration layer)

The score is mapped to a win probability with a logistic curve fitted on
historical data (start with the default below, re-fit quarterly — §8):

```
P(A wins) = 1 / (1 + exp(−k × (confidence − 50)))     k ≈ 0.055 (ATP), 0.050 (WTA)
```

The smaller WTA k reflects higher outcome variance on the women's tour
(more service breaks, more upsets at equivalent rating gaps).

### Elo scoring rule of thumb (factor 1)

Convert blended surface-Elo difference `D = Elo_A − Elo_B` to the −10…+10 scale:
`score = clip(D / 40, −10, +10)` (i.e., 400 Elo points = maximal +10).

### Hard vetoes (override any score — automatic NO BET)

- Either player retired or withdrew within the last 21 days, or took a medical
  timeout in their previous match, with no confirming fitness evidence since.
- First match back from a layoff ≥ 6 weeks (for either player) — unpriceable.
- Qualifier/wildcard with < 10 tour-level matches of data on this surface.
- Confirmed off-court disruption (visa, illness in camp, coaching split this week).

---

## 3. Entry criteria — when a bet is allowed

All conditions must hold. **If any fails, log the match as a SKIP and move on.**

1. **Minimum edge:** `model_prob − vig_free_book_prob ≥ 5%` (ATP) / **6%** (WTA).
   Remove the vig first: for a two-way market with implied probs `p1, p2`
   (from 1/odds), vig-free `p1' = p1 / (p1 + p2)`.
2. **Odds window: 1.50 – 3.50.** Below 1.50 the payoff can't survive your
   error bar plus limits/vig; above 3.50 you're modeling tail events where the
   model is least calibrated (extend to 4.00 only after 500+ logged bets prove
   calibration there).
3. **Confidence ≥ 62** when backing the model's pick as favorite, or
   **confidence ≤ 38** on the opponent when taking the underdog side.
4. **Data completeness:** ≥ 10 surface matches in the last 52 weeks for both
   players; all 8 factors scoreable (no "unknown" entries).
5. **No hard veto** (§2) triggered.
6. **Volume cap:** max 3 bets/day, max 15% of bankroll exposed simultaneously.
7. **Timing:** bet as early as your information is complete — beating the
   closing line is the point. Never add or increase a stake after lineups of
   emotions ("chasing" after a loss is a logged rule violation).

### Situations to always avoid

- **Retirement-risk spots**: any injury flag, extreme heat days for players with
  cramping history.
- **Exhibition-adjacent events**: United Cup dead rubbers, post-clinch team ties.
- **First rounds after Slams** for top-10 players at 250s (motivation trap).
- **In-play betting** unless it is a pre-planned, rule-based entry (e.g., "back A
  at better odds if they lose the first set but win ≥ 40% of return points").
  Never improvise live.
- **Accumulators/parlays** — they multiply the bookmaker margin.
- **Betting your own emotional attachments** (favorite players, revenge bets,
  "due for a win" reasoning).
- Markets with **stale/thin liquidity** (Challenger/ITF unless that's your
  dedicated, separately-tracked edge).

---

## 4. Bankroll management: fractional Kelly vs flat staking

### Kelly Criterion (recommended: 25% fractional, capped)

Full Kelly fraction for decimal odds `o` and model probability `p`:

```
f* = (p × o − 1) / (o − 1)          (only bet when f* > 0)
stake = bankroll × min(0.25 × f*, 2%)
```

Why **quarter** Kelly, not full:
- Full Kelly is only optimal if `p` is exactly right. Your `p` has estimation
  error, and full Kelly with an overestimated edge over-bets catastrophically
  (drawdowns of 60–80% are normal under full Kelly even *with* a real edge).
- Quarter Kelly retains ~75% of the long-run growth rate at roughly **half** the
  volatility — the standard professional compromise.
- The 2% absolute cap protects against model failure on any single input.

### Flat staking (benchmark / beginner mode)

Stake a fixed **1.0–1.5% of current bankroll** on every qualifying bet.

| | Flat 1–1.5% | Quarter Kelly (2% cap) |
|---|---|---|
| Growth rate | Lower — ignores edge size | Higher — bets more when edge is bigger |
| Drawdowns | Smaller, predictable | Moderate |
| Robustness to model error | High | Medium (fraction + cap mitigate) |
| Psychological difficulty | Easy | Medium (stake varies) |
| When to use | First 300 bets, or whenever calibration is unproven | After calibration verified (Brier/CLV checks, §8) |

**Rule:** run flat 1% for your first 300 logged bets. Switch to quarter Kelly
only after the calibration check in §8 passes. Recompute stakes from *current*
bankroll weekly (not per bet — avoids emotional anchoring to yesterday's swing).

---

## 5. ATP vs WTA filters

The tours have measurably different dynamics; one model with two parameter sets.

| Adjustment | ATP | WTA | Rationale |
|---|---|---|---|
| Minimum edge | 5% | **6%** | WTA outcomes are higher-variance (service breaks ~1.5× more frequent); demand more margin for error |
| Logistic k (calibration) | 0.055 | 0.050 | Same rating gap converts to less certainty in WTA |
| Serve/return factor | As weighted (15%) | Shift 5 pts from serve/return to recent form (→ serve 10%, form 20%) | Serve is less deterministic in WTA; hot/cold form streaks carry more signal |
| Best-of-5 fatigue boost (Slams) | Fatigue weight ×1.5 (take the extra 5 pts from H2H) | n/a (Bo3 everywhere) | Bo5 rewards fitness and makes big favorites *more* reliable — top ATP seeds cover Bo5 far more consistently |
| Odds ceiling | 3.50 | 3.50 (underdogs are genuinely live more often — but only with full data) | WTA dogs hit more, but data quality gates matter more |
| Newcomer gate | ≥10 surface matches | ≥12 surface matches | WTA rankings churn faster; thinner priors need more evidence |
| Scheduling note | Watch late-night 5-set turnarounds | Watch back-to-back-day finals weeks | Tour-specific fatigue patterns |

---

## 6. Daily workflow (≈ 45–60 minutes)

**Evening before (20 min)**
1. Pull tomorrow's schedule (ATP/WTA official, Flashscore).
2. Shortlist matches where both players pass the data-completeness gate — expect
   3–8 candidates from a full slate.
3. Fill the 8-factor sheet for each candidate (`model/value_model.py`).
4. Note opening odds (Pinnacle or sharpest available book) for each shortlisted match.

**Morning of (15 min)**
5. Re-check news: withdrawals, MTOs, weather, court assignments (day vs night
   session matters on some surfaces).
6. Re-run the model with updates; apply vetoes.
7. For every candidate with edge ≥ threshold: compute quarter-Kelly stake, place
   the bet, **immediately log the row** (odds taken, stake, model prob, edge).

**Evening after (10 min)**
8. Record closing odds for every bet (for CLV) and results.
9. Log SKIPs with one-line reasons — skip discipline is auditable too.
10. Weekly (Sunday, 30 min): update KPI dashboard (`model/kpi.py`), review any
    rule violations, adjust nothing else. Model weights change only at the
    quarterly review (§8) — never after a bad week.

---

## 7. Tracking spreadsheet & KPI dashboard

### Bet log — one row per bet (template: `tracker/bet_log_template.csv`)

| Column | Notes |
|---|---|
| `date`, `tour`, `tournament`, `round`, `surface` | Context |
| `player_a`, `player_b`, `selection` | Who you backed |
| `confidence`, `model_prob` | Model outputs at bet time |
| `odds_taken`, `book`, `open_odds`, `close_odds` | Market record — `close_odds` is mandatory |
| `vig_free_book_prob`, `edge` | Edge at bet time |
| `stake`, `stake_method` | Amount + `flat`/`kelly25` |
| `result`, `pnl` | `W`/`L`/`void`; profit/loss in units |
| `clv_pct` | `(close_implied_prob − taken_implied_prob) / taken_implied_prob` (positive = you beat the close) |
| `factor_1..factor_8` | The eight factor scores — this is what enables re-fitting weights later |
| `notes`, `rule_violation` | Honest annotations |

### KPI dashboard (computed by `model/kpi.py`)

| KPI | Formula | Healthy target |
|---|---|---|
| **ROI / Yield** | Σpnl / Σstake | +3–8% long-run is elite in tennis |
| **CLV %** | Mean of `clv_pct` | **> 0 over any 100-bet window** — the primary health metric |
| **Hit rate** | Wins / bets | *Diagnostic only*; expect 45–55% at avg odds ~2.0–2.2 |
| **Avg odds & avg edge** | Means | Confirms you're betting the intended profile |
| **Max drawdown** | Peak-to-trough of cumulative pnl (in units) | < 20 units at 1-unit avg stakes; quarter Kelly should keep it < 25% of bankroll |
| **Brier score** | Mean (model_prob − outcome)² | < 0.24 and beating the book's Brier |
| **Bets/week & skip ratio** | Volume discipline | Skips should outnumber bets ≥ 3:1 |

**Interpretation rules:** judge nothing on < 100 bets; judge the *model* on CLV
and Brier, judge *profit* only on 300+. Positive CLV + negative ROI = variance,
keep going. Negative CLV + positive ROI = luck, stop and re-fit.

---

## 8. Data sources & refinement loop

### Trusted data sources

| Source | Use | Cost |
|---|---|---|
| **Jeff Sackmann GitHub** (`tennis_atp`, `tennis_wta`, `tennis_slam_pointbypoint`) | Historical results, stats, rankings — the backbone for Elo + backtests (`data/fetch_data.py --source sackmann`) | Free |
| **Tennis Abstract** (tennisabstract.com) | Player pages, surface splits, Elo ratings, projected matches | Free |
| **Ultimate Tennis Statistics / ATP & WTA official sites** | Serve/return leaderboards, H2H, schedules | Free |
| **tennis-data.co.uk** | Historical results **with closing odds** for backtesting edge realism (`data/fetch_data.py` default source) | Free |
| **The Odds API / OddsPortal** | Live odds capture, opening vs closing lines (CLV) | Freemium |
| **Pinnacle** | Sharpest closing line = your CLV benchmark | Account |
| **Flashscore / Sofascore** | Live schedules, retirement news, point-by-point | Free |
| Beat-writer/insider feeds (X lists), tournament pressers | Injury & motivation intel | Free |

### Refinement over time

1. **Quarterly re-fit (never mid-quarter):** logistic regression of match outcome
   on your 8 logged factor scores over all logged bets + backtest data. Replace
   the hand-set weights with fitted coefficients once you have 300+ observations;
   shrink toward the priors above while samples are small.
2. **Calibration check:** bucket bets by model_prob decile; plot predicted vs
   actual win rate. Systematic overconfidence in a bucket → adjust `k` or ban
   that bucket.
3. **CLV autopsy:** every bet with negative CLV gets a one-line cause
   (late news? slow entry? market disagreed?). Persistent negative-CLV
   categories (e.g., WTA clay underdogs) get filtered out entirely.
4. **Backtest before deploying any rule change** on ≥ 3 years of Sackmann +
   tennis-data.co.uk odds. A change must improve backtested ROI *and* CLV proxy
   without reducing bet count below viability.
5. **Annual regime review:** surface speed changes, ball changes, new events —
   refresh Elo K-factors and surface blend ratios.

### Honest expectations

Sharp books hold ~2–3% margin on tennis; soft books more but limit winners fast.
A *realized* long-run yield of +3–8% on 500–1,000 bets/year is a strong
professional result. Anything promising more per-bet is overfit. The system's
job is to keep you solvent and rational long enough for a small real edge to
compound — that is what fractional Kelly, hard vetoes, CLV tracking, and the
skip log are for.
