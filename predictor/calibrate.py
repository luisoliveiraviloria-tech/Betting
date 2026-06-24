#!/usr/bin/env python3
"""
Calibrate the Elo -> goal-supremacy slope, home-advantage, total-goals
scaling, and Dixon-Coles rho used by elo_predict.py against real match
results, instead of the hardcoded rule-of-thumb constants.

Uses each team's actual point-in-time Elo rating as of the match date (via
elo_history.py, pulled from eloratings.net's per-team .tsv files), not
today's snapshot — this removes the staleness bias a current-Elo-as-proxy
approach would have, so the fit can safely use a longer window than a
recency-limited proxy could (--years, default 5).

Usage:
  python3 calibrate.py
  python3 calibrate.py --years 8
"""
import argparse
import csv
import datetime
import math
import os
import statistics

from elo_predict import DATA_DIR, load_team_codes, load_elo_ratings, resolve_team
from elo_history import get_team_history, elo_as_of, code_to_name_map


def load_matches(years):
    cutoff = datetime.date.today() - datetime.timedelta(days=365 * years)
    alias_to_code = load_team_codes()
    ratings = load_elo_ratings()
    code_to_name = code_to_name_map()
    history_cache = {}

    def history_for(code):
        if code not in history_cache:
            history_cache[code] = get_team_history(code, code_to_name=code_to_name)
        return history_cache[code]

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
            home_elo = elo_as_of(home_code, date, history=history_for(home_code))
            away_elo = elo_as_of(away_code, date, history=history_for(away_code))
            if home_elo is None or away_elo is None:
                continue
            rows.append({
                "elo_diff": home_elo - away_elo,
                "home_score": int(row["home_score"]),
                "away_score": int(row["away_score"]),
                "goal_diff": int(row["home_score"]) - int(row["away_score"]),
                "total_goals": int(row["home_score"]) + int(row["away_score"]),
                "neutral": row["neutral"] == "TRUE",
            })
    return rows


def poisson(lam, k):
    return math.exp(-lam) * lam**k / math.factorial(k)


def fit_rho(matches, elo_points_per_goal, home_adv_elo_equiv, total_goals_baseline, total_goals_per_elo_gap):
    """Grid-search rho (step 0.001) to maximize the log-likelihood of real
    scorelines under our elo-derived lambda_home/lambda_away, holding every
    other constant fixed. Dixon-Coles tau only touches the four cells
    (0,0)/(0,1)/(1,0)/(1,1), so the per-rho cost is O(1) per match (no need
    to build the full score grid here) — just those four cells plus the
    independent-Poisson probability of the actual scoreline."""
    prepared = []
    for m in matches:
        elo_diff = m["elo_diff"]
        home_advantage = 0.0 if m["neutral"] else home_adv_elo_equiv
        goal_supremacy = (elo_diff + home_advantage) / elo_points_per_goal
        total_goals = total_goals_baseline + total_goals_per_elo_gap * abs(elo_diff)
        lam = max((total_goals + goal_supremacy) / 2, 0.05)
        mu = max((total_goals - goal_supremacy) / 2, 0.05)
        hs, asc = m["home_score"], m["away_score"]
        p_lam0, p_lam1 = poisson(lam, 0), poisson(lam, 1)
        p_mu0, p_mu1 = poisson(mu, 0), poisson(mu, 1)
        p_obs_indep = poisson(lam, hs) * poisson(mu, asc)
        prepared.append((lam, mu, hs, asc, p_lam0, p_lam1, p_mu0, p_mu1, p_obs_indep))

    best_rho, best_ll = 0.0, float("-inf")
    for step in range(-300, 301):
        rho = step / 1000.0
        ll = 0.0
        for lam, mu, hs, asc, p_lam0, p_lam1, p_mu0, p_mu1, p_obs_indep in prepared:
            p00, p01, p10, p11 = p_lam0 * p_mu0, p_lam0 * p_mu1, p_lam1 * p_mu0, p_lam1 * p_mu1
            tau00, tau01, tau10, tau11 = 1 - lam * mu * rho, 1 + lam * rho, 1 + mu * rho, 1 - rho
            correction = (tau00 - 1) * p00 + (tau01 - 1) * p01 + (tau10 - 1) * p10 + (tau11 - 1) * p11
            total = 1.0 + correction
            if (hs, asc) == (0, 0):
                p_obs = tau00 * p00
            elif (hs, asc) == (0, 1):
                p_obs = tau01 * p01
            elif (hs, asc) == (1, 0):
                p_obs = tau10 * p10
            elif (hs, asc) == (1, 1):
                p_obs = tau11 * p11
            else:
                p_obs = p_obs_indep
            ll += math.log(max(p_obs / total, 1e-12))
        if ll > best_ll:
            best_ll, best_rho = ll, rho
    return best_rho, best_ll


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, default=5)
    args = parser.parse_args()

    matches = load_matches(args.years)
    neutral = [m for m in matches if m["neutral"]]
    home = [m for m in matches if not m["neutral"]]
    print(f"Matches used: {len(matches)} total ({len(neutral)} neutral, {len(home)} true home), "
          f"last {args.years} years, point-in-time Elo as of each match date.\n")

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
          f"(old model: 0, i.e. blowouts were not modeled as higher-scoring)\n")

    rho, ll = fit_rho(matches, elo_points_per_goal, home_adv_elo_equiv, intercept_t, slope_t)
    print(f"Dixon-Coles fit: rho = {rho:+.4f} (log-likelihood {ll:.1f}); rho=0 reproduces independent Poisson")
    print(f"  -> negative rho means real results have MORE 0-0/1-0/0-1 draws/near-draws than independent "
          f"Poisson predicts (the classic Dixon-Coles low-score correlation)")


if __name__ == "__main__":
    main()
