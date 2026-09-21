"""
Independent Poisson goals model (Maher, 1982): each team has an attack
and defense strength rating; expected goals for a fixture are a product
of attack x opponent-defense x home advantage.

    log(lambda_home) = mu + home_adv + attack[home] - defense[away]
    log(lambda_away) = mu + attack[away] - defense[home]

Fit by maximum likelihood over historical match goal counts. This is the
baseline goal model; quant/models/dixon_coles.py adds the low-score
correlation adjustment on top of the same rating structure.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson


@dataclass
class PoissonRatings:
    teams: list[str]
    attack: dict[str, float]
    defense: dict[str, float]
    home_adv: float
    mu: float

    def lambdas(self, home: str, away: str) -> tuple[float, float]:
        lam_home = np.exp(self.mu + self.home_adv + self.attack[home] - self.defense[away])
        lam_away = np.exp(self.mu + self.attack[away] - self.defense[home])
        return lam_home, lam_away


class PoissonModel:
    """Independent-Poisson goal model with team attack/defense ratings."""

    def __init__(self, max_goals: int = 10):
        self.max_goals = max_goals
        self.ratings: PoissonRatings | None = None

    def fit(self, matches: pd.DataFrame, weights: np.ndarray | None = None) -> "PoissonModel":
        teams = sorted(set(matches["home_team"]) | set(matches["away_team"]))
        idx = {t: i for i, t in enumerate(teams)}
        n = len(teams)

        home_idx = matches["home_team"].map(idx).to_numpy()
        away_idx = matches["away_team"].map(idx).to_numpy()
        hg = matches["home_goals"].to_numpy()
        ag = matches["away_goals"].to_numpy()
        w = weights if weights is not None else np.ones(len(matches))

        # params: [attack(n), defense(n), home_adv, mu]; one attack/defense pinned to 0 for identifiability.
        def unpack(x):
            attack = np.concatenate([[0.0], x[: n - 1]])
            defense = np.concatenate([[0.0], x[n - 1 : 2 * n - 2]])
            home_adv, mu = x[-2], x[-1]
            return attack, defense, home_adv, mu

        def neg_log_lik(x):
            attack, defense, home_adv, mu = unpack(x)
            lam_h = np.exp(mu + home_adv + attack[home_idx] - defense[away_idx])
            lam_a = np.exp(mu + attack[away_idx] - defense[home_idx])
            ll = w * (poisson.logpmf(hg, lam_h) + poisson.logpmf(ag, lam_a))
            # light L2 regularisation on ratings to prevent runaway estimates for teams with few games
            reg = 0.01 * np.sum(attack**2) + 0.01 * np.sum(defense**2)
            return -np.sum(ll) + reg

        x0 = np.zeros(2 * n - 2 + 2)
        x0[-2], x0[-1] = 0.25, 0.1
        res = minimize(neg_log_lik, x0, method="L-BFGS-B")

        attack, defense, home_adv, mu = unpack(res.x)
        self.ratings = PoissonRatings(
            teams=teams,
            attack=dict(zip(teams, attack)),
            defense=dict(zip(teams, defense)),
            home_adv=home_adv,
            mu=mu,
        )
        return self

    def match_probabilities(self, home: str, away: str) -> dict:
        """Returns P(home win), P(draw), P(away win), and the full scoreline grid."""
        if self.ratings is None:
            raise RuntimeError("call .fit() first")
        if home not in self.ratings.attack or away not in self.ratings.attack:
            raise KeyError(f"unseen team(s): {home}, {away} not in training data")

        lam_h, lam_a = self.ratings.lambdas(home, away)
        gh = np.arange(self.max_goals + 1)
        ga = np.arange(self.max_goals + 1)
        ph = poisson.pmf(gh, lam_h)
        pa = poisson.pmf(ga, lam_a)
        grid = np.outer(ph, pa)  # grid[i, j] = P(home scores i, away scores j)

        p_home = np.sum(np.tril(grid, -1))
        p_draw = np.sum(np.diag(grid))
        p_away = np.sum(np.triu(grid, 1))

        return {
            "home_win": p_home,
            "draw": p_draw,
            "away_win": p_away,
            "lambda_home": lam_h,
            "lambda_away": lam_a,
            "grid": grid,
        }
