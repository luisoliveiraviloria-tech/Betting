#!/usr/bin/env python3
"""
Predict a national-team football match outcome from World Football Elo
ratings (eloratings.net) using a standard Elo -> goal-supremacy -> Poisson
scoreline model.

Data sources (free, no API key, refreshed by fetch_data.sh):
  - data/elo_world.tsv / data/elo_teams.tsv  (eloratings.net current ratings)
  - data/international_results.csv          (martj42/international_results,
                                               full match history since 1872,
                                               used only as a fallback name
                                               lookup / sanity reference)

Usage:
  python3 elo_predict.py --home Canada --away Switzerland --home-adv 100
  python3 elo_predict.py --home "Bosnia and Herzegovina" --away Qatar
"""
import argparse
import csv
import math
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
AVG_TOTAL_GOALS = 2.6  # typical total goals in a competitive int'l match
ELO_POINTS_PER_GOAL = 200  # rough Elo-to-goal-supremacy conversion


# Extra aliases for team names as spelled by football-data.org / the-odds-api,
# which sometimes differ from eloratings.net's en.teams.tsv spellings.
EXTRA_ALIASES = {
    "bosnia-herzegovina": "BA",
    "cape verde islands": "CV",
    "congo dr": "CD",
    "czech republic": "CZ",
}


def load_team_codes():
    """Map every name/alias in elo_teams.tsv to its 2-letter eloratings code."""
    alias_to_code = {}
    with open(os.path.join(DATA_DIR, "elo_teams.tsv"), encoding="utf-8") as f:
        for line in f:
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 2:
                continue
            code = fields[0]
            for alias in fields[1:]:
                if alias:
                    alias_to_code[alias.strip().lower()] = code
    alias_to_code.update(EXTRA_ALIASES)
    return alias_to_code


def load_elo_ratings():
    """code -> (rank, current_elo)"""
    ratings = {}
    with open(os.path.join(DATA_DIR, "elo_world.tsv"), encoding="utf-8") as f:
        for line in f:
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 4:
                continue
            rank, code, elo = fields[0], fields[2], fields[3]
            try:
                ratings[code] = (int(rank), int(elo))
            except ValueError:
                continue
    return ratings


def resolve_team(name, alias_to_code, ratings):
    code = alias_to_code.get(name.strip().lower())
    if code is None or code not in ratings:
        raise ValueError(f"Could not find an Elo rating for {name!r}")
    return code


def poisson(lam, k):
    return math.exp(-lam) * lam**k / math.factorial(k)


def match_probabilities(lambda_home, lambda_away, max_goals=10):
    home = draw = away = 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = poisson(lambda_home, i) * poisson(lambda_away, j)
            if i > j:
                home += p
            elif i == j:
                draw += p
            else:
                away += p
    return home, draw, away


def predict(home_name, away_name, home_advantage=0.0, total_goals=AVG_TOTAL_GOALS):
    alias_to_code = load_team_codes()
    ratings = load_elo_ratings()

    home_code = resolve_team(home_name, alias_to_code, ratings)
    away_code = resolve_team(away_name, alias_to_code, ratings)
    home_rank, home_elo = ratings[home_code]
    away_rank, away_elo = ratings[away_code]

    rating_diff = (home_elo - away_elo) + home_advantage
    goal_supremacy = rating_diff / ELO_POINTS_PER_GOAL

    lambda_home = (total_goals + goal_supremacy) / 2
    lambda_away = (total_goals - goal_supremacy) / 2
    lambda_home = max(lambda_home, 0.05)
    lambda_away = max(lambda_away, 0.05)

    p_home, p_draw, p_away = match_probabilities(lambda_home, lambda_away)

    return {
        "home": home_name,
        "away": away_name,
        "home_elo": home_elo,
        "away_elo": away_elo,
        "home_rank": home_rank,
        "away_rank": away_rank,
        "home_advantage": home_advantage,
        "lambda_home": lambda_home,
        "lambda_away": lambda_away,
        "p_home": p_home,
        "p_draw": p_draw,
        "p_away": p_away,
    }


def format_prediction(result):
    return (
        f"{result['home']} (Elo {result['home_elo']}, #{result['home_rank']}) vs "
        f"{result['away']} (Elo {result['away_elo']}, #{result['away_rank']})\n"
        f"  expected goals: {result['lambda_home']:.2f} - {result['lambda_away']:.2f}\n"
        f"  Home {result['p_home']*100:.1f}%  Draw {result['p_draw']*100:.1f}%  "
        f"Away {result['p_away']*100:.1f}%"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True)
    parser.add_argument("--away", required=True)
    parser.add_argument(
        "--home-adv",
        type=float,
        default=0.0,
        help="Elo-point home-advantage bonus (0 for neutral-venue matches, "
        "~100 for a true home fixture)",
    )
    args = parser.parse_args()
    result = predict(args.home, args.away, home_advantage=args.home_adv)
    print(format_prediction(result))


if __name__ == "__main__":
    main()
