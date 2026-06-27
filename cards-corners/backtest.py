#!/usr/bin/env python3
"""
No-lookahead calibration backtest for the corners and cards models.

We have NO historical cards/corners betting odds (football-data doesn't
publish them), so this cannot measure ROI. What it CAN measure - and what has
to be true before any betting could ever work - is whether the models are
calibrated and INFORMATIVE: when the model says "62% chance of over 9.5
corners", do ~62% of those go over, and does the model beat a naive baseline
that knows only the league average?

Metrics per market/line (binary over/under):
  log-loss, Brier  - lower is better
  reliability      - predicted% vs actual% by bucket
  vs BASELINE      - same metrics for a Poisson at the running league-average
                     lambda (no team/referee info). Beating it = real signal.

For cards in the English leagues (the only ones with referee data) we also run
the model WITH vs WITHOUT the referee factor - the headline test of whether
the referee is worth conditioning on.

Usage:
  python3 backtest.py                 # all markets, all leagues
  python3 backtest.py --league E0
"""
import argparse
import math

from model import (load_matches, StatsEngine, p_over,
                    SEED_CORNERS, SEED_CARDS)

CORNER_LINES = [9.5, 10.5]
CARD_LINES = [3.5, 4.5]
BURN_IN = 2000  # matches to accrue stats before scoring (global, chronological)


def collect(league=None):
    """One chronological pass -> per-match predictions and outcomes."""
    rows = load_matches()
    eng = StatsEngine()
    recs = []
    for i, r in enumerate(rows):
        # league baseline lambda (no team/ref info) for the baseline model
        m = eng.lg_means(r["div"])
        base_corner_lam = m["home_c"] + m["away_c"]
        base_card_lam = m["cards"]
        rec = None
        if i >= BURN_IN and (league is None or r["div"] == league):
            rec = {
                "div": r["div"], "ref": r["ref"],
                "corner_total": r["hc"] + r["ac"],
                "card_total": r["cards"],
                "lam_corner": eng.predict_corners(r["div"], r["home"], r["away"]),
                "lam_corner_base": base_corner_lam,
                "lam_card": eng.predict_cards(r["div"], r["home"], r["away"], r["ref"], use_ref=True),
                "lam_card_noref": eng.predict_cards(r["div"], r["home"], r["away"], r["ref"], use_ref=False),
                "lam_card_base": base_card_lam,
            }
            recs.append(rec)
        eng.update(r)
    return recs


def metrics(recs, lam_key, total_key, line):
    """log-loss, Brier, n for P(over line) from lam_key vs actual total_key."""
    n = ll = brier = over = 0
    for r in recs:
        p = min(max(p_over(r[lam_key], line), 1e-9), 1 - 1e-9)
        y = 1 if r[total_key] > line else 0
        ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        brier += (p - y) ** 2
        over += y
        n += 1
    if not n:
        return None
    return {"n": n, "log_loss": ll / n, "brier": brier / n, "base_rate": over / n}


def reliability(recs, lam_key, total_key, line, bins=10):
    buckets = [[] for _ in range(bins)]
    for r in recs:
        p = p_over(r[lam_key], line)
        idx = min(bins - 1, int(p * bins))
        buckets[idx].append((p, 1 if r[total_key] > line else 0))
    out = []
    for b in buckets:
        if len(b) >= 20:
            out.append((sum(x[0] for x in b) / len(b),
                        sum(x[1] for x in b) / len(b), len(b)))
    return out


def mae(recs, lam_key, total_key):
    return sum(abs(r[lam_key] - r[total_key]) for r in recs) / len(recs)


def show(recs, market, lam_key, base_key, total_key, lines):
    print(f"\n### {market}  (n={len(recs)}, MAE {mae(recs, lam_key, total_key):.2f} "
          f"vs baseline MAE {mae(recs, base_key, total_key):.2f})")
    for line in lines:
        mo = metrics(recs, lam_key, total_key, line)
        mb = metrics(recs, base_key, total_key, line)
        print(f"  O/U {line}: model  log-loss {mo['log_loss']:.4f}  Brier {mo['brier']:.4f}"
              f"   | baseline log-loss {mb['log_loss']:.4f}  Brier {mb['brier']:.4f}"
              f"   (over rate {mo['base_rate']*100:.0f}%)")
        rel = reliability(recs, lam_key, total_key, line)
        cells = "  ".join(f"{p*100:.0f}->{e*100:.0f}" for p, e, _ in rel)
        print(f"           reliability pred->actual: {cells}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", default=None)
    args = ap.parse_args()

    recs = collect(args.league)
    tag = args.league or "ALL LEAGUES"
    print(f"==== Cards/Corners calibration backtest [{tag}] | {len(recs)} scored matches ====")

    show(recs, "CORNERS (total)", "lam_corner", "lam_corner_base", "corner_total", CORNER_LINES)
    show(recs, "CARDS (total)", "lam_card", "lam_card_base", "card_total", CARD_LINES)

    # Referee ablation on English leagues only (the rows that have a referee)
    eng_recs = [r for r in recs if r["ref"]]
    if eng_recs:
        print(f"\n==== REFEREE ABLATION (English leagues, n={len(eng_recs)}) ====")
        for line in CARD_LINES:
            mwith = metrics(eng_recs, "lam_card", "card_total", line)
            mwout = metrics(eng_recs, "lam_card_noref", "card_total", line)
            mbase = metrics(eng_recs, "lam_card_base", "card_total", line)
            print(f"  Cards O/U {line}:  with-ref log-loss {mwith['log_loss']:.4f}"
                  f"   no-ref {mwout['log_loss']:.4f}"
                  f"   league-baseline {mbase['log_loss']:.4f}")
        print(f"  MAE: with-ref {mae(eng_recs,'lam_card','card_total'):.3f}"
              f"   no-ref {mae(eng_recs,'lam_card_noref','card_total'):.3f}"
              f"   baseline {mae(eng_recs,'lam_card_base','card_total'):.3f}")


if __name__ == "__main__":
    main()
