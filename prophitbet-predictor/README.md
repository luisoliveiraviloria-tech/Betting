# ProphitBet Predictor (headless)

A trimmed, GUI-free fork of [ProphitBet-Soccer-Bets-Predictor](https://github.com/kochlisGit/ProphitBet-Soccer-Bets-Predictor)
by Vasileios Kochliaridis (MIT licensed, see `LICENSE.txt`).

The original project ships a PyQt6 desktop app. This fork keeps only the
core, Qt-free logic (data download, statistics, training, prediction) and
exposes it through three small CLI scripts, so a match can be predicted by
just passing league/team/date/odds as arguments — no UI, no browser.

Excluded from the original project: the PyQt6 GUI, the TensorFlow/Keras
neural-network classifier, and the Selenium-based footystats.org fixture
scraper (not needed when teams/odds are entered manually).

## Setup

```bash
cd prophitbet-predictor
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Network requirement

League data is downloaded from **football-data.co.uk**. If this environment
restricts outbound network access, that host must be added to the egress
allowlist before `setup_league.py` can succeed (see the main repo's
instructions for how to update the allowlist).

## Workflow

### 1. Download a league

```bash
python cli/setup_league.py --list                                   # see all supported leagues
python cli/setup_league.py --country England --name Premier-League --league-id epl
```

### 2. Train a model

```bash
python cli/train.py --league-id epl --model-id epl-rf --algo randomforest --target result
```

`--algo` choices: `logistic`, `discriminant`, `decisiontree`, `randomforest`,
`xgboost`, `knn`, `naivebayes`, `svm`.
`--target` choices: `result` (Home/Draw/Away) or `over-under` (Over/Under 2.5).

### 3. Predict a match

```bash
python cli/predict.py --league-id epl --model-id epl-rf \
    --home "Arsenal" --away "Chelsea" --odds 1.80 3.40 4.20
```

Team names are fuzzy-matched against the league's dataset, so minor
spelling differences are tolerated.
