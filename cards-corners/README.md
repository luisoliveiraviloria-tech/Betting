# cards-corners

Predictive models for **total corners** and **total cards** in football, built
after our 1X2 work showed the match-result market is too efficient to beat with
public data. Secondary markets (cards/corners) are softer and less sharply
priced, so they're a more promising target — *if* the model carries real signal.

This folder answers the first, necessary question: **is there signal?** It does
NOT yet answer "does it beat the bookies" — see the data caveat below.

## Data

- **football-data.co.uk** (`fetch_data.py` → `data/matches.csv`): 21k matches,
  6 leagues (E0, E1, SP1, I1, D1, F1), 2016/17–2024/25, with corners
  (HC/AC), cards (HY/AY/HR/AR), fouls, shots and — crucially — the **Referee**.
- **Referee is only published for the English leagues (E0/E1)** — 100% there,
  0% elsewhere. So the referee-conditioned card model is England-only; corners
  and team-only cards work for all six.
- **There are NO free historical cards/corners betting odds.** That's the
  binding constraint: we can measure whether the model is calibrated and
  informative, but **not** ROI. ROI validation needs an odds source we collect
  going forward (next step).

## Models (`model.py`)

Opponent-adjusted Poisson, estimated in one **no-lookahead** chronological pass
(each match predicted from prior data only, then folded into running stats).

- **Corners**: team attack (corners won) × opponent defence (corners conceded),
  relative to the league venue baseline. Total ~ Poisson(sum).
- **Cards**: team "receive" × opponent "induce" factors, then a **referee**
  multiplier (ref's cards/game vs league). All shrunk toward league means for
  small samples, with a final shrink-to-mean (`SHRINK_*`) that fixes
  over-dispersion (raw predictions fanned out at the extremes).

## Results (`backtest.py`, 19,150 scored matches, no-lookahead)

Binary over/under calibration vs a **baseline** that knows only the league
average (beating it = real signal). Lower log-loss is better.

| Market | model log-loss | baseline | verdict |
|---|---|---|---|
| Corners O/U 9.5 | 0.6866 | 0.6903 | **weak** — barely beats league average |
| Corners O/U 10.5 | 0.6685 | 0.6723 | weak |
| Cards O/U 3.5 | 0.6578 | 0.6656 | **real signal** |
| Cards O/U 4.5 | 0.6463 | 0.6538 | real signal |

Reliability curves now track the diagonal closely after the shrink calibration.

### Referee ablation (English leagues, n=7,576) — the headline

| Cards line | with referee | no referee | league baseline |
|---|---|---|---|
| O/U 3.5 | **0.6801** | 0.6846 | 0.6930 |
| O/U 4.5 | **0.6058** | 0.6110 | 0.6182 |

with-ref > no-ref > baseline, every time. **The referee genuinely adds
predictive value for cards**, on top of team tendencies — confirming the thesis
that motivated this folder.

## Bounded ROI test (`synth_roi.py`)

No historical cards/corners odds exist (free), so true ROI can't be measured.
Instead we bet the model against a synthetic book priced off the league-average
baseline + a 5.7% margin — the OPTIMISTIC case (book no sharper than the naive
average). Then we slide the book from naive (beta=0) to as-sharp-as-our-model
(beta=1) to see how fast the edge dies:

| beta (book sharpness) | Cards O/U 4.5 ROI | Corners O/U 9.5 ROI |
|---|---|---|
| 0.0 (naive) | +13.7% | +7.1% |
| 0.5 | +9.5% | +4.4% |
| 0.75 | **+7.8%** | +2.5% |
| 1.0 (= our model) | 0.0% | 0.0% |

**Cards edge is robust** (survives a fairly sharp book); **corners is fragile**
(gone by beta≈0.75). Referee helps: cards O/U 4.5 +19.8% with-ref vs +17.7%
without (English leagues). Caveat: where the *real* card book sits on the beta
axis is unknown — that's the one thing only live odds can settle.

## Bottom line

- **Corners**: weak and fragile — not worth betting with this model.
- **Cards**: genuine, well-calibrated edge that survives a moderately sharp
  synthetic book, strongest where the referee is known (England). The most
  promising thing we've built.
- **Still unconfirmed**: whether real bookmaker card lines are soft enough
  (beta < ~0.9) for the edge to survive. Next step is logging live card odds to
  locate the real book on the beta axis. **Not validated for staking yet.**

## Usage

```
python3 fetch_data.py
python3 backtest.py                 # calibration + referee ablation
python3 backtest.py --league E0
python3 predict.py --league E0 --home "Man United" --away "Liverpool" --ref "M Oliver"
```

## Next step

Collect live cards/corners odds (a different feed from the goals/1X2 lines —
the-odds-api's standard soccer markets don't include them) to build the odds
dataset we lack, then run the same ROI/CLV gate we used on 1X2 before staking.
