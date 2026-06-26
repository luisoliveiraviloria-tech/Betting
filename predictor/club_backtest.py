#!/usr/bin/env python3
"""
Backtest OUR engine on club leagues. The model is national-team only, but its
maths (Elo difference -> goal supremacy -> Poisson scoreline -> Dixon-Coles
1X2 probabilities) is sport/league-agnostic, so we import it verbatim from
elo_predict.py and feed it point-in-time CLUB Elo (clubelo.com) instead of
national Elo. Results and closing odds come from football-data.co.uk.

It answers, per league, the same two questions we asked for tennis:
  1. Is the model calibrated? (multiclass log-loss / Brier / reliability)
  2. Would its value bets have made money vs real closing odds?

No lookahead: each match uses each club's Elo as of the match date and only
results strictly before it are implied by clubelo's own history.

Usage:
  python3 club_backtest.py                       # all leagues, our constants
  python3 club_backtest.py --league E0
  python3 club_backtest.py --tune                # refit home-adv & slope for clubs
"""
import argparse
import csv
import datetime
import json
import math
import os

from elo_predict import (
    match_probabilities,
    ELO_POINTS_PER_GOAL,
    TOTAL_GOALS_BASELINE,
    TOTAL_GOALS_PER_ELO_GAP,
    RHO,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "club")
ELO_DIR = os.path.join(DATA_DIR, "elo")
LEAGUE_NAME = {"E0": "Premier League", "SP1": "La Liga", "I1": "Serie A",
               "D1": "Bundesliga", "F1": "Ligue 1"}
DEFAULT_HOME_ADV = 65.0  # Elo points; clubelo-scale home advantage (tunable)


def parse_date(s):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(s)


def load_name_map():
    with open(os.path.join(DATA_DIR, "name_map.json"), encoding="utf-8") as f:
        return json.load(f)


def load_elo_histories():
    """clubelo_name -> sorted list of (from_date, to_date, elo)."""
    hist = {}
    for fn in os.listdir(ELO_DIR):
        if not fn.endswith(".csv"):
            continue
        intervals = []
        with open(os.path.join(ELO_DIR, fn), encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    frm = datetime.date.fromisoformat(r["From"])
                    to = datetime.date.fromisoformat(r["To"])
                    elo = float(r["Elo"])
                except (ValueError, KeyError):
                    continue
                intervals.append((frm, to, elo))
        intervals.sort()
        if intervals:
            hist[fn[:-4]] = intervals
    return hist


def elo_at(intervals, date):
    """Elo for a club on `date` (last interval whose range covers it, else
    the nearest prior interval). None if no prior data (avoids lookahead)."""
    chosen = None
    for frm, to, elo in intervals:
        if frm > date:
            break
        chosen = elo
        if frm <= date <= to:
            return elo
    return chosen  # most recent rating before the match


def safe_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def devig_3way(h, d, a):
    rh, rd, ra = 1 / h, 1 / d, 1 / a
    t = rh + rd + ra
    return rh / t, rd / t, ra / t


def model_probs(home_elo, away_elo, home_adv, slope, base, per_gap, rho):
    team_diff = home_elo - away_elo
    rating_diff = team_diff + home_adv
    supremacy = rating_diff / slope
    total = base + per_gap * abs(team_diff)
    lh = max((total + supremacy) / 2, 0.05)
    la = max((total - supremacy) / 2, 0.05)
    return match_probabilities(lh, la, rho=rho)


_RAW_CACHE = {}


def build_raw_records(league=None):
    """One expensive pass: join each match to point-in-time club Elo. Cached so
    parameter sweeps (tune) don't reload 144 Elo files per grid point."""
    if league in _RAW_CACHE:
        return _RAW_CACHE[league]
    name_map = load_name_map()
    hist = load_elo_histories()

    def club_key(fd_name):
        cl = name_map.get(fd_name, fd_name)
        return cl.replace(" ", "_").replace("/", "_")

    out = []
    skipped = 0
    with open(os.path.join(DATA_DIR, "results.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if league and r["Div"] != league:
                continue
            try:
                date = parse_date(r["Date"])
            except ValueError:
                continue
            hk, ak = club_key(r["HomeTeam"]), club_key(r["AwayTeam"])
            if hk not in hist or ak not in hist:
                skipped += 1
                continue
            he, ae = elo_at(hist[hk], date), elo_at(hist[ak], date)
            if he is None or ae is None:
                skipped += 1
                continue
            out.append({
                "div": r["Div"], "date": date, "ftr": r["FTR"],
                "he": he, "ae": ae,
                "avg": (safe_float(r["AvgH"]), safe_float(r["AvgD"]), safe_float(r["AvgA"])),
                "psc": (safe_float(r["PSCH"]), safe_float(r["PSCD"]), safe_float(r["PSCA"])),
            })
    _RAW_CACHE[league] = (out, skipped)
    return out, skipped


def attach_probs(raw, home_adv, slope, base, per_gap, rho):
    scored = []
    for r in raw:
        ph, pd, pa = model_probs(r["he"], r["ae"], home_adv, slope, base, per_gap, rho)
        scored.append({**r, "p": {"H": ph, "D": pd, "A": pa}})
    return scored


def build_records(league=None, home_adv=DEFAULT_HOME_ADV, slope=ELO_POINTS_PER_GOAL,
                  base=TOTAL_GOALS_BASELINE, per_gap=TOTAL_GOALS_PER_ELO_GAP, rho=RHO):
    raw, skipped = build_raw_records(league)
    return attach_probs(raw, home_adv, slope, base, per_gap, rho), skipped


# ---- metrics -------------------------------------------------------------

def calib(records, source="model"):
    n = ll = brier = acc = 0
    for r in records:
        if source == "model":
            p = r["p"]
        else:
            odds = r["avg"]
            if None in odds:
                continue
            ph, pd, pa = devig_3way(*odds)
            p = {"H": ph, "D": pd, "A": pa}
        n += 1
        pa_actual = max(p[r["ftr"]], 1e-12)
        ll += -math.log(pa_actual)
        brier += sum((p[k] - (1 if k == r["ftr"] else 0)) ** 2 for k in "HDA")
        if max(p, key=p.get) == r["ftr"]:
            acc += 1
    if not n:
        return {}
    return {"n": n, "log_loss": ll / n, "brier": brier / n, "acc": acc / n}


def reliability(records, bins=10):
    buckets = [[] for _ in range(bins)]
    for r in records:
        k = max(r["p"], key=r["p"].get)
        pfav = r["p"][k]
        idx = min(bins - 1, int(pfav * bins))
        buckets[idx].append((pfav, 1 if r["ftr"] == k else 0))
    rows = []
    for b in buckets:
        if b:
            rows.append((sum(x[0] for x in b) / len(b),
                         sum(x[1] for x in b) / len(b), len(b)))
    return rows


def roi(records, price="psc", edge=0.0, min_odds=1.3, max_odds=8.0):
    staked = profit = bets = wins = 0
    for r in records:
        odds = r[price] if price == "psc" else r["avg"]
        if None in odds:
            continue
        mh, md, ma = devig_3way(*odds)
        mkt = {"H": mh, "D": md, "A": ma}
        for k, o in zip("HDA", odds):
            if min_odds <= o <= max_odds and (r["p"][k] - mkt[k]) >= edge:
                staked += 1
                bets += 1
                if r["ftr"] == k:
                    profit += o - 1
                    wins += 1
                else:
                    profit -= 1
    return {"bets": bets, "roi": (profit / staked) if staked else 0.0,
            "hit": (wins / bets) if bets else 0.0, "profit": profit}


def _logloss_fast(scored):
    n = ll = 0
    for r in scored:
        ll += -math.log(max(r["p"][r["ftr"]], 1e-12))
        n += 1
    return ll / n if n else float("inf")


def tune():
    """Grid over club home-advantage, Elo-points-per-goal slope and total-goals
    baseline (club football outscores internationals, so the international 2.09
    baseline is likely too low), minimising log-loss on a 2015-2022 training
    split. Uses cached raw Elo pairs so the whole sweep is in-memory."""
    raw, _ = build_raw_records()
    train = [r for r in raw if r["date"].year <= 2022]
    best = (None, float("inf"))
    for ha in range(40, 101, 10):
        for slope in range(150, 261, 20):
            for base in (2.4, 2.6, 2.8, 3.0):
                scored = attach_probs(train, ha, slope, base,
                                      TOTAL_GOALS_PER_ELO_GAP, RHO)
                ll = _logloss_fast(scored)
                if ll < best[1]:
                    best = ((ha, slope, base), ll)
    return best


def report(records, tag):
    cm = calib(records, "model")
    mm = calib(records, "market")
    print(f"--- {tag} ---")
    print(f"  model : log-loss {cm['log_loss']:.4f}  Brier {cm['brier']:.4f}  "
          f"acc {cm['acc']*100:.1f}%  (n={cm['n']})")
    if mm:
        print(f"  market: log-loss {mm['log_loss']:.4f}  Brier {mm['brier']:.4f}  "
              f"acc {mm['acc']*100:.1f}%  (n={mm['n']})")
    print("  reliability (favourite):  pred%  emp%   n")
    for pred, emp, n in reliability(records):
        flag = "  <-- overconf" if emp < pred - 0.04 else ""
        print(f"                            {pred*100:5.1f} {emp*100:5.1f} {n:5d}{flag}")
    for price in ("psc", "avg"):
        lbl = "Pinnacle-close" if price == "psc" else "Market-avg"
        for edge in (0.0, 0.03, 0.05):
            rr = roi(records, price=price, edge=edge)
            print(f"  ROI {lbl:14s} edge>={edge*100:3.0f}pp: {rr['bets']:5d} bets  "
                  f"{rr['roi']*100:+6.1f}%  hit {rr['hit']*100:4.1f}%  profit {rr['profit']:+8.1f}u")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", default=None, help="E0|SP1|I1|D1|F1 (default: all)")
    ap.add_argument("--home-adv", type=float, default=DEFAULT_HOME_ADV)
    ap.add_argument("--slope", type=float, default=ELO_POINTS_PER_GOAL)
    ap.add_argument("--base", type=float, default=TOTAL_GOALS_BASELINE)
    ap.add_argument("--tune", action="store_true")
    ap.add_argument("--test-only", action="store_true",
                    help="score only 2023+ (hold-out) matches")
    args = ap.parse_args()

    base = args.base
    if args.tune:
        (ha, slope, base), ll = tune()
        print(f"Tuned on <=2022: home_adv={ha}, slope={slope}, total-base={base} "
              f"(train log-loss {ll:.4f})\n")
        args.home_adv, args.slope = ha, slope

    records, skipped = build_records(args.league, home_adv=args.home_adv,
                                     slope=args.slope, base=base)
    if args.test_only or args.tune:
        records = [r for r in records if r["date"].year >= 2023]
    label = "2023+ hold-out" if (args.test_only or args.tune) else "all seasons"
    print(f"== Club backtest [{label}] | {len(records)} matches scored, {skipped} skipped "
          f"| home_adv={args.home_adv} slope={args.slope} base={base} ==\n")

    leagues = [args.league] if args.league else list(LEAGUE_NAME)
    for lg in leagues:
        recs = [r for r in records if r["div"] == lg]
        if recs:
            report(recs, f"{LEAGUE_NAME.get(lg, lg)} ({lg})")
            print()
    if not args.league:
        report(records, "ALL LEAGUES")


if __name__ == "__main__":
    main()
