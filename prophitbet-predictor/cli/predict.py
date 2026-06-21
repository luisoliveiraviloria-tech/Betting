#!/usr/bin/env python3
"""Predicts the outcome of a single match using a previously trained model.
No GUI, no browser — just league id, team names, odds and (optionally) a date.

Example:
    python cli/predict.py --league-id epl --model-id epl-rf \\
        --home "Arsenal" --away "Chelsea" --odds 1.80 3.40 4.20
"""
import argparse
import sys
from datetime import date

import pandas as pd

from _common import fuzzy_match_team

from src.database.league import LeagueDatabase
from src.database.model import ModelDatabase
from src.preprocessing.utils.inputs import construct_inputs_by_teams
from src.preprocessing.utils.target import TargetType

RESULT_LABELS = {0: 'Home Win', 1: 'Draw', 2: 'Away Win'}
OVER_UNDER_LABELS = {0: 'Under 2.5', 1: 'Over 2.5'}

FUZZY_WARN_THRESHOLD = 0.6


def resolve_team(name: str, known_teams) -> str:
    match, ratio = fuzzy_match_team(name, known_teams)
    if match is None:
        sys.exit(f'No teams found in this league\'s dataset to match against "{name}".')
    if match != name:
        print(f'Matched "{name}" -> "{match}" (similarity {ratio:.2f})', file=sys.stderr)
    if ratio < FUZZY_WARN_THRESHOLD:
        print(f'Warning: low-confidence team match for "{name}".', file=sys.stderr)
    return match


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--league-id', required=True, help='League id created via setup_league.py.')
    parser.add_argument('--model-id', required=True, help='Model id created via train.py.')
    parser.add_argument('--home', required=True, help='Home team name (fuzzy-matched against the league dataset).')
    parser.add_argument('--away', required=True, help='Away team name (fuzzy-matched against the league dataset).')
    parser.add_argument('--odds', nargs=3, type=float, metavar=('HOME', 'DRAW', 'AWAY'), required=True, help='Bookmaker odds for 1 / X / 2.')
    parser.add_argument('--date', default=date.today().strftime('%Y-%m-%d'), help='Match date, YYYY-MM-DD (default: today).')
    args = parser.parse_args()

    league_db = LeagueDatabase()
    df = league_db.load_league(league_id=args.league_id)
    if df is None:
        sys.exit(f'League "{args.league_id}" not found. Run setup_league.py first.')

    known_teams = sorted(set(df['Home'].unique()) | set(df['Away'].unique()))
    home = resolve_team(args.home, known_teams)
    away = resolve_team(args.away, known_teams)

    model_db = ModelDatabase(league_id=args.league_id)
    model, config = model_db.load_model(model_id=args.model_id)
    if model is None:
        available = model_db.get_model_ids()
        sys.exit(f'Model "{args.model_id}" not found for league "{args.league_id}". Available: {available}')

    match_df = pd.DataFrame({
        'Date': [args.date],
        'Home': [home],
        'Away': [away],
        '1': [args.odds[0]],
        'X': [args.odds[1]],
        '2': [args.odds[2]],
    })
    match_df = construct_inputs_by_teams(df=df, match_df=match_df)

    probs = model.predict_proba(df=match_df)[0]
    labels = RESULT_LABELS if config['target_type'] == TargetType.RESULT else OVER_UNDER_LABELS

    print(f'{home} vs {away} on {args.date} (odds {args.odds[0]} / {args.odds[1]} / {args.odds[2]})')
    for i, label in labels.items():
        print(f'  {label}: {probs[i]:.1%}')
    predicted = labels[int(probs.argmax())]
    print(f'Predicted: {predicted}')


if __name__ == '__main__':
    main()
