#!/usr/bin/env python3
"""
Predict total corners and cards for an upcoming match, from all history to date.

Builds the running stats over the entire dataset, then prints expected totals
and over/under probabilities for the standard lines. Supplying the referee
(English leagues only) sharpens the cards line - that's the model's main edge.

Usage:
  python3 predict.py --league E0 --home "Man United" --away "Liverpool" --ref "M Oliver"
  python3 predict.py --league SP1 --home "Real Madrid" --away "Sevilla"
"""
import argparse

from model import load_matches, StatsEngine, p_over

CORNER_LINES = [8.5, 9.5, 10.5, 11.5]
CARD_LINES = [2.5, 3.5, 4.5, 5.5]


def build_current_engine():
    eng = StatsEngine()
    for r in load_matches():
        eng.update(r)
    return eng


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", required=True, help="E0 E1 SP1 I1 D1 F1")
    ap.add_argument("--home", required=True)
    ap.add_argument("--away", required=True)
    ap.add_argument("--ref", default="", help="referee (English leagues only)")
    args = ap.parse_args()

    eng = build_current_engine()
    lam_c = eng.predict_corners(args.league, args.home, args.away)
    lam_k = eng.predict_cards(args.league, args.home, args.away, args.ref, use_ref=bool(args.ref))

    print(f"{args.home} vs {args.away}  [{args.league}"
          + (f", ref {args.ref}]" if args.ref else "]"))
    print(f"\nCORNERS  expected total {lam_c:.2f}")
    for ln in CORNER_LINES:
        po = p_over(lam_c, ln)
        print(f"  over {ln}: {po*100:5.1f}%   under: {(1-po)*100:5.1f}%   "
              f"fair odds  O {1/po:4.2f} / U {1/(1-po):4.2f}")
    print(f"\nCARDS    expected total {lam_k:.2f}"
          + ("" if args.ref else "   (no referee given - less sharp)"))
    for ln in CARD_LINES:
        po = p_over(lam_k, ln)
        print(f"  over {ln}: {po*100:5.1f}%   under: {(1-po)*100:5.1f}%   "
              f"fair odds  O {1/po:4.2f} / U {1/(1-po):4.2f}")

    print("\nNB: these are MODEL fair prices. We have no historical cards/corners"
          "\nodds yet, so this is NOT validated for ROI - compare to the book's"
          "\nline for interest only until the odds-validation step is done.")


if __name__ == "__main__":
    main()
