# Bet ladder log

Goal: £10 stake, double up on each win, 7 wins in a row → ~£600+. Stake
resets to £10 after any loss. One log entry per leg attempted.

| # | Date | Fixture | Market | Selection | Bookmaker | Odds | Stake | Result | P&L | Ladder leg after |
|---|------|---------|--------|-----------|-----------|------|-------|--------|-----|-------------------|
| 1 | 2026-06-24 | Bosnia-Herzegovina vs Qatar | O/U 2.5 | Under 2.5 | Betfair Exchange | 2.50 | £10.00 | LOSS | -£10.00 | reset to leg 1, £10 |
| 1 | 2026-06-25 | Japan vs Sweden | 1X2 | Japan | Betfair Sportsbook | 1.91 | £10.00 | LOSS (1-1) | -£10.00 | reset to leg 1, £10 |

**Running total: -£20.00**

## Side note: free-bet fourfold (not part of the ladder stake)

Separate from the £10 ladder above — a fourfold built from the £2 free bet
+ 90p remaining balance (£2.90 total stake), using the corners/cards/goals
multi-market picks discussed alongside the Japan-Sweden analysis:

| Leg | Market | Selection | Odds |
|---|---|---|---|
| 1 | Corners | Curaçao vs Ivory Coast Over 8.5 | 1.75 |
| 2 | Cards | Ecuador vs Germany Over 2.5 | 1.90 |
| 3 | Goals O/U | Ecuador vs Germany Over 2.5 | ~1.65 |
| 4 | Cards | Curaçao vs Ivory Coast Over 2.5 | 1.96 |

Combined odds ~10.76. **Result: WON** — all four legs landed. Not counted
in the ladder running total above since it didn't use ladder stake money,
but worth noting as a real-world confirmation of the live-form-over-stale-model
reasoning used for the Ecuador-Germany goals leg in particular.

Independently verified (not just taken on the user's report) via
API-Football's `/fixtures/statistics` endpoint, post-match:
- Curaçao vs Ivory Coast: 4+6 = **10 corners** (>8.5 ✓), 2+1 = **3 yellow
  cards** (>2.5 ✓).
- Ecuador vs Germany: 3+1 = **4 yellow cards** (>2.5 ✓), final score 2-1 =
  **3 goals** (>2.5 ✓).

Two honest nuances from this fact-check, not corrections to the result but
worth recording: (1) Ecuador actually **won outright 2-1** — an upset, not
"Germany's hot scoring form continuing" as reasoned at the time; the Over
2.5 goals leg landed but not via the predicted mechanism. (2) The model
pick we *rejected* for this same matchday — Curaçao vs Ivory Coast Under
3.5 (69.1%, see rejection reasoning below) — final score was 0-2, only 2
total goals, so it would also have won this specific time. n=1, doesn't
overturn the dead-rubber/motivated-favourite logic used to reject it, but
flagged here per "fact check everything."

## System audit (2026-06-26, before today's pick)

Triggered by: "analyse our system first, fact check everything, debug
everything, flag improvements that can be made, and only then, we move on
to today's matches." Findings and fixes, in order found:

1. **Stale data snapshot (real, material bug).** `data/LAST_UPDATED.txt`
   showed the Elo/results snapshot was last refreshed 2026-06-24 15:14 UTC
   — before several matches that had since finished (Ecuador-Germany,
   Curaçao-Ivory Coast, Tunisia-Netherlands, Japan-Sweden on 6/25;
   Turkey-USA, Paraguay-Australia on 6/26). Quantified the impact with a
   before/after Elo diff after re-running `fetch_data.sh`:
   ```
   DE: 1954 -> 1916  (Δ-38)      TR: 1813 -> 1852  (Δ+39)
   JP: 1925 -> 1910  (Δ-15)      US: 1820 -> 1781  (Δ-39)
   SE: 1727 -> 1742  (Δ+15)      PY: 1816 -> 1815  (Δ-1)
   EC: 1864 -> 1902  (Δ+38)      AU: 1799 -> 1800  (Δ+1)
   CU: 1239 -> 1239  (Δ+0)      BA: 1596 -> 1622  (Δ+26)
   CI: 1728 -> 1743  (Δ+15)      QA: 1437 -> 1411  (Δ-26)
   ```
   Shifts up to ±39 points — comparable in size to the home-advantage
   adjustment used elsewhere in the model, i.e. material, not cosmetic.
   This did not corrupt the Japan-Sweden pick (made before the
   2026-06-25 fixtures existed, so the snapshot was contemporaneously
   accurate for that bet), but would have corrupted *today's* pick had it
   gone unnoticed. **Fix**: re-ran `./fetch_data.sh` before scanning
   today's fixtures. **Process gap this exposes**: nothing in the
   pipeline currently checks staleness automatically — it relies on
   someone remembering to look at `LAST_UPDATED.txt`. Worth adding an
   automatic freshness check (e.g. warn if `LAST_UPDATED.txt` is >24h old)
   so this isn't manual every time.

2. **football-data.org `status` query-param bug (real, confirmed code
   bug, now fixed).** The literal string `"TIMED"` passed as the
   `status` filter always returns 0 results, even though `"TIMED"` is the
   exact status string the API uses for upcoming matches in its response
   body. Only `"SCHEDULED"` works as a filter value, and correctly
   returns those same `TIMED`-status fixtures. Confirmed via direct
   testing:
   - `status='SCHEDULED'` (no matchday) → 44 results, all `TIMED`.
   - `status='TIMED'` → 0 results, always.
   - `matchday=3` (no status) → 24 results, mixing 12 `FINISHED` + 12
     `TIMED`.
   - `matchday=3, status='SCHEDULED'` → 12 results, all `TIMED` (correct).
   `live_report.py` was using `status="TIMED" if args.matchday else
   "SCHEDULED"` — so any `--matchday` run was silently pulling in already
   decided matches alongside upcoming ones, which would waste
   API-Football injury-lookup quota on finished games and show
   nonsensical model-vs-market output for them. **Fix**: both
   `live_report.py` and `value_finder.py` now always query
   `status="SCHEDULED"` and, on empty result, fall back to all fixtures
   filtered to exclude `FINISHED` (rather than an unfiltered fallback).

No other system issues found this round. Two ladder legs (both losses) is
too small a sample to draw any conclusion about the model or process
itself — consistent with the "variance vs. process failure" framing
already used for leg 1 above.

## Notes on leg 1 retry (2026-06-25, Japan vs Sweden)

- Model: Elo gap 198 (Japan 1925, Sweden 1727), neutral venue → Japan 61.1%,
  Draw 24.3%, Sweden 14.6% to win.
- Historical cross-check (5yr, neutral matches, Elo gap 150-250, n=210):
  favourite won 67.1%, draw 27.6%, underdog won only 5.2% — *higher* than
  the model's own favourite-win estimate, and the model's 14.6% underdog
  figure looks generous next to the 5.2% historical rate. Cross-check points
  the same direction as the model, not against it.
- Market: even the sharpest book (Pinnacle, devigged) implies only ~50.6%
  for Japan — a genuine ~10.5pp gap vs the model, ~16.5pp vs the historical
  base rate. Softer UK/EU books are looser still (~47-49% implied).
- Live-form check (the step that killed the day's two highest-edge O/U
  picks, see below): Japan are the form team in Group F — 2-2 with
  group-leaders Netherlands, 4-0 over Tunisia, +4 GD, 6 scored/2 conceded.
  Sweden are inconsistent — 5-1 over a hapless Tunisia, then 1-5 vs
  Netherlands, GD 0. No contradiction with the model here, unlike the
  rejected candidates.
- Stakes/incentive check: Japan are tied with Netherlands on points/GD for
  top spot and need only avoid defeat to be in a strong qualifying position;
  Sweden need a win to have a real shot at 2nd. Both sides have something to
  play for — no dead-rubber asymmetry distorting the price.
- Injury check (API-Football): one Japan squad absence, S. Machino
  (illness) — a backup forward, not a regular starter; not treated as
  material. Lineups not yet published (kickoff T-8h at time of check).
- Two higher-edge candidates from today's scan were investigated and
  rejected:
  - Ecuador vs Germany Under 2.5 (model 59.0% vs market ~40%, the day's
    largest nominal edge): passed a historical-frequency check (323 matches,
    Elo gap 60-120, neutral, 59.1% history vs 59.0% model — near-exact
    agreement) but failed live-form: Germany have scored 9 goals in 2 games
    (7-1, 2-1) against a model lambda_away of only 1.40, and sharp books
    (Pinnacle, Coolbet, MyBookie) have already moved their totals line up to
    3.0/3.5 instead of the standard 2.5 — i.e. the sharp market has already
    priced in exactly the live-form signal the Elo model missed. Backing
    Under here means betting against money that's already adjusted for the
    thing the model doesn't know.
  - Curaçao vs Ivory Coast Under 3.5 (model 69.1% vs market ~55%): this is
    the dead-rubber leg of Group E — Curaçao are already effectively out,
    Ivory Coast need a win/draw to secure 2nd. That motivation asymmetry
    (one side pushing for goal difference/security, the other with nothing
    to lose by sitting in or conceding) historically inflates total goals
    beyond what Elo alone predicts, and Curaçao have already shipped 7 to
    Germany this tournament. Sharp books again show it: Pinnacle's line sits
    at 3.25 (devigged ~50/50) versus the model's 2.80 expected total —
    another case of the sharp market already pricing the context the model
    can't see. Rejected for the same reason as Ecuador-Germany.
- **Result: LOSS.** Final score Japan 1-1 Sweden — the bet needed Japan to
  win outright, and a draw was always the third-most-likely outcome by the
  model's own numbers (24.3%), not a freak occurrence. The diligence (model,
  historical base rate, sharp-book devig, live form, incentives, injuries)
  was sound and all pointed the same direction; the draw probability itself
  was never small enough to treat as noise. Possible process refinement for
  future legs with a similar draw-probability profile (>20%): consider
  Win-or-Draw (double chance) as the safer side of the same edge when the
  raw win price doesn't compensate for a non-trivial draw chance, rather
  than defaulting to a straight win bet just because it has the bigger edge
  on paper.

## Notes / lessons from leg 1

- Thesis: two independent historical-frequency samples (5yr broad, WC-since-2014)
  showed P(Over 2.5) ~43-45% for this Elo-gap bucket vs market pricing Over at
  ~57-62% implied — a real, multiply-corroborated statistical gap at bet time.
- Live-check before kickoff found a genuine counter-signal the historical
  buckets couldn't see: both teams' *specific* prior group matches were
  high-scoring on the conceding side (Bosnia conceded 4 vs Switzerland, Qatar
  conceded 6 vs Canada) — a current-tournament defensive-frailty signal that
  current-Elo-as-proxy doesn't capture.
- The market itself kept shortening Under right up to kickoff (devig prob
  rose from ~37-40% to ~39-43% across matched bookmakers, Betfair Exchange
  drifted 2.65→2.20 on real volume) — i.e. informed money was *agreeing*
  with the Under thesis, not contradicting it. The bet still lost.
- What actually happened: 3 goals inside the first 13 minutes of the match
  (29', 34' own goal, 42') — total already past 2.5 by half-time. This is
  plain match variance (small-sample, single-match outcome), not evidence the
  process (model + market cross-check + live recheck) was wrong. A
  well-reasoned bet with a real edge still loses a meaningful fraction of the
  time — that's expected, not a signal to abandon the method after one leg.
- Process worked as designed: model fit (`calibrate.py`), historical
  cross-validation, live re-check, and odds-direction correction (see
  `CLAUDE.md`) all functioned correctly. The loss came from outcome variance,
  not a process failure — no process change indicated from this leg alone.
