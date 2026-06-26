#!/usr/bin/env python3
"""
Build Elo ratings for ATP/WTA players from match history and predict
head-to-head win probability.

No draws in tennis, so this is much simpler than the football model: a
single win-probability number per match, no Poisson/Dixon-Coles needed.

Elo update uses the FiveThirtyEight tennis K-factor, which shrinks as a
player accumulates matches (new players' ratings move fast, established
players' ratings move slowly):
    K = 250 / (matches_played + 5) ** 0.4

Two ratings are tracked per player - "overall" (all surfaces) and
"surface" (Hard/Clay/Grass/Carpet) - and blended at prediction time,
weighted toward the surface rating once a player has enough matches on
it. This matters a lot in tennis (grass/clay specialists), unlike
football where a single rating is enough.

Usage:
  python3 elo_predict.py --tour atp "Carlos Alcaraz" "Novak Djokovic" --surface Grass
"""
import argparse
import csv
import datetime
import math
import os
import unicodedata

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
INITIAL_ELO = 1500.0
SURFACE_BLEND_SCALE = 20.0  # matches needed on a surface before it dominates the blend
MAX_SURFACE_WEIGHT = 0.7
# Prediction calibration. Textbook Elo uses 1.0; backtest.py over 45k+ matches
# of historical results showed raw Elo was OVERconfident in its favourites
# (said ~85%, won ~78%). 0.8 pulls probabilities toward 50% and made the
# reliability curve track the diagonal almost perfectly on a 2023+ hold-out.
# Re-derive with `python3 backtest.py --tour atp --tune` if the model changes.
PREDICTION_SCALE = 0.8


def win_prob(rating_a, rating_b):
    """Calibrated probability that A beats B given their (blended) Elo ratings."""
    return 1.0 / (1.0 + 10 ** (-(rating_a - rating_b) / 400.0 * PREDICTION_SCALE))


def parse_date(s):
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date format: {s!r}")


def load_match_rows(tour):
    path = os.path.join(DATA_DIR, f"{tour}_matches.csv")
    rows = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("winner_name") or not row.get("loser_name"):
                continue
            try:
                date = parse_date(row["tourney_date"])
            except ValueError:
                continue
            rows.append({
                "date": date,
                "surface": row["surface"].strip() or None,
                "winner": row["winner_name"].strip(),
                "loser": row["loser_name"].strip(),
            })
    rows.sort(key=lambda r: r["date"])
    return rows


def k_factor(matches_played):
    return 250.0 / (matches_played + 5) ** 0.4


def build_elo(tour):
    """Returns {player: {'overall': elo, 'overall_n': int,
    'surface': {surf: elo}, 'surface_n': {surf: int}, 'last_date': date}}."""
    ratings = {}

    def player(name):
        if name not in ratings:
            ratings[name] = {
                "overall": INITIAL_ELO, "overall_n": 0,
                "surface": {}, "surface_n": {},
                "last_date": None,
            }
        return ratings[name]

    for m in load_match_rows(tour):
        w, l, surface, date = m["winner"], m["loser"], m["surface"], m["date"]
        pw, pl = player(w), player(l)

        expected_w = 1.0 / (1.0 + 10 ** ((pl["overall"] - pw["overall"]) / 400.0))
        k_w, k_l = k_factor(pw["overall_n"]), k_factor(pl["overall_n"])
        pw["overall"] += k_w * (1 - expected_w)
        pl["overall"] += k_l * (0 - (1 - expected_w))
        pw["overall_n"] += 1
        pl["overall_n"] += 1

        if surface:
            sw = pw["surface"].setdefault(surface, INITIAL_ELO)
            sl = pl["surface"].setdefault(surface, INITIAL_ELO)
            nw = pw["surface_n"].setdefault(surface, 0)
            nl = pl["surface_n"].setdefault(surface, 0)
            expected_sw = 1.0 / (1.0 + 10 ** ((sl - sw) / 400.0))
            ks_w, ks_l = k_factor(nw), k_factor(nl)
            pw["surface"][surface] = sw + ks_w * (1 - expected_sw)
            pl["surface"][surface] = sl + ks_l * (0 - (1 - expected_sw))
            pw["surface_n"][surface] = nw + 1
            pl["surface_n"][surface] = nl + 1

        pw["last_date"] = date
        pl["last_date"] = date

    return ratings


def blended_rating(player_ratings, surface):
    overall = player_ratings["overall"]
    if not surface or surface not in player_ratings["surface"]:
        return overall
    n = player_ratings["surface_n"].get(surface, 0)
    weight = min(MAX_SURFACE_WEIGHT, n / (n + SURFACE_BLEND_SCALE))
    return weight * player_ratings["surface"][surface] + (1 - weight) * overall


def normalize_name(name):
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower().replace("-", " ").replace(".", "")
    return " ".join(name.split())


def build_name_index(ratings):
    index = {}
    for name in ratings:
        index.setdefault(normalize_name(name), []).append(name)
    return index


def resolve_player(query, ratings, index):
    norm = normalize_name(query)
    if norm in index:
        candidates = index[norm]
        if len(candidates) == 1:
            return candidates[0]
        # ambiguous exact match (shouldn't really happen): prefer most-recently-active
        return max(candidates, key=lambda n: ratings[n]["last_date"] or datetime.date.min)

    # fallback: match by last name (last token), require uniqueness
    last = norm.split()[-1] if norm.split() else norm
    by_last = [n for n, ns in index.items() if ns and n.split()[-1] == last]
    flat = [n for key in by_last for n in index[key]]
    if len(flat) == 1:
        return flat[0]
    raise ValueError(f"could not resolve player name {query!r} ({len(flat)} candidates by last name)")


def predict(tour, player_a, player_b, surface=None, ratings=None):
    if ratings is None:
        ratings = build_elo(tour)
    index = build_name_index(ratings)
    name_a = resolve_player(player_a, ratings, index)
    name_b = resolve_player(player_b, ratings, index)
    ra, rb = blended_rating(ratings[name_a], surface), blended_rating(ratings[name_b], surface)
    p_a = win_prob(ra, rb)
    return {
        "player_a": name_a, "player_b": name_b,
        "elo_a": ra, "elo_b": rb,
        "surface": surface,
        "p_a": p_a, "p_b": 1 - p_a,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tour", choices=["atp", "wta"], required=True)
    parser.add_argument("player_a")
    parser.add_argument("player_b")
    parser.add_argument("--surface", default=None, choices=["Hard", "Clay", "Grass", "Carpet"])
    args = parser.parse_args()

    result = predict(args.tour, args.player_a, args.player_b, surface=args.surface)
    print(f"{result['player_a']} (Elo {result['elo_a']:.0f}) vs "
          f"{result['player_b']} (Elo {result['elo_b']:.0f}) "
          f"[surface: {result['surface'] or 'blended overall'}]")
    print(f"  P({result['player_a']} wins) = {result['p_a']*100:.1f}%")
    print(f"  P({result['player_b']} wins) = {result['p_b']*100:.1f}%")


if __name__ == "__main__":
    main()
