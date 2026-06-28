"""Multi-runner racing value engine — back & lay EV vs a sharp reference.

The lesson from the football work (see ../prophitbet-predictor/STRATEGY.md):
you cannot out-predict a sharp market, so use it as the truth and find prices
elsewhere that beat it. For racing the sharpest cheap reference is the Betfair
Exchange. This module:

  1. De-vigs a sharp win-market (N runners) into true probabilities.
  2. Scores BACK offers (soft bookmakers) and LAY offers (exchange) for +EV
     against those true probabilities, net of exchange commission.
  3. Sizes each with fractional Kelly (lay stake expressed as liability).

Pure math, no I/O. Feed it odds from feed.py (sample file or live Betfair).
"""

from __future__ import annotations

from dataclasses import dataclass


# --------------------------------------------------------------------------- #
# Probabilities
# --------------------------------------------------------------------------- #
def implied_prob(decimal_odds: float) -> float:
    if decimal_odds <= 1.0:
        raise ValueError("decimal odds must be > 1.0")
    return 1.0 / decimal_odds


def devig_market(decimal_odds: list[float]) -> list[float]:
    """Normalize a full win-market's implied probabilities to sum to 1.0.

    Works for any number of runners. Removes the book/exchange overround
    proportionally (the standard multi-runner de-vig).
    """
    inv = [implied_prob(o) for o in decimal_odds]
    total = sum(inv)
    return [i / total for i in inv]


def market_overround(decimal_odds: list[float]) -> float:
    """Total implied probability minus 1 (the book's margin; ~0 on the exchange)."""
    return sum(implied_prob(o) for o in decimal_odds) - 1.0


# --------------------------------------------------------------------------- #
# Back / Lay valuation
# --------------------------------------------------------------------------- #
@dataclass
class Valuation:
    side: str            # "back" or "lay"
    ev: float            # expected profit per £1 backer stake
    kelly: float         # full-Kelly fraction of bankroll (lay: of bankroll as liability)

    @property
    def positive(self) -> bool:
        return self.ev > 0 and self.kelly > 0


def back_value(true_prob: float, odds: float, commission: float = 0.0) -> Valuation:
    """Value of BACKING a runner at decimal ``odds`` given ``true_prob``.

    Commission applies to net winnings (exchange) — pass 0 for a bookmaker.
    """
    net = (odds - 1.0) * (1.0 - commission)          # net fractional odds after commission
    ev = true_prob * net - (1.0 - true_prob)
    kelly = ev / net if net > 0 else 0.0             # (bp - q)/b
    return Valuation(side="back", ev=ev, kelly=kelly)


def lay_value(true_prob: float, lay_odds: float, commission: float = 0.0) -> Valuation:
    """Value of LAYING a runner at decimal ``lay_odds`` given ``true_prob``.

    EV is per £1 of backer stake matched; liability is (lay_odds - 1) per £1.
    Laying is a back of the liability at equivalent odds lay/(lay-1) on the
    complement, so Kelly is expressed as a fraction of bankroll used as liability.
    """
    if lay_odds <= 1.0:
        raise ValueError("lay odds must be > 1.0")
    q = 1.0 - true_prob                               # P(selection loses) = you win the stake
    ev = q * (1.0 - commission) - true_prob * (lay_odds - 1.0)
    net_on_liability = (1.0 - commission) / (lay_odds - 1.0)   # equiv. back net odds
    kelly = (q * net_on_liability - true_prob) / net_on_liability if net_on_liability > 0 else 0.0
    return Valuation(side="lay", ev=ev, kelly=kelly)


def stake_from_kelly(v: Valuation, bankroll: float, fraction: float = 0.25) -> float:
    """Recommended stake (back) or liability (lay) using fractional Kelly."""
    if v.kelly <= 0:
        return 0.0
    return max(0.0, v.kelly * fraction * bankroll)
