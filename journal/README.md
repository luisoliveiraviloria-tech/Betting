# Bet journal

Append-only record of every prediction and its outcome, written by
`race-predictor/scripts/journal.py`. `bets.csv` is the live ledger.

- Log a bet:    `journal.py log --track ... --time ... --selection ... --odds ... --stake ...`
- Settle it:    `journal.py settle --id N --result win|lose [--closing-odds ...]`
- Performance:  `journal.py report`  (strike rate, ROI, CLV, calibration)

Why it exists: a single race tells you nothing. Over dozens of logged bets,
closing-line value and ROI reveal whether the model has a real edge. Keep it
honest — log the losers too.
