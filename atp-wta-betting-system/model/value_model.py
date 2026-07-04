#!/usr/bin/env python3
"""ATP/WTA value betting model.

Turns an 8-factor match assessment into:
  confidence score (0-100) -> calibrated win probability -> edge vs the
  vig-free bookmaker line -> quarter-Kelly stake -> BET / SKIP decision.

Usage:
    python3 value_model.py --example
    python3 value_model.py --match my_match.json

Factor scores are from Player A's perspective, each in [-10, +10]
(0 = neutral, +10 = maximal advantage to A). See docs/SYSTEM.md.
"""

import argparse
import json
import math
import sys
from dataclasses import dataclass, field

# ---------------------------------------------------------------- parameters

BASE_WEIGHTS = {
    "surface_elo":   0.30,  # blended surface Elo gap, score = clip(elo_diff/40, -10, 10)
    "recent_form":   0.15,  # dominance-ratio trend, last 8-10 matches
    "serve_return":  0.15,  # serve vs return matchup on this surface
    "fatigue":       0.10,  # time on court / schedule last 7-14 days
    "injury":        0.10,  # physical state (also drives hard vetoes)
    "conditions":    0.08,  # travel, altitude, weather, balls, indoor/outdoor
    "motivation":    0.07,  # stage, points defended, ranking cutoffs
    "h2h":           0.05,  # surface-specific, recency-weighted
}

TOUR_PARAMS = {
    # k: logistic slope confidence->probability (WTA flatter = more variance)
    # min_edge: minimum model edge over vig-free book probability
    # weight_shift: tour-specific reweighting per docs/SYSTEM.md section 5
    "atp": {"k": 0.055, "min_edge": 0.05, "weight_shift": {}},
    "wta": {"k": 0.050, "min_edge": 0.06,
            "weight_shift": {"serve_return": -0.05, "recent_form": +0.05}},
}

MIN_ODDS, MAX_ODDS = 1.50, 3.50
CONF_FAV_MIN, CONF_DOG_MAX = 62.0, 38.0   # back A above 62, back B below 38
KELLY_FRACTION = 0.25
STAKE_CAP = 0.02                          # max 2% of bankroll on one bet


@dataclass
class Match:
    tour: str                     # "atp" | "wta"
    player_a: str
    player_b: str
    odds_a: float                 # decimal odds currently offered
    odds_b: float
    factors: dict                 # factor name -> score in [-10, +10], A's perspective
    best_of_five: bool = False    # ATP Slams: boosts fatigue weight
    vetoes: list = field(default_factory=list)  # any entry triggers NO BET
    bankroll: float = 1000.0


def weights_for(match: Match) -> dict:
    w = dict(BASE_WEIGHTS)
    for k, dv in TOUR_PARAMS[match.tour]["weight_shift"].items():
        w[k] += dv
    if match.tour == "atp" and match.best_of_five:
        # Bo5: fatigue matters 1.5x, funded from H2H
        w["fatigue"] += 0.05
        w["h2h"] -= 0.05
    assert abs(sum(w.values()) - 1.0) < 1e-9
    return w


def confidence(match: Match) -> float:
    w = weights_for(match)
    missing = [f for f in w if f not in match.factors]
    if missing:
        raise ValueError(f"missing factor scores: {missing} (no bet without full data)")
    raw = sum(w[f] * max(-10.0, min(10.0, match.factors[f])) for f in w)
    return max(0.0, min(100.0, 50.0 + 5.0 * raw))


def model_prob(conf: float, tour: str) -> float:
    k = TOUR_PARAMS[tour]["k"]
    return 1.0 / (1.0 + math.exp(-k * (conf - 50.0)))


def vig_free_probs(odds_a: float, odds_b: float) -> tuple:
    pa, pb = 1.0 / odds_a, 1.0 / odds_b
    total = pa + pb
    return pa / total, pb / total


def kelly_stake(prob: float, odds: float, bankroll: float) -> float:
    f_star = (prob * odds - 1.0) / (odds - 1.0)
    if f_star <= 0:
        return 0.0
    return bankroll * min(KELLY_FRACTION * f_star, STAKE_CAP)


def evaluate(match: Match) -> dict:
    conf = confidence(match)
    p_a = model_prob(conf, match.tour)
    book_a, book_b = vig_free_probs(match.odds_a, match.odds_b)
    min_edge = TOUR_PARAMS[match.tour]["min_edge"]

    # pick the side the model favors relative to the market
    if p_a - book_a >= (1 - p_a) - book_b:
        side, prob, book, odds, conf_ok = (
            match.player_a, p_a, book_a, match.odds_a, conf >= CONF_FAV_MIN)
    else:
        side, prob, book, odds, conf_ok = (
            match.player_b, 1 - p_a, book_b, match.odds_b, conf <= CONF_DOG_MAX)

    edge = prob - book
    stake = kelly_stake(prob, odds, match.bankroll)
    flat = match.bankroll * 0.01

    reasons = []
    if match.vetoes:
        reasons.append(f"HARD VETO: {'; '.join(match.vetoes)}")
    if edge < min_edge:
        reasons.append(f"edge {edge:+.1%} < required {min_edge:.0%}")
    if not (MIN_ODDS <= odds <= MAX_ODDS):
        reasons.append(f"odds {odds:.2f} outside [{MIN_ODDS}, {MAX_ODDS}]")
    if not conf_ok:
        reasons.append(f"confidence {conf:.1f} inside no-bet band "
                       f"({CONF_DOG_MAX:.0f}-{CONF_FAV_MIN:.0f})")
    if stake <= 0:
        reasons.append("non-positive Kelly stake")

    return {
        "matchup": f"{match.player_a} vs {match.player_b} ({match.tour.upper()})",
        "confidence": round(conf, 1),
        "model_prob": round(prob, 4),
        "selection": side,
        "odds": odds,
        "vig_free_book_prob": round(book, 4),
        "edge": round(edge, 4),
        "decision": "BET" if not reasons else "SKIP",
        "skip_reasons": reasons,
        "stake_kelly25": round(stake, 2) if not reasons else 0.0,
        "stake_flat_1pct": round(flat, 2) if not reasons else 0.0,
    }


def print_report(r: dict) -> None:
    print(f"\n{r['matchup']}")
    print(f"  confidence (A):        {r['confidence']}/100")
    print(f"  selection:             {r['selection']} @ {r['odds']:.2f}")
    print(f"  model probability:     {r['model_prob']:.1%}")
    print(f"  vig-free book prob:    {r['vig_free_book_prob']:.1%}")
    print(f"  edge:                  {r['edge']:+.1%}")
    print(f"  decision:              {r['decision']}")
    if r["skip_reasons"]:
        for reason in r["skip_reasons"]:
            print(f"    - {reason}")
    else:
        print(f"  stake (25% Kelly, 2% cap): {r['stake_kelly25']:.2f}")
        print(f"  stake (flat 1%):           {r['stake_flat_1pct']:.2f}")


EXAMPLE = {
    "tour": "atp",
    "player_a": "Big Server (hard-court specialist, fresh)",
    "player_b": "Grinder (played 3 three-setters this week)",
    "odds_a": 2.10,
    "odds_b": 1.78,
    "factors": {
        "surface_elo": 2.5,    # A +100 surface-blended Elo -> 100/40
        "recent_form": 3.0,    # A's dominance ratio trending up
        "serve_return": 4.0,   # A's serve vs B's weak return on fast hard
        "fatigue": 6.0,        # B heavily loaded this week
        "injury": 0.0,         # both clean
        "conditions": 2.0,     # fast indoor favors A
        "motivation": 0.0,     # neutral
        "h2h": -2.0,           # B leads 2-1, but 2019 clay matches
    },
    "vetoes": [],
    "bankroll": 1000.0,
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--example", action="store_true", help="run the worked example")
    g.add_argument("--match", metavar="JSON", help="path to a match factor sheet")
    args = ap.parse_args()

    data = EXAMPLE if args.example else json.load(open(args.match))
    match = Match(**data)
    if match.tour not in TOUR_PARAMS:
        sys.exit(f"tour must be one of {list(TOUR_PARAMS)}")
    print_report(evaluate(match))


if __name__ == "__main__":
    main()
