"""Tests for the racing value engine.

Run:
    python -m pytest tests/ -q        # from horse-racing/
or:
    python tests/test_value.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from racing import value
from racing.regions import resolve_countries, betfair_market_filter


def _close(a, b, tol=1e-9):
    return abs(a - b) <= tol


def test_devig_sums_to_one():
    probs = value.devig_market([2.0, 4.0, 4.0])      # raw inv 0.5+0.25+0.25 = 1.0
    assert _close(sum(probs), 1.0)
    assert _close(probs[0], 0.5)


def test_overround():
    # Two-runner book at 1.90 / 1.90 -> implied 1.0526, overround ~5.26%.
    assert _close(value.market_overround([1.90, 1.90]), 2 / 1.9 - 1.0)


def test_back_value_positive_and_negative():
    # True 50%, back at 2.20 -> EV = 0.5*1.2 - 0.5 = +0.10.
    v = value.back_value(0.5, 2.20)
    assert _close(v.ev, 0.10)
    assert v.positive
    # Back at 1.90 -> EV = 0.5*0.9 - 0.5 = -0.05.
    assert value.back_value(0.5, 1.90).ev < 0


def test_back_kelly_identity():
    # Kelly = (bp - q)/b with b = net fractional odds.
    p, odds = 0.5, 2.20
    v = value.back_value(p, odds)
    b = odds - 1.0
    assert _close(v.kelly, (b * p - (1 - p)) / b)


def test_commission_reduces_back_value():
    no_comm = value.back_value(0.5, 2.20, commission=0.0).ev
    comm = value.back_value(0.5, 2.20, commission=0.05).ev
    assert comm < no_comm


def test_lay_value_longshot_is_positive():
    # True 10%, lay at 8.0 (fair lay would be 10.0) -> EV = 0.9 - 0.1*7 = +0.20.
    v = value.lay_value(0.10, 8.0, commission=0.0)
    assert _close(v.ev, 0.20)
    assert v.positive


def test_lay_value_fair_favourite_is_negative():
    # True 50%, lay at 2.0 (fair) -> EV = 0.5 - 0.5*1 = 0; just below 0 with commission.
    assert value.lay_value(0.5, 2.0, commission=0.02).ev < 0


def test_stake_zero_when_no_edge():
    v = value.back_value(0.5, 1.90)
    assert value.stake_from_kelly(v, 1000) == 0.0


def test_regions_expand():
    assert resolve_countries(["uk"]) == ["GB"]
    assert "US" in resolve_countries(["overseas"])
    assert set(resolve_countries(["uk", "ireland"])) == {"GB", "IE"}
    mf = betfair_market_filter(["uk", "ireland"])
    assert mf["eventTypeIds"] == ["7"]
    assert set(mf["marketCountries"]) == {"GB", "IE"}


def run():
    fns = [g for n, g in globals().items() if n.startswith("test_")]
    for fn in fns:
        fn()
    print(f"all {len(fns)} racing tests passed")


if __name__ == "__main__":
    run()
