#!/usr/bin/env python3
"""
Test whether extra features (recent form, rest, fatigue) add predictive power
on TOP of surface Elo - measured honestly against the same historical odds
hold-out used by backtest.py.

Method: one chronological pass builds, per match, a pre-match feature vector
(no lookahead) oriented to a coin-flip "player1 vs player2" so the label isn't
leaked by winner/loser ordering. A pure-Python logistic regression then learns
each feature's weight on a validation period; we report log-loss and value-bet
ROI on a later test period. Nested models (Elo only -> +form -> +rest+fatigue)
show whether each feature actually earns its place.

Features (all player1 - player2 differentials, known before the match):
  elo_logit : ln-odds of the calibrated surface-Elo win prob
  form      : win rate over last FORM_N matches
  rest      : days since last match (capped), a layoff/rust proxy
  fatigue   : matches played in the last FATIGUE_DAYS days

Usage:
  python3 model_features.py --tour atp
"""
import argparse
import math
import random

from backtest import load_rows, EloEngine, devig_two_way, split

FORM_N = 20
FATIGUE_DAYS = 14
REST_CAP = 60  # days; beyond this "rest" stops meaning anything useful


def build_feature_rows(tour, scale=0.8, burn_in_year=2016):
    rows = load_rows(tour)
    eng = EloEngine(scale=scale)
    state = {}  # name -> {last_date, results: [win bools], dates: [date]}

    def st(name):
        if name not in state:
            state[name] = {"last": None, "results": [], "dates": []}
        return state[name]

    def form(name):
        r = st(name)["results"]
        return sum(r[-FORM_N:]) / len(r[-FORM_N:]) if r else 0.5

    def rest(name, date):
        last = st(name)["last"]
        if last is None:
            return REST_CAP
        return min((date - last).days, REST_CAP)

    def fatigue(name, date):
        return sum(1 for d in st(name)["dates"] if 0 < (date - d).days <= FATIGUE_DAYS)

    out = []
    for m in rows:
        w, l, surf, date = m["winner"], m["loser"], m["surface"], m["date"]
        # coin-flip orientation (deterministic) to avoid winner/loser leakage
        flip = (hash((w, l, date.toordinal())) & 1) == 0
        p1, p2 = (w, l) if not flip else (l, w)
        label = 1 if p1 == w else 0

        p_elo_p1 = eng.prob(p1, p2, surf)
        p_elo_p1 = min(max(p_elo_p1, 1e-6), 1 - 1e-6)
        feat = {
            "elo_logit": math.log(p_elo_p1 / (1 - p_elo_p1)),
            "form": form(p1) - form(p2),
            "rest": rest(p1, date) - rest(p2, date),
            "fatigue": fatigue(p1, date) - fatigue(p2, date),
        }

        if date.year > burn_in_year and m["comment"] in ("Completed", "Retired", ""):
            # odds oriented to player1
            ow, ol = (m["avgw"], m["avgl"])
            psw, psl = (m["psw"], m["psl"])
            o1, o2 = (ow, ol) if p1 == w else (ol, ow)
            ps1, ps2 = (psw, psl) if p1 == w else (psl, psw)
            out.append({"date": date, "x": feat, "y": label,
                        "o1": o1, "o2": o2, "ps1": ps1, "ps2": ps2})

        # update Elo + rolling state AFTER recording features
        eng.update(w, l, surf)
        for name, won in ((w, True), (l, False)):
            s = st(name)
            s["results"].append(won)
            s["dates"].append(date)
            s["last"] = date
    return out


# ---- pure-python logistic regression -------------------------------------

def standardize(rows, feats):
    mean, std = {}, {}
    for f in feats:
        vals = [r["x"][f] for r in rows]
        mu = sum(vals) / len(vals)
        var = sum((v - mu) ** 2 for v in vals) / len(vals)
        mean[f], std[f] = mu, math.sqrt(var) or 1.0
    return mean, std


def train_logistic(rows, feats, epochs=300, lr=0.3, l2=1e-4):
    mean, std = standardize(rows, feats)
    w = {f: 0.0 for f in feats}
    b = 0.0
    n = len(rows)
    for _ in range(epochs):
        gw = {f: 0.0 for f in feats}
        gb = 0.0
        for r in rows:
            z = b + sum(w[f] * (r["x"][f] - mean[f]) / std[f] for f in feats)
            p = 1.0 / (1.0 + math.exp(-z))
            err = p - r["y"]
            for f in feats:
                gw[f] += err * (r["x"][f] - mean[f]) / std[f]
            gb += err
        for f in feats:
            w[f] = w[f] - lr * (gw[f] / n + l2 * w[f])
        b -= lr * gb / n
    return {"w": w, "b": b, "mean": mean, "std": std, "feats": feats}


def predict_p1(model, x):
    z = model["b"] + sum(model["w"][f] * (x[f] - model["mean"][f]) / model["std"][f]
                         for f in model["feats"])
    return 1.0 / (1.0 + math.exp(-z))


def log_loss(model, rows):
    s = 0.0
    for r in rows:
        p = min(max(predict_p1(model, r["x"]), 1e-12), 1 - 1e-12)
        s += -(r["y"] * math.log(p) + (1 - r["y"]) * math.log(1 - p))
    return s / len(rows)


def roi(model, rows, price="o", edge=0.0, min_odds=1.3, max_odds=5.0):
    a, bcol = f"{price}1", f"{price}2"
    staked = profit = bets = wins = 0
    for r in rows:
        o1, o2 = r[a], r[bcol]
        if not o1 or not o2:
            continue
        m1, m2 = devig_two_way(o1, o2)
        p1 = predict_p1(model, r["x"])
        p2 = 1 - p1
        for (p, mkt, odds, won) in ((p1, m1, o1, r["y"]), (p2, m2, o2, 1 - r["y"])):
            if min_odds <= odds <= max_odds and (p - mkt) >= edge:
                staked += 1
                bets += 1
                profit += (odds - 1) if won else -1
                wins += won
    return {"bets": bets, "roi": (profit / staked) if staked else 0.0,
            "hit": (wins / bets) if bets else 0.0, "profit": profit}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tour", choices=["atp", "wta"], default="atp")
    args = ap.parse_args()

    rows = build_feature_rows(args.tour)
    val, test = split(rows)
    print(f"== {args.tour.upper()} | {len(val)} val (<=2022) / {len(test)} test (2023+) ==\n")

    models = {
        "Elo only          ": ["elo_logit"],
        "Elo + form        ": ["elo_logit", "form"],
        "Elo + form + rest ": ["elo_logit", "form", "rest"],
        "Elo + form+rest+fat": ["elo_logit", "form", "rest", "fatigue"],
    }
    # market benchmark log-loss on test
    mkt_ll = 0.0
    for r in test:
        if r["o1"] and r["o2"]:
            m1, _ = devig_two_way(r["o1"], r["o2"])
            p = m1 if r["y"] == 1 else 1 - m1
            mkt_ll += -math.log(max(p, 1e-12))
    mkt_ll /= sum(1 for r in test if r["o1"] and r["o2"])

    print(f"{'model':22s}  test log-loss   weights")
    for name, feats in models.items():
        mdl = train_logistic(val, feats)
        ll = log_loss(mdl, test)
        wtxt = "  ".join(f"{f}={mdl['w'][f]:+.3f}" for f in feats)
        print(f"{name}  {ll:.4f}        {wtxt}")
    print(f"{'MARKET (Avg odds)':22s}  {mkt_ll:.4f}")

    print("\n--- Value-bet ROI on test (full model) ---")
    full = train_logistic(val, models["Elo + form+rest+fat"])
    for price in ("o", "ps"):
        lbl = "Avg" if price == "o" else "Pinnacle"
        for edge in (0.0, 0.03, 0.05):
            r = roi(full, test, price=price, edge=edge)
            print(f"  {lbl:8s} edge>={edge*100:3.0f}pp: {r['bets']:5d} bets  "
                  f"ROI {r['roi']*100:+6.1f}%  hit {r['hit']*100:4.1f}%  profit {r['profit']:+8.1f}u")


if __name__ == "__main__":
    main()
