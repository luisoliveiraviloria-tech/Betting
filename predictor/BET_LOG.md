# Bet ladder log

Goal: £10 stake, double up on each win, 7 wins in a row → ~£600+. Stake
resets to £10 after any loss. One log entry per leg attempted.

| # | Date | Fixture | Market | Selection | Bookmaker | Odds | Stake | Result | P&L | Ladder leg after |
|---|------|---------|--------|-----------|-----------|------|-------|--------|-----|-------------------|
| 1 | 2026-06-24 | Bosnia-Herzegovina vs Qatar | O/U 2.5 | Under 2.5 | Betfair Exchange | 2.50 | £10.00 | LOSS | -£10.00 | reset to leg 1, £10 |

**Running total: -£10.00**

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
