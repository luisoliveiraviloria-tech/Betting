#!/usr/bin/env python3
"""
Bounded ROI test for the cards/corners models.

We have no historical cards/corners odds, so a true ROI backtest is impossible.
This is the next best thing: bet the model against a SYNTHETIC bookmaker that
prices each over/under line off the league-average baseline (the naive model)
plus a realistic margin. That is the OPTIMISTIC bound - it assumes the book is
no smarter than the league average. A real book is somewhere between this and
our own model; if it prices as well as we do, our edge is zero.

Reading it:
  - If even this optimistic book beats us -> the idea is dead, stop.
  - If we beat this book comfortably -> promising, but it ONLY confirms we beat
    a naive line. Real bookmaker corners/cards lines are sharper, so treat a
    positive result here as "worth getting real odds to confirm", not "profit".

Usage:
  python3 synth_roi.py
"""
import argparse

from model import p_over
from backtest import collect, CORNER_LINES, CARD_LINES


def book_odds(p_fair, overround):
    """Two-sided over/under prices from a fair prob with proportional margin."""
    raw_over = overround * p_fair
    raw_under = overround * (1 - p_fair)
    return 1.0 / raw_over, 1.0 / raw_under


def roi(recs, lam_model, lam_book, total_key, line, overround, edge=0.0):
    staked = profit = bets = wins = 0
    for r in recs:
        p_model = p_over(r[lam_model], line)
        p_book = min(max(p_over(r[lam_book], line), 1e-6), 1 - 1e-6)
        o_over, o_under = book_odds(p_book, overround)
        y = 1 if r[total_key] > line else 0
        # over side
        if p_model * o_over - 1 >= edge:
            staked += 1; bets += 1
            profit += (o_over - 1) if y else -1; wins += y
        # under side
        if (1 - p_model) * o_under - 1 >= edge:
            staked += 1; bets += 1
            profit += (o_under - 1) if not y else -1; wins += (1 - y)
    return {"bets": bets, "roi": (profit / staked) if staked else 0.0,
            "hit": (wins / bets) if bets else 0.0, "profit": profit}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", default=None)
    ap.add_argument("--overround", type=float, default=1.06,
                    help="synthetic book overround (1.06 = ~5.7% hold)")
    ap.add_argument("--edge", type=float, default=0.02, help="min EV to bet")
    args = ap.parse_args()

    recs = collect(args.league)
    print(f"== Bounded ROI vs baseline-priced book (overround {args.overround}, "
          f"min EV {args.edge*100:.0f}%) | {len(recs)} matches ==")
    print("   [OPTIMISTIC bound: assumes the book is no sharper than the league average]\n")

    print("CORNERS:")
    for line in CORNER_LINES:
        r = roi(recs, "lam_corner", "lam_corner_base", "corner_total", line, args.overround, args.edge)
        print(f"  O/U {line}: {r['bets']:5d} bets  ROI {r['roi']*100:+6.1f}%  "
              f"hit {r['hit']*100:4.1f}%  profit {r['profit']:+7.1f}u")
    print("CARDS:")
    for line in CARD_LINES:
        r = roi(recs, "lam_card", "lam_card_base", "card_total", line, args.overround, args.edge)
        print(f"  O/U {line}: {r['bets']:5d} bets  ROI {r['roi']*100:+6.1f}%  "
              f"hit {r['hit']*100:4.1f}%  profit {r['profit']:+7.1f}u")

    eng = [r for r in recs if r["ref"]]
    if eng:
        print(f"\nCARDS, English leagues only (referee known), n={len(eng)}:")
        for line in CARD_LINES:
            r = roi(eng, "lam_card", "lam_card_base", "card_total", line, args.overround, args.edge)
            rn = roi(eng, "lam_card_noref", "lam_card_base", "card_total", line, args.overround, args.edge)
            print(f"  O/U {line}: with-ref ROI {r['roi']*100:+6.1f}% ({r['bets']} bets)   "
                  f"no-ref ROI {rn['roi']*100:+6.1f}% ({rn['bets']} bets)")


if __name__ == "__main__":
    main()
