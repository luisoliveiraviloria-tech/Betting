"""
Converts raw decimal odds (which embed bookmaker/exchange margin) into
de-vigged implied probabilities, used as the market benchmark every model
must beat out-of-sample.

Two methods:
  - multiplicative: naive normalisation, P_i = (1/odds_i) / sum(1/odds_j).
    Assumes margin is spread proportionally across outcomes; ignores the
    favourite-longshot bias.
  - shin: Shin's method (Shin, 1992, "Prices of State Contingent Claims
    with Insider Trading, and the Favourite-Longshot Bias", Economic
    Journal 102) backs out an insider-trading parameter z and true
    probabilities that account for the bias for favourites being
    under-priced relative to longshots. Preferred when it converges;
    falls back to multiplicative otherwise.
"""
import numpy as np


def devig_multiplicative(odds: list[float]) -> list[float]:
    inv = [1.0 / o for o in odds]
    total = sum(inv)
    return [p / total for p in inv]


def devig_shin(odds: list[float], tol: float = 1e-10, max_iter: int = 100) -> list[float]:
    """Solves for Shin's z via bisection, then returns true probabilities."""
    inv = np.array([1.0 / o for o in odds])
    book_sum = inv.sum()  # overround, > 1

    def probs_for_z(z):
        # Shin (1992) closed form: p_i = (sqrt(z^2 + 4(1-z) pi_i^2 / book_sum) - z) / (2(1-z))
        # where pi_i = inv[i] (raw implied prob incl. margin).
        if z >= 1.0:
            z = 1.0 - 1e-9
        inner = z**2 + 4 * (1 - z) * (inv**2) / book_sum
        return (np.sqrt(inner) - z) / (2 * (1 - z))

    lo, hi = 0.0, 0.5
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        total = probs_for_z(mid).sum()
        if abs(total - 1.0) < tol:
            break
        if total > 1.0:
            lo = mid
        else:
            hi = mid
    else:
        return devig_multiplicative(odds)  # no convergence -> fall back

    p = probs_for_z(mid)
    p = p / p.sum()  # final numerical cleanup
    return p.tolist()


def implied_probabilities(odds_home: float, odds_draw: float, odds_away: float, method: str = "shin") -> dict:
    odds = [odds_home, odds_draw, odds_away]
    if method == "shin":
        p_home, p_draw, p_away = devig_shin(odds)
    elif method == "multiplicative":
        p_home, p_draw, p_away = devig_multiplicative(odds)
    else:
        raise ValueError(f"unknown method: {method}")
    return {"home_win": p_home, "draw": p_draw, "away_win": p_away}
