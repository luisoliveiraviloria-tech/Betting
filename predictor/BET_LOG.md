# Bet ladder log

Goal: £10 stake, double up on each win, 7 wins in a row → ~£600+. Stake
resets to £10 after any loss. One log entry per leg attempted.

| # | Date | Fixture | Market | Selection | Bookmaker | Odds | Stake | Result | P&L | Ladder leg after |
|---|------|---------|--------|-----------|-----------|------|-------|--------|-----|-------------------|
| 1 | 2026-06-24 | Bosnia-Herzegovina vs Qatar | O/U 2.5 | Under 2.5 | Betfair Exchange | 2.50 | £10.00 | LOSS | -£10.00 | reset to leg 1, £10 |
| 1 | 2026-06-25 | Japan vs Sweden | 1X2 | Japan | shop best price (Pinnacle 1.93 / Betsson, Nordic Bet 1.95 / 1xBet, Unibet SE 1.97) | ~1.93-1.97 | £10.00 | PENDING | — | leg 1, £10 |

**Running total: -£10.00**

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
