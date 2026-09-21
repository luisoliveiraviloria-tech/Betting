"""
Dixon-Coles (1997) adjustment on top of the independent-Poisson model.

Two additions over plain Poisson:
  1. A low-score correlation term tau(x, y; lambda, mu, rho) that corrects
     the well-documented tendency of independent Poisson to misprice
     0-0, 1-0, 0-1 and 1-1 scorelines.
  2. Optional exponential time-decay weighting (xi) so recent matches
     carry more weight in the fit than old ones, per the original paper's
     treatment of a team's "current" strength.

Reference: Dixon, M.J. and Coles, S.G. (1997), "Modelling Association
Football Scores and Inefficiencies in the Football Betting Market",
Journal of the Royal Statistical Society: Series C, 46(2), 265-280.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

from quant.models.poisson import PoissonRatings


def tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    """Dixon-Coles low-score correlation adjustment factor."""
    if x == 0 and y == 0:
        return 1 - lam * mu * rho
    if x == 0 and y == 1:
        return 1 + lam * rho
    if x == 1 and y == 0:
        return 1 + mu * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def time_decay_weights(dates: pd.Series, as_of: pd.Timestamp, xi: float) -> np.ndarray:
    """Exponential decay: weight = exp(-xi * days_since_match / 365)."""
    days = (as_of - dates).dt.days.clip(lower=0).to_numpy()
    return np.exp(-xi * days / 365.0)


@dataclass
class DixonColesRatings(PoissonRatings):
    rho: float = 0.0


class DixonColesModel:
    def __init__(self, max_goals: int = 10, xi: float = 0.0):
        """xi=0 disables time decay (matches weighted equally); xi~0.0018-0.005
        per Dixon-Coles' own fitted range is typical for weekly domestic leagues."""
        self.max_goals = max_goals
        self.xi = xi
        self.ratings: DixonColesRatings | None = None

    def fit(self, matches: pd.DataFrame, as_of: pd.Timestamp | None = None) -> "DixonColesModel":
        teams = sorted(set(matches["home_team"]) | set(matches["away_team"]))
        idx = {t: i for i, t in enumerate(teams)}
        n = len(teams)

        home_idx = matches["home_team"].map(idx).to_numpy()
        away_idx = matches["away_team"].map(idx).to_numpy()
        hg = matches["home_goals"].to_numpy()
        ag = matches["away_goals"].to_numpy()

        if self.xi > 0:
            as_of = as_of or matches["date"].max()
            w = time_decay_weights(matches["date"], as_of, self.xi)
        else:
            w = np.ones(len(matches))

        def unpack(x):
            attack = np.concatenate([[0.0], x[: n - 1]])
            defense = np.concatenate([[0.0], x[n - 1 : 2 * n - 2]])
            home_adv, mu_, rho = x[-3], x[-2], x[-1]
            return attack, defense, home_adv, mu_, rho

        def neg_log_lik(x):
            attack, defense, home_adv, mu_, rho = unpack(x)
            lam_h = np.exp(mu_ + home_adv + attack[home_idx] - defense[away_idx])
            lam_a = np.exp(mu_ + attack[away_idx] - defense[home_idx])

            base_ll = poisson.logpmf(hg, lam_h) + poisson.logpmf(ag, lam_a)

            # low-score correction, only affects (0,0),(1,0),(0,1),(1,1)
            tau_vals = np.array([
                tau(int(h), int(a), lh, la, rho)
                for h, a, lh, la in zip(hg, ag, lam_h, lam_a)
            ])
            tau_vals = np.clip(tau_vals, 1e-6, None)  # guard against invalid rho pushing tau <= 0
            ll = w * (base_ll + np.log(tau_vals))

            reg = 0.01 * np.sum(attack**2) + 0.01 * np.sum(defense**2)
            return -np.sum(ll) + reg

        x0 = np.zeros(2 * n - 2 + 3)
        x0[-3], x0[-2], x0[-1] = 0.25, 0.1, 0.0  # home_adv, mu, rho
        bounds = [(None, None)] * (2 * n - 2 + 2) + [(-1.0, 1.0)]  # keep rho in a sane range
        res = minimize(neg_log_lik, x0, method="L-BFGS-B", bounds=bounds)

        attack, defense, home_adv, mu_, rho = unpack(res.x)
        self.ratings = DixonColesRatings(
            teams=teams,
            attack=dict(zip(teams, attack)),
            defense=dict(zip(teams, defense)),
            home_adv=home_adv,
            mu=mu_,
            rho=rho,
        )
        return self

    def match_probabilities(self, home: str, away: str) -> dict:
        if self.ratings is None:
            raise RuntimeError("call .fit() first")
        if home not in self.ratings.attack or away not in self.ratings.attack:
            raise KeyError(f"unseen team(s): {home}, {away} not in training data")

        lam_h, lam_a = self.ratings.lambdas(home, away)
        gh = np.arange(self.max_goals + 1)
        ga = np.arange(self.max_goals + 1)
        ph = poisson.pmf(gh, lam_h)
        pa = poisson.pmf(ga, lam_a)
        grid = np.outer(ph, pa)

        for x in range(2):
            for y in range(2):
                grid[x, y] *= tau(x, y, lam_h, lam_a, self.ratings.rho)
        grid = grid / grid.sum()  # renormalise after the low-score adjustment

        p_home = np.sum(np.tril(grid, -1))
        p_draw = np.sum(np.diag(grid))
        p_away = np.sum(np.triu(grid, 1))

        return {
            "home_win": p_home,
            "draw": p_draw,
            "away_win": p_away,
            "lambda_home": lam_h,
            "lambda_away": lam_a,
            "rho": self.ratings.rho,
            "grid": grid,
        }
