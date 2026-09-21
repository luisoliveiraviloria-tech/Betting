"""
Expected value, qualification gates and staking.

Betting at decimal odds `o` with win probability `p`, Betfair commission `c`
charged only on winnings:

    win :  +(o - 1) * (1 - c)
    lose:  -1
    EV   =  p * (o - 1) * (1 - c) - (1 - p)

Two consequences that are easy to get wrong:

  * BREAK-EVEN IS NOT o = 1/p. Commission moves it to
        o_min = 1 + (1 - p) / (p * (1 - c))
    e.g. at p = 0.50 the fair price is 2.00 but you need 2.05 to break even.

  * KELLY MUST USE THE POST-COMMISSION PAYOFF.
        b = (o - 1) * (1 - c),   f* = p - (1 - p) / b
    Using gross odds overstakes systematically.

Everything here takes the model probability as given. Whether that probability
is good enough to produce real positive EV is decided by model.py's kill test
and backtest.py -- not by this module.
"""
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

COMMISSION = 0.05


@dataclass
class Gates:
    """Qualification thresholds. Defaults are deliberately conservative."""
    min_ev: float = 0.02             # required EV per £1 staked, after commission
    min_edge: float = 0.02           # model prob must exceed market prob by this (absolute)
    min_prob: float = 0.0            # no lower bound on probability by default
    max_prob: float = 1.0
    min_price: float = 1.01
    max_price: float = 1000.0
    min_liquidity: float = 500.0     # pre-play traded volume, GBP
    max_uncertainty: float = 1.0     # cap on the band's historical calibration error
    kelly_fraction: float = 0.25     # fractional Kelly
    max_stake_frac: float = 0.05     # never risk more than this share of bankroll on one bet
    max_race_frac: float = 0.08      # ... or on one race


def fair_odds(p: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / np.clip(p, 1e-9, 1.0)


def min_acceptable_odds(p: np.ndarray | float, commission: float = COMMISSION,
                        min_ev: float = 0.0) -> np.ndarray | float:
    """Lowest price at which EV >= min_ev. Solves p*(o-1)*(1-c) - (1-p) = min_ev."""
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return 1.0 + (1.0 - p + min_ev) / (p * (1.0 - commission))


def expected_value(p: np.ndarray | float, odds: np.ndarray | float,
                   commission: float = COMMISSION) -> np.ndarray | float:
    return p * (odds - 1.0) * (1.0 - commission) - (1.0 - p)


def kelly_fraction_full(p: np.ndarray | float, odds: np.ndarray | float,
                        commission: float = COMMISSION) -> np.ndarray | float:
    """Full-Kelly stake as a fraction of bankroll, using the post-commission payoff."""
    b = (odds - 1.0) * (1.0 - commission)
    with np.errstate(divide="ignore", invalid="ignore"):
        f = np.where(b > 0, p - (1.0 - p) / b, 0.0)
    return np.clip(f, 0.0, 1.0)


def calibration_uncertainty(oos: pd.DataFrame, prob_col: str, n_bins: int = 20) -> pd.DataFrame:
    """Empirical |predicted - actual| per probability bin, from out-of-sample predictions.

    This is the honest uncertainty measure available to us: not a claim about a
    single horse, but how far this model's probabilities have historically drifted
    from reality in that range. Wide bins with few rows get a wide value.
    """
    d = oos[[prob_col, "win"]].dropna().copy()
    d["bin"] = pd.qcut(d[prob_col], n_bins, duplicates="drop")
    g = d.groupby("bin", observed=True).agg(n=("win", "size"), pred=(prob_col, "mean"),
                                            actual=("win", "mean"))
    g["abs_err"] = (g.pred - g.actual).abs()
    # binomial standard error of the observed rate, so thin bins are not trusted
    g["se"] = np.sqrt(g.actual * (1 - g.actual) / g.n)
    g["uncertainty"] = g.abs_err + g.se
    return g.reset_index()


def attach_uncertainty(df: pd.DataFrame, prob_col: str, table: pd.DataFrame) -> pd.Series:
    """Map each row's probability into the calibration table's bins."""
    edges = [iv.left for iv in table["bin"]] + [table["bin"].iloc[-1].right]
    idx = np.clip(np.searchsorted(edges, df[prob_col].values, side="right") - 1,
                  0, len(table) - 1)
    return pd.Series(table["uncertainty"].values[idx], index=df.index)


def evaluate(df: pd.DataFrame, prob_col: str = "p_blend", price_col: str = "bsp",
             gates: Gates = None, commission: float = COMMISSION,
             uncertainty: pd.Series = None) -> pd.DataFrame:
    """Add fair odds, EV, min price, Kelly stake fraction and the pass/fail reason."""
    g = gates or Gates()
    d = df.copy()
    p = d[prob_col].values
    o = d[price_col].values

    d["fair_odds"] = fair_odds(p)
    d["ev"] = expected_value(p, o, commission)
    d["min_odds"] = min_acceptable_odds(p, commission, g.min_ev)
    d["edge"] = p - 1.0 / o
    d["kelly_full"] = kelly_fraction_full(p, o, commission)
    d["stake_frac"] = np.minimum(g.kelly_fraction * d.kelly_full, g.max_stake_frac)
    d["uncertainty"] = uncertainty if uncertainty is not None else np.nan

    liq = d["pptradedvol"] if "pptradedvol" in d else pd.Series(np.inf, index=d.index)
    unc = pd.Series(d["uncertainty"], index=d.index)
    checks = {
        "ev": pd.Series(d.ev >= g.min_ev, index=d.index),
        "edge": pd.Series(d.edge >= g.min_edge, index=d.index),
        "prob": pd.Series((p >= g.min_prob) & (p <= g.max_prob), index=d.index),
        "price": pd.Series((o >= g.min_price) & (o <= g.max_price), index=d.index),
        "liquidity": pd.Series(liq.fillna(0) >= g.min_liquidity, index=d.index),
        "uncertainty": unc.isna() | (unc <= g.max_uncertainty),
        "stake": pd.Series(d.stake_frac > 0, index=d.index),
    }
    d["qualifies"] = np.logical_and.reduce([c.to_numpy(dtype=bool) for c in checks.values()])
    # first failing gate, for a readable "why not" on the daily card
    fail = pd.Series("", index=d.index, dtype=object)
    for name, ok in checks.items():
        fail = fail.mask((fail == "") & ~ok, name)
    d["fail_reason"] = fail.where(~d.qualifies, "")
    return d


def cap_race_exposure(d: pd.DataFrame, gates: Gates = None) -> pd.DataFrame:
    """Runners in one race are mutually exclusive -- cap total stake per race."""
    g = gates or Gates()
    d = d.copy()
    tot = d.groupby("race_key").stake_frac.transform("sum")
    scale = np.where(tot > g.max_race_frac, g.max_race_frac / tot.replace(0, np.nan), 1.0)
    d["stake_frac"] = d.stake_frac * np.nan_to_num(scale, nan=1.0)
    return d


def gates_dict(g: Gates) -> dict:
    return asdict(g)
