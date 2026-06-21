#!/usr/bin/env python3
"""Trains a classifier on a previously downloaded league (see setup_league.py)
and saves it so predict.py can use it later.

Example:
    python cli/train.py --league-id epl --model-id epl-rf --algo randomforest --target result
"""
import argparse
import sys

from _common import get_algorithms

from src.database.league import LeagueDatabase
from src.database.model import ModelDatabase
from src.models.trainer import Trainer
from src.preprocessing.selection import train_test_split
from src.preprocessing.utils.normalization import NormalizerType
from src.preprocessing.utils.target import TargetType

TARGETS = {'result': TargetType.RESULT, 'over-under': TargetType.OVER_UNDER}
NORMALIZERS = {'standard': NormalizerType.STANDARD, 'min-max': NormalizerType.MIN_MAX, 'max-abs': NormalizerType.MAX_ABS}


def main():
    algorithms = get_algorithms()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--league-id', required=True, help='League id created via setup_league.py.')
    parser.add_argument('--model-id', required=True, help='Unique id to save the trained model under.')
    parser.add_argument('--algo', choices=sorted(algorithms), default='randomforest', help='Classifier to train.')
    parser.add_argument('--target', choices=sorted(TARGETS), default='result', help='Prediction target.')
    parser.add_argument('--normalizer', choices=sorted(NORMALIZERS), default='standard')
    parser.add_argument('--eval-ratio', type=float, default=20.0, help='Percent of most recent matches held out for evaluation.')
    parser.add_argument('--no-calibrate', action='store_true', help='Disable probability calibration.')
    args = parser.parse_args()

    league_db = LeagueDatabase()
    df = league_db.load_league(league_id=args.league_id)
    if df is None:
        sys.exit(f'League "{args.league_id}" not found. Run setup_league.py first.')

    df = df.dropna().reset_index(drop=True)
    if df.empty:
        sys.exit('League dataset has no complete rows (not enough history). Try a larger date range.')

    train_df, eval_df = train_test_split(df=df, test_size=args.eval_ratio)

    model_cls = algorithms[args.algo]
    model = model_cls(
        league_id=args.league_id,
        model_id=args.model_id,
        target_type=TARGETS[args.target],
        normalizer=NORMALIZERS[args.normalizer],
        calibrate_probabilities=not args.no_calibrate,
    )

    metrics_df = Trainer().train(model=model, train_df=train_df, eval_df=eval_df)[1]
    print(metrics_df.to_string(index=False))

    model_db = ModelDatabase(league_id=args.league_id)
    model_db.save_model(model=model, model_config=model.get_default_model_config())
    print(f'Saved model "{args.model_id}" for league "{args.league_id}".')


if __name__ == '__main__':
    main()
