#!/usr/bin/env python3
"""Downloads a league's historical match data and computes the rolling-form
statistics needed for training/prediction. Run once per league before
train.py / predict.py.

Examples:
    python cli/setup_league.py --list
    python cli/setup_league.py --country England --name Premier-League --league-id epl
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.database.league import LeagueDatabase
from src.preprocessing.statistics import StatisticsEngine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', action='store_true', help='List all available leagues and exit.')
    parser.add_argument('--country', help='League country, e.g. "England".')
    parser.add_argument('--name', help='League name, e.g. "Premier-League".')
    parser.add_argument('--league-id', help='Unique id to save this league under, e.g. "epl".')
    parser.add_argument('--start-year', type=int, default=None, help='First season to download (default: league default).')
    parser.add_argument('--history-window', type=int, default=10, help='Number of past matches used to compute rolling form stats.')
    parser.add_argument('--goal-diff-margin', type=int, default=2, help='Goal difference considered an outstanding win/loss.')
    args = parser.parse_args()

    db = LeagueDatabase()

    if args.list:
        for league in db.leagues:
            print(f'{league.country} - {league.name}')
        return

    if not args.country or not args.name or not args.league_id:
        parser.error('--country, --name and --league-id are required (or pass --list).')

    base_league = next(
        (
            league for league in db.leagues
            if league.country.lower() == args.country.lower() and league.name.lower() == args.name.lower()
        ),
        None,
    )
    if base_league is None:
        sys.exit(f'No league found for country="{args.country}" name="{args.name}". Use --list to see options.')

    stats_columns = StatisticsEngine.get_basic_stat_columns()

    league = base_league.clone(
        start_year=args.start_year or base_league.start_year,
        league_id=args.league_id,
        match_history_window=args.history_window,
        goal_diff_margin=args.goal_diff_margin,
        stats_columns=stats_columns,
    )

    print(f'Downloading "{league}" from {league.url} ...')
    df = db.create_league(league=league)

    if df is None:
        sys.exit(
            'Failed to download league data (no internet connection or the host is not reachable). '
            'Check that football-data.co.uk is allowed in this environment\'s network policy.'
        )

    print(f'Saved league "{args.league_id}": {df.shape[0]} matches, seasons {df["Season"].min()}-{df["Season"].max()}.')
    teams = sorted(set(df['Home'].unique()) | set(df['Away'].unique()))
    print(f'{len(teams)} teams: {", ".join(teams)}')


if __name__ == '__main__':
    main()
