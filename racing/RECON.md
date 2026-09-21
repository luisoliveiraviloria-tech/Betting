# Horse-racing engine — Phase 0 reconnaissance (2026-09-21)

Nothing has been built yet. This records what was checked, what was verified
live, what was only read from secondary sources, and what is still unknown.

Confidence tags: **[live]** = probed by us today · **[official]** = Betfair's own
docs · **[secondary]** = third-party page, not confirmed · **[unknown]**.

## 1. Skill discovery

`ListSkills` and `SearchSkills` for horse racing / Betfair / betting / sports
data / ML returned **nothing**. No domain skills exist; everything is built
from scratch. The only enabled skills are generic (docx, xlsx, pdf, pptx…).

## 2. Betfair access (the constraint that shapes everything)

| Item | Finding |
|---|---|
| Delayed App Key | Free, but data is delayed 1–180 s **[official]** — useless for "BET AT X OR BETTER" execution. |
| Live App Key | **£499 one-off, non-refundable**, debited from the account **[official]**. Some pages still say £299; the official support page says £499. Read-only use of the live key is not permitted **[official]**. |
| Sportsbook | **No API.** Exchange API cannot read Sportsbook prices or place Sportsbook bets; read-only Sportsbook feed is for licensed affiliates only **[official/secondary]**. Sportsbook prices can only be checked and placed by hand. Scraping Betfair endpoints breaches ToS. |
| Commission | UK Market Base Rate 5%, reduced by points-based discount; "Expert Fee" (ex-Premium Charge) only above £25k rolling 52-week profit **[secondary — Betfair's own charges page redirected/403'd, verify in account]**. |

Implication: the spec's "use whichever of Exchange/Sportsbook is better" can
only be automated for the Exchange. A £499 live key is 25× the £20 bankroll.

## 3. Betfair historical data

### 3a. Free daily BSP CSVs — verified live, no login **[live]**
`https://promo.betfair.com/betfairsp/prices/dwbfprices{uk|ire}{win|place}DDMMYYYY.csv`

- Coverage confirmed for 2016, 2018, 2020, 2022, 2024, 2026 (both regions, win + place). Page states 2008 onward.
- Columns: `event_id, menu_hint (course+date), event_name (distance+race type, e.g. "2m Hcap Hrd"), event_dt, selection_id, selection_name, win_lose, bsp, ppwap, morningwap, ppmax, ppmin, ipmax, ipmin, morningtradedvol, pptradedvol, iptradedvol`.
- Gives us: result, BSP, pre-play weighted-average price, pre-play min/max, morning price, and traded volumes. That is enough for the market baseline, CLV and liquidity proxy.
- Does **not** give: going, official rating, age, weight, draw, jockey, trainer, form, class, field size (derivable), sectionals. It is a market/results file, not a form file.
- Quirks seen: header case differs between files (`EVENT_ID` vs `event_id`); `bsp` null for non-runners (996 of 4,393 rows in our sample); `bsp`=1000 when no SP backers; `event_name` sometimes blank; dead-heats give 2 winners in a race.
- **Rate limit: HTTP 429 after ~64 rapid requests (8 threads).** Bulk pulls must be throttled (≈1 req/s) and resumable.
- **Licence:** Betfair's page says the data is © / database right and "may not be used for any purpose without a licence". Personal research use is a grey area — your call, but it is not clearly permitted.
- Sanity check on a 29-day sample (361 races, 4,393 runners): the BSP book sums to **1.003 ± 0.016** per race. i.e. **no overround** — the only cost of backing at BSP is commission. Price-band tables from this sample are far too small to interpret (e.g. 14 runners in 1.5–1.7); ignore them.

### 3b. Betfair Historic Data service (order-book files) **[official/secondary]**
- Basic = free, 1-minute last-traded price, **no volume**. Advanced (£, 1-second, top-3 ladder + volume) and Pro (£, 50 ms full ladder) are paid; **prices are not public** — only visible after login **[unknown]**.
- Data from April 2015 **[official]**; horse racing GB/IE WIN/PLACE markets expected but not confirmed for the Basic plan.
- Could not probe: `BETFAIR_USERNAME/PASSWORD/APP_KEY` are **not set in this shell**. The existing `quant/betfair_historic.py` client was written for football (hard-coded `Soccer`/`MATCH_ODDS`), and `DownloadFile` has never been exercised live.

## 4. Racing form / feature data

| Source | Type | Depth | Cost | Verdict |
|---|---|---|---|---|
| Betfair BSP CSVs | Market + result | 2008+ | Free (licence caveat) | **Use** — spine of the DB |
| Kaggle `deltaromeo/horse-racing-results-ukireland` | Results/form, scraped | 1988–2026 claimed; SQLite/CSV last stated update 19 May 2025 | Free | Candidate for features. **Field list, licence and provenance unverified (page did not render)**; likely scraped from Racing Post → licence grey, and stale for live use. |
| The Racing API | JSON API, racecards/results/SP/form/sectionals/profiles, 20+ bookmaker odds | Historical results on the Standard plan: last 12 months only; Pro for more | Paid tiers, **prices not verified** | Candidate for the daily racecard feed. Need pricing and depth before committing. |
| Timeform sectionals | Sectionals | UK/IRE AW + selected turf; provider changed May 2026 | Paid | Defer (Phase 11). |
| Racing Post / Timeform / PA / Sporting Life feeds | Ratings, RPR, TS | Deep | Commercial licence; no public API | Out of reach at £20. |
| BHA | Fixtures, aggregate stats | — | Free | Aggregates only, not runner-level. |
| Scrapers (Apify, GitHub `rpscrapesa`) | Racing Post scraping | — | Cheap | ToS/database-right risk; fragile. Not recommended as a foundation. |

Bottom line: **there is no free, licensed, runner-level form dataset with
official ratings, going, draw and sectionals.** Any feature-rich model needs
either paid data or scraping with legal risk.

## 5. What already exists in the repo (reusable)

`quant/market.py` (multiplicative + Shin de-vig), `quant/backtest.py`
(walk-forward, Brier/log-loss, edge-bucket ROI, 5% commission), `betfair_auth.py`
(interactive + cert login), `betfair_historic.py` (needs generalising from
football). Football result on record: goals-only models lost to the closing
market and the nested blend weight collapsed to ~0.

## 6. Honest feasibility read

- The football work already showed that a model built on public results does not beat a liquid closing market. Betfair UK/IRE racing WIN markets are similarly liquid, and the BSP book has zero overround. Expect the same outcome unless the racing data carries information the price does not.
- To profit at 1.4–1.9 you need a true edge above 5% commission **plus** estimation error. The market-prior blend (Model 6) is the right design precisely because it will say "ignore the model" when there is no edge — that is an acceptable result.
- £20 → £1,000 is 50×. At realistic edges of a few percent on short-priced bets that is hundreds of bets with a real ruin probability; it is a stretch target, not a base case. Phase 5/6 will quantify this rather than assume it.
- The Live key fee (£499) exceeds the bankroll, so live automated execution is uneconomic at £20. The realistic mode is **shadow betting + manual placement**, which the spec's Phase 14 already anticipates.

## 7. Recommended Phase 1 (zero spend)

1. Throttled, resumable BSP CSV downloader 2016→today into SQLite (`racing.db`), schema-normalised.
2. Market-only baseline: de-vigged BSP/PPWAP probabilities, calibration by price band across the full history (this is the real version of the sample above).
3. **Kill test before buying anything:** does any feature-based model beat the BSP-implied probability out of sample on log loss? If not, stop before spending on data.
4. Only if step 3 shows promise: buy form data (Racing API tier or Historic Advanced) — needs your decision on budget.

## 8. Open questions for the user

1. Comfortable using Betfair's free BSP CSVs given the © notice?
2. Data budget before the kill test passes (assumed £0)?
3. Are you willing to pay £499 for a live key, or should the design assume manual placement?
4. Can you run `python -m quant.betfair_historic --options` / `--my-data` from your own machine (with credentials set) to show what Basic/your plan exposes for Horse Racing?

---

## 9. Update (same day, after user input and live checks)

**Decisions from the user:** BSP CSV use accepted. Placement is **manual** (no £499 live key). Data budget: will pay if a model proves profitable; £20 → £1,000 is a fun challenge, real bankroll may grow substantially if profitable long-term.

**Betfair Historic Data — verified live (user ran it):**
- `GetMyData` works. Plan held: **Horse Racing Basic, months 2024-01 → 2026-09**; Soccer Basic 2020-01 → 2026-09. Basic = 1-minute last-traded price, no volume.
- Root cause of the earlier 401/405s: the service authenticates with an `ssoid` header, not `X-Authentication`. Fixed in `betfair_auth.py`. `get_my_data` tries GET then POST; which one succeeded was not captured — **pin it once known**.
- `--options --sport "Horse Racing"`: WIN 837k, PLACE 572k, OTHER_PLACE 595k, MATCH_BET 247k market files; GB 805k, IE 206k files (counts are across the whole catalogue, not just our plan).
- Further Basic months (2015–2023) can presumably be added for free on the Historic Data site (purchase-per-month model) — not yet tried.

**The Racing API — tiers checked against its OpenAPI docs (the earlier "£27.99 Basic" assumption is WRONG for training data):**

| Plan | £/mo | Bulk historical data? |
|---|---|---|
| Free | 0 | Today only. 1 req/s. |
| Basic | 27.99 | Today's racecards/results; per-horse history for horses on today's card. **No bulk historical results.** |
| Standard | 59.99 | `/v1/results`: all races, **last 12 months only**. Search + analysis endpoints. |
| Pro | 99.99 | Per-horse/jockey/trainer results back to 2005 (365-day window per call); historical racecards + odds only from 2023-01-23. **Full 20-yr `/v1/results` needs a one-time £499 add-on.** |

- Racing Post RPR / Topspeed were **removed June 2026**; replaced by in-house `performance_rating` / `speed_rating` (no validated track record).
- 5 req/s paid, 1 req/s free; Cloudflare cool-offs at >100 req/10 s. Prohibited for betting operators (n/a personally). Data licence/storage terms not read — check ToS before committing.
- Cheapest route to a usable training set: **Standard £59.99 for one or two months** and export the 12 months of all-race results (≈12k UK+IRE races) — or Pro + £499 for 2005+. A 12-month window is thin for horse-history features because each runner's earlier form falls outside it.

**Build state:** `racing/bsp_data.py` (throttled, resumable BSP → SQLite, `racing/data/racing.db`). Verified: (a) `requests` fails Betfair's cert chain and Python 3.13+ strict X.509 rejects its CA ("Basic Constraints … not critical") → uses urllib with **only** `VERIFY_X509_STRICT` cleared, chain/hostname checks on; (b) **a 302 to the same URL is transient** (file returned 200 seconds later) — treated as retryable, not "no racing"; `--audit` cross-checks win vs place for holes.

---

## 10. Kaggle `deltaromeo` dataset — inspected (3.7 GB extracted to `racing/data/kaggle/`, gitignored)

**Contents:** `form_2015-present/raceform.csv` (685 MB, **1.85 M runner rows, 188,782 races, 2015-01-01 → 2026-05-27**), `mini-update.csv` (2026-05-28 → 06-03), `archive_2005-2014` and `archive_1988-2004` (CSV+SQLite), `BHA_ratings/` (weekly BHA rating snapshots — **not dated per race, so leak-prone; do not use without as-of dating**), `betfair/` (2026-only BSP mapping), `daily_racecards/` (Apr–Jun 2026 pre-race cards with ~100 columns incl. trainer/jockey 14-day stats), `recent_form_html/` (skip).

**raceform.csv columns:** date, course, race_id, off, race_name, type, class, pattern, rating_band, age_band, sex_rest, dist, going, ran, num, pos, draw, ovr_btn, btn, horse, age, sex, wgt, hg, time, sp, jockey, trainer, prize, or, rpr, ts, sire, dam, damsire, owner, comment.

**Quality (UK+IRE):** ~12.6–14.1k races/yr (2020 = 8.5k, COVID). No duplicate (race, horse) rows; exactly one winner in 99.7% of races (dead-heats otherwise); none with zero winners. Median field 9. Missing/placeholder: `or` 27% (mostly non-handicaps), `draw` 39% (jumps have none), `class` 27%, `rating_band` 45%, `time` 6%, `sp` 0.2%. `dist`, `going`, `jockey`, `trainer`, `age`, `wgt`, `sex`, pedigree: complete. Course names mix GB and foreign (`(IRE)`, `(FR)`…) and GB all-weather tracks also carry brackets (`Kempton (AW)`) — **a naive "has brackets = foreign" filter silently drops all UK all-weather racing** (I made that mistake once; the region filter must be an explicit foreign-tag list).

**Join to BSP:** on Oct-2025, 96.8% of BSP runners match Kaggle on date + normalised horse name; winner flag agrees on 99.98% of matches. Remaining gap = name variants + same-horse-twice-a-day; use race time in the key. So `racing.db` (BSP prices) + raceform (features) is a workable spine.

**Leakage map (critical):** `pos, btn, ovr_btn, time, comment` and this race's own `rpr`/`ts` are **post-race** — usable only as *previous-run* features, shifted strictly before the race date. `or, wgt, draw, age, going, dist, class, jockey, trainer, hg` are pre-race. `sp` is a starting price (post-race by definition, but the market benchmark).

**RISK — RPR/Topspeed are decaying at source.** Missing-rate for UK+IRE `rpr`: ~5% through Sep-2025 → 8% Oct → 13% Nov → 19–27% Jan–May 2026; `ts`: ~13% → 23–31%. Consistent with the Racing API removing RPR/TS in June 2026. **A model that leans on RPR/TS would train on a rich history and then be starved at live inference.** Policy: benchmark every model with and without RPR/TS; only keep them if (a) they add out-of-sample value and (b) a live source for them is confirmed (none is, today).

**Licence/provenance:** the archive has no licence file (only the author's ReadMe). Fields like `rpr`, `ts`, `comment` are Racing Post editorial/proprietary content, so this is a scrape of third-party database-right material. Treat as **private personal research only: no redistribution, no publishing, no resale.** The user should be aware this is a grey area, as with the BSP files.

**Staleness:** ends 2026-06-03; today is 2026-09-21 — fine for the kill test, not for live use. Live daily cards need another source (Racing API Basic £27.99/mo or Free tier are candidates; not yet tested).

**Revised data plan:** Kaggle = historical features (free) · BSP CSVs = prices/CLV (free) · Racing API Basic (£27.99) = *possible* live daily racecards only, and only after the kill test passes. The 12-month Standard-tier purchase is no longer needed for history.

---

## 11. Feature pipeline — built and verified (2026-09-21)

Files: `racing/clean.py` (Kaggle CSV → typed UK+IRE `runs`), `racing/features.py` (as-of features), `racing/check_features.py` (gates). Output: `racing/data/form.db` (`runs`, `features`; separate from `racing.db` because the BSP downloader holds write locks there). Rebuild: `python -m racing.clean && python -m racing.features` (~2 min + ~8 min). Gates: `python -m racing.check_features`.

**Result:** 1,348,488 UK+IRE runner rows, 143,069 races, 2015-01-01 → 2026-06-03 (GB 990k / IRE 358k; Turf 1.03M / AW 313k). 74 model inputs (67 without the `rp_` RPR/TS group). Every parser leaves <0.01% unparsed.

**Bugs found and fixed by the gates (do not reintroduce):**
1. `race_id` is **not unique** in the source: 135 ids (2,703 rows) are reused for races at different courses/dates (e.g. 616900 = Musselburgh *and* Thurles). Caught by the truncation test (field size / race-relative features changed with future rows removed). All grouping now uses `race_key = race_id|start-time|course`.
2. Region must come from explicit GB/IRE course whitelists (bare untagged names include foreign courses; GB AW courses carry brackets).
3. Corrupted `½` (`\ufffd`) in distances, mixed 12h/24h race times, `EvensF`/`Evs` SP formats — all handled and unit-tested.

**Verification:**
- Truncation leakage test: 74 features × 145,767 rows bit-identical with vs without any data after a cutoff (2018–19 window). It proves no *future-row* leakage. It cannot detect a feature that includes the runner's *own* result — that is covered by design (cumsum-minus-self, shift(1)), the target guard, and the strike sanity below.
- Target guard: no win/place/pos/time/SP columns in the model-input whitelist (whitelist-based, so new raw columns cannot slip in).
- Strike sanity (115,922 races, 2017+): random pick 12.0% · **SP favourite 34.0%** · best single pre-race features: RPR-last 24.7%, avg-3 RPR 23.2%, last-3 finishing % 22.1%, horse place a/e 20.5%, trainer a/e 20.1%, combo 20.0%, jockey 19.4%, OR 18.6%. All well below the market favourite, no leak alarm.

**Reading:** each single feature is informative (≈ +6–13 points over random) but the market's own favourite is ~10 points better than the best feature. That is the expected picture for an efficient market and means the *kill test* (does a combined model add anything beyond the market price?) is the real question — not whether features "work".

**Not built yet:** BSP↔form join table, baseline calibration, the model/kill test, sectionals/pace, draw bias by course, going-change features, non-runner recalculation.

---

## 12. BSP join + market calibration baseline (2026-09-21)

New: `racing/market.py` (BSP -> form join, writes `market` table), `racing/calibrate.py` (the baseline). Run: `python -m racing.market && python -m racing.calibrate`.

### Join quality
796,321 runners matched, **99.5% of all form runners inside the BSP date range**; 1,151 ambiguous matches dropped rather than guessed; **course_mismatch = 0**; winner flag agrees 99.97% (residual ~235 rows = dead-heats / amended results).

Two things had to be got right:
1. **The BSP file published on date D contains races run on D-1** (lag exactly 1 for every row checked). `event_dt` is the race time; `file_date` is a publication date and must never be used as a race date.
2. **Betfair's `menu_hint` course naming is not stable.** Pre-2023 it is `"GB / Kemp 3rd Jun"` (country prefix + abbreviations: Kemp, Newc, Wolv, Ling, Dund, Sthl, ChelmC, Aint, Epsm, Extr, MrktR, GowP); from 2024 it is plain `"Kempton 3rd Jun"`. Naive parsing produced 401,083 false "mismatches" and silently disabled the only check that confirms we matched the *right* horse. Now resolved by prefix-strip -> alias map -> unique prefix match, giving 0 mismatches.

### Result: the market is, for practical purposes, perfectly calibrated
708,326 runners in 76,733 full-book races, 2019-07-29 -> 2026-06-03. Book (sum of 1/BSP) = **1.0018** — an 0.18% overround, versus **1.168** for the bookmaker SP in the same period.

| BSP band | n | implied | actual | diff | ROI | fair bar | vs fair |
|---|---|---|---|---|---|---|---|
| 1.01-1.3 | 829 | 0.828 | 0.834 | +0.5pp | -0.5% | -0.8% | +0.3% |
| 1.3-1.5 | 1,457 | 0.710 | 0.719 | +0.9pp | -0.3% | -1.4% | +1.1% |
| 1.5-1.7 | 2,108 | 0.624 | 0.622 | -0.2pp | -2.1% | -1.9% | -0.2% |
| 1.7-1.9 | 2,923 | 0.555 | 0.548 | -0.7pp | -3.5% | -2.3% | -1.2% |
| 1.9-2.2 | 5,952 | 0.489 | 0.471 | **-1.8pp** | -6.1% | -2.6% | -3.5% |
| 2.2-3.5 | 37,836 | — | — | -0.3..-0.5pp | ~-4.5% | ~-3.2% | ~-1.2% |
| 3.5-25 | 434,709 | — | — | +0.1pp | -3.5..-3.9% | -3.8..-4.7% | +0.2..+1.1% |
| 25+ | 222,512 | 0.019 | 0.019 | +0.0pp | -7.5% | -4.9% | -2.6% |

11 of 12 bands have the actual win rate inside the 95% CI of the implied probability. Only 1.9-2.2 is outside (-1.8pp).

**The "fair bar" matters and is easy to get wrong.** A perfectly priced market still loses the backer `-commission x (1 - strike)`, because commission is charged only on winnings. At an 10.8% overall strike that is **-4.46%**, not 0%. Observed overall ROI is **-4.91%**, so the true excess cost of the market is only **-0.45%** (overround + miscalibration). *(An earlier version of calibrate.py had this formula inverted — `-c x p` instead of `-c x (1-p)` — which understated the bar by 4pp. Fixed.)*

**No segment is profitable**: handicap -3.9%, non-handicap -7.6%, flat -5.0%, jumps -4.9%, AW -6.3%, turf -4.4%, GB -4.7%, IRE -5.6%, every field-size bucket -4.6..-5.2%, favourites-only -3.9% (33.3% strike). There is no bias to exploit at the segment level — only, possibly, at the individual-runner level.

### Information timing (log loss, 691,403 common rows)
| estimator | log loss | vs no-info |
|---|---|---|
| no-info (1/field) | 0.3386 | 0% |
| morning WAP | 0.3005 | 11.2% |
| pre-play WAP | 0.2951 | 12.9% |
| **BSP** | **0.2931** | **13.4%** |

The price improves materially through the day: ~16% of the market's total edge over no-information arrives after the morning. **Betting early means betting against a worse-informed price — but also means our own model must be that much better to justify the earlier, worse price.** Book-normalising changes nothing (book is already 1.0018).

### Universe size — a hard constraint on the £20 plan
At BSP, UK+IRE has only **~3.8 runners/day priced under 1.9** (9,413 in 6.8 years, 64.2% strike) and **~6.6/day under 2.2**. The spec's preferred 1.4-1.9 zone is ~3.3/day *before* any qualification filter. Any strategy restricted to short prices is therefore capped at a few opportunities a day, most of which will not qualify.

### What this means for the kill test
The market baseline to beat is **log loss 0.2931**. The bands the spec cares about (1.3-1.9) are calibrated to within 1pp, so there is no crude mispricing to harvest; a model must add genuinely new information about *specific* runners. The 1.9-2.2 band's -1.8pp is the only visible anomaly and is worth a closer look (is it a real effect or a multiple-comparisons artifact across 12 bands?).

**Next:** the kill test — fit models on the leak-free features and ask whether combining them with the market price beats the market price alone, out of sample, walk-forward.

---

## 13. PRODUCT DELIVERED (2026-09-21) — model, EV engine, validated strategy, daily card

New modules: `model.py` (kill test), `ev.py` (EV/Kelly/gates), `backtest.py` (validation + ruin), `explain.py` (SHAP reasons), `train_final.py`, `daily.py`. Full write-up in `racing/README.md`.

### The leak that nearly shipped
First kill test returned log loss **0.011 vs the market's 0.291 — a 96% "improvement"**, which is impossible. Cause: **`prize` in the Kaggle data is the prize money the RUNNER WON**, not the race's prize fund. The biggest prize in a race belongs to the winner **99.81%** of the time, so it directly encodes the result. I had whitelisted it assuming it was the advertised fund.

The truncation test could not catch this (it only detects *future-row* influence, not a column that encodes the runner's own result), and my strike-sanity check tested a **hand-picked list** that did not include `prize`. Fixes: per-runner `prize` removed (race-level SUM kept — constant within a race, so it cannot rank runners); **`leak_scan()` added, which tests EVERY feature automatically** and fails if any single pre-race feature beats the SP favourite (~34%). Post-fix, top single feature strikes 0.221. **Never rely on a curated list again.**

### Second correction: the blend was worse than the market
With the features simply concatenated, the blend LOST to the market every year (0.29332 vs 0.28714) — the model was overfitting form and degrading the price signal. Fixed by making the market an explicit **prior**: LightGBM trained with the market log-odds as `init_score`, so trees are *corrections*, with early stopping deciding how much correction is earned. Zero trees reproduces the market exactly. Typically 20–70 trees survive. After this the blend beat the market in every properly-trained year.

Also: the 2017 fold had too little prior data to form an early-stopping split, trained all 600 rounds unvalidated and overfit (the only losing year). Now capped at 40 rounds when no validation split is possible.

### Result (out of sample, 2024–26, thresholds fixed on 2017–23)
**911 bets · 1.03/day · 42.3% strike · mean price 3.19 · ROI +24.28% (95% CI +14.5%…+34.0%, t=4.88)**; positive every year (2024 +22.2%, 2025 +23.2%, 2026 +43.4%). All 23 threshold configurations tested were positive (t 3.1–5.8).

Decisive diagnostic: on bet runners the market says 0.329, model says 0.372, **actual 0.423** — the market under-prices them, and the model is *under*-confident. Overfitting gives the opposite sign, so this is strong evidence the edge is real.

### Bankroll
Ruin is a stake-size problem. £20 at the £2 minimum = 10% per bet: survives ~90% but through an **82% drawdown**. £100 at 2%: ~0% ruin, 21% drawdown, £100→£542 flat or £1,543 at 1/4 Kelly over the validation period. **£100+ recommended.**

### Operational notes
- `racing.db` is **WAL** — a plain `read_sql` in rollback-journal mode killed a 5,219-file download mid-run. Always use `racing.bsp_data.connect_db()`.
- BSP download **complete**: 7,832 files, 2016-01-01 → 2026-09-19. Join now covers 1,226,440 runners (99.5%, 0 course mismatches).
- Betfair's `menu_hint` course naming is unstable pre-2023 ("GB / Kemp"); `market.resolve_course` handles it. Naive parsing produced 401k false mismatches and silently disabled the only check that we matched the right horse.

### Next
1. **Live racecard feed** — the only blocker to daily use (form archive ends 2026-06-03). The Racing API Basic £27.99/mo; `daily.py --prices` already takes a `horse,price` CSV.
2. Watch RPR decay: `rp_rpr_minus_or` is the strongest feature and its source coverage is falling (~95% → ~75-80% since Oct-2025). Re-benchmark without `rp_` features.
3. Web app for the daily card.
