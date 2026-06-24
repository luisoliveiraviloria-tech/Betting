#!/usr/bin/env python3
"""
Calibrate the Elo -> goal-supremacy slope, home-advantage, and total-goals
scaling used by elo_predict.py against real match results, instead of the
hardcoded rule-of-thumb constants (200 Elo points/goal, home advantage,
constant 2.6 total goals).

Caveat: eloratings.net only publishes *current* ratings, not a historical
time series, so this uses each team's CURRENT Elo as a proxy for its
strength at match time. That's only a reasonable proxy over a short
recency window, so the fit is restricted to the last few years
(--years, default 3) to limit staleness. It is not as rigorous as a true
point-in-time Elo fit would be — treat the output as a meaningfully
better estimate than the old guesses, not a precise one.

Usage:
  python3 calibrate.py
  python3 calibrate.py --years 4
"""
import argparse
import csv
import datetime
import os
import statistics

from elo_predict import DATA_DIR, load_team_codes, load_elo_ratings, resolve_team


def load_matches(years):
    cutoff = datetime.date.today() - datetime.timedelta(days=365 * years)
    alias_to_code = load_team_codes()
    ratings = load_elo_ratings()

    rows = []
    path = os.path.join(DATA_DIR, "international_results.csv")
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["home_score"] in ("", "NA") or row["away_score"] in ("", "NA"):
                continue
            try:
                date = datetime.date.fromisoformat(row["date"])
            except ValueError:
                continue
            if date < cutoff or date > datetime.date.today():
                continue
            try:
                home_code = resolve_team(row["home_team"], alias_to_code, ratings)
                away_code = resolve_team(row["away_team"], alias_to_code, ratings)
            except ValueError:
                continue
            home_elo = ratings[home_code][1]
            away_elo = ratings[away_code][1]
            rows.append({
                "elo_diff": home_elo - away_elo,
                "goal_diff": int(row["home_score"]) - int(row["away_score"]),
                "total_goals": int(row["home_score"]) + int(row["away_score"]),
                "neutral": row["neutral"] == "TRUE",
            })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, default=3)
    args = parser.parse_args()

    matches = load_matches(args.years)
    neutral = [m for m in matches if m["neutral"]]
    home = [m for m in matches if not m["neutral"]]
    print(f"Matches used: {len(matches)} total ({len(neutral)} neutral, {len(home)} true home), "
          f"last {args.years} years, current Elo used as strength proxy.\n")

    slope_n, intercept_n = statistics.linear_regression(
        [m["elo_diff"] for m in neutral], [m["goal_diff"] for m in neutral]
    )
    elo_points_per_goal = 1 / slope_n
    print(f"Neutral-venue fit: goal_diff = {intercept_n:+.3f} + {slope_n:.5f} * elo_diff")
    print(f"  -> Elo points per goal of supremacy: {elo_points_per_goal:.1f} (old hardcoded: 200)")
    print(f"  -> intercept {intercept_n:+.3f} goals (should be ~0 with no home edge; sanity check)\n")

    slope_h, intercept_h = statistics.linear_regression(
        [m["elo_diff"] for m in home], [m["goal_diff"] for m in home]
    )
    home_adv_goals = intercept_h - intercept_n
    home_adv_elo_equiv = home_adv_goals * elo_points_per_goal
    print(f"True-home fit: goal_diff = {intercept_h:+.3f} + {slope_h:.5f} * elo_diff")
    print(f"  -> home advantage: {home_adv_goals:+.3f} goals of supremacy "
          f"= {home_adv_elo_equiv:+.0f} Elo-equivalent points (old hardcoded guess: 100)\n")

    slope_t, intercept_t = statistics.linear_regression(
        [abs(m["elo_diff"]) for m in matches], [m["total_goals"] for m in matches]
    )
    print(f"Total-goals fit: total_goals = {intercept_t:.3f} + {slope_t:.5f} * |elo_diff|")
    print(f"  -> baseline (evenly matched) total goals: {intercept_t:.2f} (old hardcoded constant: 2.6)")
    print(f"  -> total goals rise by {slope_t*100:.3f} per 100 Elo points of mismatch "
          f"(old model: 0, i.e. blowouts were not modeled as higher-scoring)")


if __name__ == "__main__":
    main()
