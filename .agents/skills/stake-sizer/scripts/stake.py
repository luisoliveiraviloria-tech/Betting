#!/usr/bin/env python3
"""Kelly stake sizer for horse racing (win market).

Pure standard library, no third-party dependencies, so it runs anywhere.

Core idea
---------
A race is a set of MUTUALLY EXCLUSIVE runners. Exactly one wins. That has two
consequences a naive Kelly calculator gets wrong:

  1. Your model's win probabilities for the field should sum to ~1. If they
     don't, they are renormalized so the edge numbers mean something.
  2. The market's implied probabilities (1/odds) sum to well above 1 (the
     "overround" / vig). De-vigging them gives the market's own fair estimate,
     which is the honest thing to compare your model against.

Edge (expected value per unit staked) for a runner:  edge = p * o - 1
where p is your model win probability and o is the decimal odds. Positive edge
means the bet is +EV by your model. No edge, no bet.

Full Kelly fraction of bankroll:  f = (p*o - 1) / (o - 1) = edge / (o - 1)

Full Kelly maximizes long-run growth but is VOLATILE — deep drawdowns are
normal and a single over-estimated probability overstakes badly. That is why
this tool always prints a fractional-Kelly column alongside it; treat full
Kelly as the ceiling, not the default you must use.

Usage
-----
Single bet:
    python stake.py --bankroll 1000 --prob 0.35 --odds 3.5
    python stake.py --bankroll 1000 --prob 0.35 --odds 5/2      # fractional odds

Whole race from a file (recommended — enables field normalization + de-vig):
    python stake.py --bankroll 1000 --race race.json

race.json:
    {"runners": [
        {"name": "Ballydoyle",   "model_prob": 0.34, "odds": 3.5},
        {"name": "Turf Master",  "model_prob": 0.22, "odds": "9/2"},
        {"name": "Longshot Lad", "model_prob": 0.05, "odds": 15.0}
    ]}

Instead of "model_prob" you may give "model_score" (any positive number, e.g.
a raw model output); scores are normalized across the field into probabilities.

Options:
    --kelly-fraction 0.25   scale every stake (0.25 = quarter Kelly). Default 1.0.
    --min-odds 3.0          odds sanity band, decimal (3.0 = 2/1). Default 3.0.
    --max-odds 7.0          odds sanity band, decimal (7.0 = 6/1). Default 7.0.
    --simultaneous          also compute the log-optimal joint stake when more
                            than one runner in the field qualifies.
    --json                  emit machine-readable JSON instead of a table.
"""

import argparse
import json
import sys
from math import log


def parse_odds(value):
    """Accept decimal odds (3.5) or fractional strings ('5/2', '2-1')."""
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    for sep in ("/", "-"):
        if sep in s:
            num, den = s.split(sep, 1)
            return 1.0 + float(num) / float(den)
    return float(s)


def decimal_to_fractional(o):
    """Best-effort pretty fractional label for a decimal price."""
    net = o - 1.0
    for den in (1, 2, 3, 4, 5, 6, 8, 10):
        num = net * den
        if abs(num - round(num)) < 0.02:
            return f"{int(round(num))}/{den}"
    return f"{net:.1f}/1"


def kelly_fraction(p, o):
    """Full Kelly fraction of bankroll for a single win bet. Clamped at 0."""
    b = o - 1.0
    if b <= 0:
        return 0.0
    f = (p * o - 1.0) / b
    return max(0.0, f)


def size_runner(r, bankroll, kfrac, min_odds, max_odds):
    p = r["prob"]
    o = r["odds"]
    edge = p * o - 1.0
    full_k = kelly_fraction(p, o)
    in_band = min_odds <= o <= max_odds
    qualifies = edge > 0 and in_band
    return {
        "name": r.get("name", "?"),
        "prob": p,
        "odds": o,
        "odds_frac": decimal_to_fractional(o),
        "market_fair_prob": r.get("market_fair_prob"),
        "edge": edge,
        "full_kelly_frac": full_k,
        "full_kelly_stake": full_k * bankroll,
        "scaled_kelly_frac": full_k * kfrac,
        "scaled_kelly_stake": full_k * kfrac * bankroll,
        "in_band": in_band,
        "qualifies": qualifies,
    }


def simultaneous_kelly(runners, iters=20000, lr=0.5):
    """Log-optimal joint stake fractions across mutually exclusive runners.

    Maximizes E[log(final wealth)] where backing runner j means: if j wins you
    hold (1 - S + f_j*o_j), if anyone else wins you hold (1 - S), with S the
    total staked fraction. Concave problem, solved by projected gradient ascent
    with pure Python so there is no numpy/scipy dependency.
    """
    n = len(runners)
    if n == 0:
        return []
    p = [r["prob"] for r in runners]
    o = [r["odds"] for r in runners]
    p_none = max(0.0, 1.0 - sum(p))
    f = [0.0] * n
    for _ in range(iters):
        S = sum(f)
        base = 1.0 - S
        if base <= 1e-9:
            break
        grad = []
        for j in range(n):
            wj = base + f[j] * o[j]
            if wj <= 1e-9:
                grad.append(-1e9)
                continue
            # d/df_j of E[log W]
            g = p[j] * (o[j] - 1.0) / wj
            for k in range(n):
                if k != j:
                    wk = base + f[k] * o[k]
                    g -= p[k] * 1.0 / wk
            g -= p_none * 1.0 / base
            grad.append(g)
        moved = False
        for j in range(n):
            nf = f[j] + lr * grad[j] / (1.0 + _ * 0.001)
            nf = max(0.0, nf)
            if abs(nf - f[j]) > 1e-12:
                moved = True
            f[j] = nf
        # keep total < 1
        S = sum(f)
        if S >= 0.999:
            scale = 0.999 / S
            f = [x * scale for x in f]
        if not moved:
            break
    return f


def prepare_runners(race, min_odds, max_odds):
    runners = []
    scores = []
    have_prob = all("model_prob" in r for r in race)
    for r in race:
        o = parse_odds(r["odds"])
        runners.append({"name": r.get("name", "?"), "odds": o})
        if have_prob:
            runners[-1]["prob"] = float(r["model_prob"])
        else:
            scores.append(float(r.get("model_score", 0.0)))
    if not have_prob:
        total = sum(scores) or 1.0
        for r, s in zip(runners, scores):
            r["prob"] = s / total
    else:
        total = sum(r["prob"] for r in runners)
        if total > 0 and abs(total - 1.0) > 0.02:
            # Field probabilities should sum to 1; renormalize and note it.
            for r in runners:
                r["prob"] = r["prob"] / total
    # De-vig the market for an honest comparison column.
    implied = [1.0 / r["odds"] for r in runners]
    overround = sum(implied)
    for r, imp in zip(runners, implied):
        r["market_fair_prob"] = imp / overround if overround else None
    return runners, overround


def fmt_pct(x):
    return "-" if x is None else f"{x*100:5.1f}%"


def render_table(sized, bankroll, kfrac, overround, min_odds, max_odds, simult):
    lines = []
    lines.append(f"Bankroll: {bankroll:,.2f}   Kelly fraction shown: {kfrac:g}x")
    if overround is not None:
        lines.append(
            f"Market overround: {overround*100:.1f}%  "
            f"(a fair book is 100%; the excess is the vig)"
        )
    lines.append(
        f"Odds sanity band: {decimal_to_fractional(min_odds)}"
        f"–{decimal_to_fractional(max_odds)} "
        f"(decimal {min_odds:g}–{max_odds:g})"
    )
    lines.append("")
    header = (
        f"{'Runner':<18}{'Odds':>7}{'Model':>8}{'Mkt fair':>9}"
        f"{'Edge':>8}{'FullK':>8}{'FullK stk':>11}{'Scaled stk':>11}  Note"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for s in sized:
        note = ""
        if not s["in_band"]:
            note = "outside band — skip"
        elif s["edge"] <= 0:
            note = "no edge — skip"
        elif s["qualifies"]:
            note = "VALUE"
        lines.append(
            f"{s['name'][:17]:<18}"
            f"{s['odds_frac']:>7}"
            f"{fmt_pct(s['prob']):>8}"
            f"{fmt_pct(s['market_fair_prob']):>9}"
            f"{s['edge']*100:>7.1f}%"
            f"{s['full_kelly_frac']*100:>7.1f}%"
            f"{s['full_kelly_stake']:>11,.2f}"
            f"{s['scaled_kelly_stake']:>11,.2f}  {note}"
        )
    lines.append("")

    qualifiers = [s for s in sized if s["qualifies"]]
    qualifiers.sort(key=lambda s: s["edge"], reverse=True)
    if not qualifiers:
        lines.append("RECOMMENDATION: No bet. Nothing clears both the edge and "
                     "the odds band. Passing a race is a position.")
        return "\n".join(lines)

    top = qualifiers[0]
    lines.append("RECOMMENDATION")
    lines.append(
        f"  Back {top['name']} at {top['odds_frac']} "
        f"(edge {top['edge']*100:.1f}%)."
    )
    lines.append(
        f"  Full Kelly: {top['full_kelly_stake']:,.2f} "
        f"({top['full_kelly_frac']*100:.1f}% of bankroll)."
    )
    lines.append(
        f"  Scaled ({kfrac:g}x): {top['scaled_kelly_stake']:,.2f} "
        f"({top['scaled_kelly_frac']*100:.1f}% of bankroll)."
    )
    lines.append(
        "  Full Kelly is the growth-optimal ceiling but swings hard; a "
        "quarter-to-half stake keeps most of the growth for a fraction of the "
        "drawdown. Prefer the scaled figure unless the edge is rock solid."
    )
    if len(qualifiers) > 1:
        others = ", ".join(f"{q['name']} ({q['edge']*100:.1f}%)"
                           for q in qualifiers[1:])
        lines.append("")
        lines.append(
            f"  {len(qualifiers)} runners show value ({others}). They are "
            "mutually exclusive — you cannot collect on two. Backing the single "
            "highest-edge selection is the clean default; if you back more than "
            "one, individual Kelly stakes overbet the race."
        )
        if simult:
            f = simultaneous_kelly(qualifiers)
            lines.append("")
            lines.append("  Log-optimal JOINT stake (accounts for exclusivity):")
            for q, frac in zip(qualifiers, f):
                lines.append(
                    f"    {q['name']:<18} {frac*100:5.1f}% "
                    f"= {frac*bankroll:,.2f}"
                )
            lines.append(f"    total staked: {sum(f)*100:.1f}% of bankroll")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Kelly stake sizer for horse racing.")
    ap.add_argument("--bankroll", type=float, required=True)
    ap.add_argument("--race", help="JSON file with a 'runners' list")
    ap.add_argument("--prob", type=float, help="single-bet model win probability")
    ap.add_argument("--odds", help="single-bet decimal or fractional odds")
    ap.add_argument("--kelly-fraction", type=float, default=1.0)
    ap.add_argument("--min-odds", type=float, default=3.0)
    ap.add_argument("--max-odds", type=float, default=7.0)
    ap.add_argument("--simultaneous", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.race:
        with open(args.race) as fh:
            data = json.load(fh)
        race = data["runners"] if isinstance(data, dict) else data
        runners, overround = prepare_runners(race, args.min_odds, args.max_odds)
    elif args.prob is not None and args.odds is not None:
        runners = [{"name": "selection", "prob": args.prob,
                    "odds": parse_odds(args.odds), "market_fair_prob": None}]
        overround = None
    else:
        ap.error("provide either --race FILE or both --prob and --odds")

    sized = [size_runner(r, args.bankroll, args.kelly_fraction,
                         args.min_odds, args.max_odds) for r in runners]

    if args.json:
        print(json.dumps({"overround": overround, "runners": sized}, indent=2))
    else:
        print(render_table(sized, args.bankroll, args.kelly_fraction,
                            overround, args.min_odds, args.max_odds,
                            args.simultaneous))
    return 0


if __name__ == "__main__":
    sys.exit(main())
