"""
Market-prior blend ("Model 6" from the project spec, in its honest form).

What this is NOT: a full Bayesian model with a proper likelihood over
discrete evidence (injuries, lineups, tactical matchups) -- we don't have
those data feeds wired up yet, so building a "posterior" on top of them
would be fabricated precision. What this IS: a precision-weighted blend
of the market's de-vigged probability (which already encodes everything
the market knows) and our own Dixon-Coles goal-model probability (which
encodes only goals history), in log-odds space:

    blended_logit_k = w * model_logit_k + (1 - w) * market_logit_k   for k in {H, D, A}
    blended_prob = softmax(blended_logit)

w is not hand-picked. It is fit by nested walk-forward validation: for
each test season, w is chosen to minimise log loss on the *previous*
season only (never the test season itself, never future seasons), then
applied out-of-sample to the test season. This follows the spec's
explicit rule: "Weights MUST be determined using historical validation.
Do NOT manually choose weights because they feel right."

As real exogenous evidence (confirmed lineups, injury tiers, Betfair
liquidity/order-flow) gets wired in, this is the natural place to extend
from a single scalar w into per-evidence-source Bayesian updates.
"""
import numpy as np


def _to_logit(probs: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    p = np.clip(probs, eps, 1 - eps)
    return np.log(p)  # unnormalised log-prob is enough; softmax renormalises


def _softmax(x: np.ndarray) -> np.ndarray:
    x = x - x.max(axis=-1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=-1, keepdims=True)


def blend(model_probs: np.ndarray, market_probs: np.ndarray, w: float) -> np.ndarray:
    """model_probs, market_probs: (n,3) arrays [home,draw,away]. Returns (n,3)."""
    logit_m = _to_logit(model_probs)
    logit_k = _to_logit(market_probs)
    blended_logit = w * logit_m + (1 - w) * logit_k
    return _softmax(blended_logit)


def fit_weight(model_probs: np.ndarray, market_probs: np.ndarray, outcomes: np.ndarray, grid: np.ndarray | None = None) -> tuple[float, float]:
    """Grid-search w in [0,1] minimising log loss. Returns (best_w, best_log_loss)."""
    if grid is None:
        grid = np.linspace(0.0, 1.0, 21)
    best_w, best_ll = 0.0, np.inf
    for w in grid:
        blended = blend(model_probs, market_probs, w)
        p_true = np.clip(blended[np.arange(len(outcomes)), outcomes], 1e-15, 1 - 1e-15)
        ll = -np.mean(np.log(p_true))
        if ll < best_ll:
            best_w, best_ll = float(w), float(ll)
    return best_w, best_ll
