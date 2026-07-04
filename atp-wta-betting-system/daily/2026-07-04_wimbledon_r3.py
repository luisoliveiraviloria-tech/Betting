#!/usr/bin/env python3
"""Daily run - Saturday 2026-07-04, Wimbledon R3 (grass).

Odds: FanDuel/consensus US books ~12:00 BST. Elo: Tennis Abstract 2026-06-29
(70% grass / 30% overall blend, factor = clip(diff/40, -10, 10)).
Auto-skipped before scoring (odds outside 1.50-3.50, or already in play):
  Zverev (1.06) v Giron, Fritz (1.13) v Sonego, Swiatek (1.29) v Eala (3.75),
  Rybakina (1.22) v Mertens, Lehecka (1.31) v Munar (3.50: dog side has
  negative edge, favorite below floor), Samsonova v Bouzkova (in play 11:00).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "model"))
from value_model import Match, evaluate, print_report  # noqa: E402

BANKROLL = 1000.0

MATCHES = [
    # --- ATP ---
    Match(
        tour="atp", player_a="Dimitrov", player_b="Berrettini",
        odds_a=2.14, odds_b=1.75, bankroll=BANKROLL, best_of_five=True,
        factors={
            "surface_elo": -2.0,   # blended: Dimitrov 1686 vs Berrettini 1767 (-80)
            "recent_form": -1.0,   # both beat seeds in 4 sets; Berrettini RG QF + Fils scalp
            "serve_return": -2.0,  # Berrettini serve+FH elite on grass; Dimitrov return average
            "fatigue": 0.0,        # two 4-setters each, comparable rest
            "injury": 0.0,         # both injury-prone historically, no current flags
            "conditions": 1.0,     # 26mph gusts favor Dimitrov's variety over flat power
            "motivation": 0.0,
            "h2h": 0.0,            # 1-1, stale (2019), never on grass
        }),
    Match(
        tour="atp", player_a="Tiafoe", player_b="Bublik",
        odds_a=1.80, odds_b=2.04, bankroll=BANKROLL, best_of_five=True,
        factors={
            "surface_elo": 0.6,    # blended: Tiafoe 1852 vs Bublik 1828 (+24)
            "recent_form": 2.0,    # Tiafoe won Halle; Bublik R1 scare, easy R2
            "serve_return": 2.0,   # Bublik return among tour's worst; Tiafoe more complete
            "fatigue": 0.0,
            "injury": 0.0,
            "conditions": 1.0,     # wind vs Bublik's touch/high-risk patterns
            "motivation": 1.0,     # Bublik volatile engagement, never past W4R
            "h2h": 0.0,            # no reliable current read
        }),
    Match(
        tour="atp", player_a="Khachanov", player_b="Cobolli",
        odds_a=1.67, odds_b=2.25, bankroll=BANKROLL, best_of_five=True,
        factors={
            "surface_elo": -0.8,   # blended: Khachanov 1756 vs Cobolli 1788 (-33)
            "recent_form": -2.0,   # Cobolli's breakout season (seeded 9 vs 19)
            "serve_return": 2.0,   # Khachanov's serve travels better on grass
            "fatigue": 3.0,        # Cobolli's R2 was a 2-day, 2-TB battle; less rest
            "injury": 0.0,
            "conditions": 0.0,
            "motivation": 1.0,     # 2x Wimbledon QF experience
            "h2h": 0.0,
        }),
    # --- WTA ---
    Match(
        tour="wta", player_a="Navarro", player_b="Kostyuk",
        odds_a=2.15, odds_b=1.71, bankroll=BANKROLL,
        factors={
            "surface_elo": -0.8,   # blended: Navarro 1802 vs Kostyuk 1833 (-32)
            "recent_form": -3.0,   # Kostyuk won Madrid+Rouen, RG SF; Navarro mixed year
            "serve_return": -1.0,  # grass rewards Kostyuk's first-strike over Navarro defense
            "fatigue": 0.0,        # both dropped sets this week; Kostyuk heavier season load
            "injury": 0.0,
            "conditions": 1.0,     # gusts punish Kostyuk's flat aggression more
            "motivation": 0.0,
            "h2h": 6.0,            # Navarro leads 4-0
        }),
    Match(
        tour="wta", player_a="Anisimova", player_b="Keys",
        odds_a=2.43, odds_b=1.59, bankroll=BANKROLL,
        factors={
            "surface_elo": -1.9,   # blended: Anisimova 1812 vs Keys 1886 (-74)
            "recent_form": -1.0,   # Anisimova needed deciding-set TB vs Kenin; Keys cleaner
            "serve_return": -1.0,  # Keys serve bigger; Anisimova the better returner (offsets)
            "fatigue": -2.0,       # Anisimova's 3-set, 10-pt-TB R2
            "injury": 0.0,
            "conditions": 1.0,     # wind degrades Keys' high toss / flat power slightly more
            "motivation": 0.0,     # all-American July 4 on Centre, both max
            "h2h": 0.0,
        }),
]

if __name__ == "__main__":
    bets = 0
    for m in MATCHES:
        r = evaluate(m)
        print_report(r)
        bets += r["decision"] == "BET"
    print(f"\n=== {bets} BET / {len(MATCHES) - bets} SKIP among scored candidates ===")
