#!/usr/bin/env python3
"""Turn a structured racecard into calibrated win probabilities.

Pure standard library. Reads a race JSON with whatever signals were gathered
(market odds, recent speed figures, recent form, days since last run) and
produces a model win-probability for each runner, plus a canonical race.json
that the stake-sizer's stake.py consumes directly.

Why it works the way it does
----------------------------
Betting markets are hard to beat: the de-vigged market probability is a strong,
well-calibrated prior. A raw figure model on its own is noisy and, left alone,
invents absurd edges on longshots (a 30/1 shot does not really have a 20% chance
just because its last speed figure looked ok). So this model does NOT replace
the market — it ANCHORS to the de-vigged market and only tilts toward a runner
where the form figures give a genuine, proportionate reason.

    model_prob = market_weight * market_prob
               + (1 - market_weight) * figure_prob      (then renormalized)

- market_weight high (default 0.65) → trust the market, small/rare edges, low risk.
- market_weight low → trust your figures more, bigger and more frequent edges,
  but more model risk. Move this dial deliberately, not hopefully.

The figure model is a softmax over each runner's rating (recent speed figure,
nudged by recent form and layoff). Softmax means rating GAPS map to probability
gaps smoothly; `--temp` controls how decisively a rating edge converts to
probability (smaller temp = more decisive).

This is a transparent heuristic, not a trained model. It is a sane STARTING
prior. The honest path to a real edge is logging every prediction (journal.py)
and backtesting the method against results — see references/method.md.

Usage
-----
    python3 rate.py --race card.json                 # print table + write race.json
    python3 rate.py --race card.json --market-weight 0.5 --temp 6
    python3 rate.py --race card.json --out race.json # explicit output path

card.json:
    {"runners": [
      {"name": "Steampunk",     "odds": "7/5",  "speed": 117, "form": [1,2,1], "days": 21},
      {"name": "Crushed Ice",   "odds": "3/2",  "speed": 91},
      {"name": "Dessert First", "odds": "12/1", "speed": 83}
    ]}

Only `name` and `odds` are required. `speed` (a recent speed figure) drives the
tilt; runners with no speed sit at the market estimate. `form` (list of recent
finish positions, most recent first) and `days` (days since last run) apply
small, documented adjustments.
"""

import argparse
import json
import sys
from math import exp


def parse_odds(value):
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower()
    if s in ("evs", "evens", "even"):
        return 2.0
    for sep in ("/", "-"):
        if sep in s:
            num, den = s.split(sep, 1)
            return 1.0 + float(num) / float(den)
    return float(s)


def decimal_to_fractional(o):
    net = o - 1.0
    for den in (1, 2, 3, 4, 5, 6, 8, 10):
        num = net * den
        if abs(num - round(num)) < 0.02:
            return f"{int(round(num))}/{den}"
    return f"{net:.1f}/1"


def rating(r):
    """Combine available signals into a single rating in speed-figure points.

    Speed figure is the backbone. Recent form gives a small bump for winning
    recently and a small penalty for trailing in. A long layoff gets a mild
    penalty for uncertainty. All adjustments are deliberately gentle so the
    speed figure dominates and the market anchor keeps everything honest.
    """
    if "speed" not in r or r["speed"] is None:
        return None
    score = float(r["speed"])
    form = r.get("form")
    if form:
        recent = form[:3]
        avg_finish = sum(recent) / len(recent)
        # 1st ≈ +2, mid-pack neutral, tailed-off small negative.
        score += (3.0 - avg_finish) * 1.5
    days = r.get("days")
    if days is not None:
        if days > 60:
            score -= 3.0
        elif days > 35:
            score -= 1.0
    return score


def softmax(scores, temp):
    m = max(scores)
    exps = [exp((s - m) / temp) for s in scores]
    tot = sum(exps) or 1.0
    return [e / tot for e in exps]


def compute(runners, market_weight, temp):
    dec = [parse_odds(r["odds"]) for r in runners]
    implied = [1.0 / d for d in dec]
    overround = sum(implied)
    market_prob = [i / overround for i in implied]

    ratings = [rating(r) for r in runners]
    have = [x for x in ratings if x is not None]
    if have:
        # Runners lacking a figure sit at the field's average rating so the
        # figure model stays neutral about them rather than guessing.
        fallback = sum(have) / len(have)
        filled = [x if x is not None else fallback for x in ratings]
        fig_prob = softmax(filled, temp)
    else:
        fig_prob = market_prob[:]  # no figures anywhere → lean entirely on market

    raw = [market_weight * mp + (1.0 - market_weight) * fp
           for mp, fp in zip(market_prob, fig_prob)]
    tot = sum(raw) or 1.0
    model_prob = [x / tot for x in raw]

    out = []
    for r, d, mp, fp, mo in zip(runners, dec, market_prob, fig_prob, model_prob):
        out.append({
            "name": r.get("name", "?"),
            "odds": d,
            "odds_frac": decimal_to_fractional(d),
            "market_prob": mp,
            "figure_prob": fp,
            "model_prob": mo,
            "edge": mo * d - 1.0,
            "rating": rating(r),
        })
    return out, overround


def render(rows, overround, market_weight, temp):
    lines = []
    lines.append(f"market_weight={market_weight:g}  temp={temp:g}  "
                 f"overround={overround*100:.1f}%")
    header = (f"{'Runner':<18}{'Odds':>7}{'Rating':>8}{'Market':>8}"
             f"{'Figure':>8}{'Model':>8}{'Edge':>8}")
    lines.append(header)
    lines.append("-" * len(header))
    ordered = sorted(rows, key=lambda x: x["model_prob"], reverse=True)
    for x in ordered:
        rat = "-" if x["rating"] is None else f"{x['rating']:.0f}"
        lines.append(
            f"{x['name'][:17]:<18}{x['odds_frac']:>7}{rat:>8}"
            f"{x['market_prob']*100:>7.1f}%{x['figure_prob']*100:>7.1f}%"
            f"{x['model_prob']*100:>7.1f}%{x['edge']*100:>7.1f}%"
        )
    lines.append("")
    top = ordered[0]
    lines.append(f"MOST PROBABLE: {top['name']} at {top['odds_frac']} "
                 f"— model {top['model_prob']*100:.1f}% "
                 f"(market {top['market_prob']*100:.1f}%), edge {top['edge']*100:+.1f}%")
    value = [x for x in ordered if x["edge"] > 0]
    if value:
        v = ", ".join(f"{x['name']} ({x['edge']*100:+.1f}%)" for x in value)
        lines.append(f"Positive-edge runners: {v}")
        lines.append("Next: size with stake-sizer/scripts/stake.py, which applies "
                     "the odds band and Kelly. A short-priced 'most probable' "
                     "runner is often NOT a value bet — that is expected.")
    else:
        lines.append("No positive-edge runner: the model agrees with the market. "
                     "The likeliest winner is fairly priced — nothing to back.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Racecard -> win probabilities.")
    ap.add_argument("--race", required=True, help="input racecard JSON")
    ap.add_argument("--out", help="output race.json for staking (default: race.json)")
    ap.add_argument("--market-weight", type=float, default=0.70)
    ap.add_argument("--temp", type=float, default=8.0)
    ap.add_argument("--json", action="store_true", help="print rows as JSON too")
    args = ap.parse_args(argv)

    with open(args.race) as fh:
        data = json.load(fh)
    runners = data["runners"] if isinstance(data, dict) else data

    rows, overround = compute(runners, args.market_weight, args.temp)

    out_path = args.out or "race.json"
    with open(out_path, "w") as fh:
        json.dump({"runners": [{"name": x["name"],
                                "model_prob": round(x["model_prob"], 4),
                                "odds": round(x["odds"], 3)} for x in rows]},
                  fh, indent=2)

    print(render(rows, overround, args.market_weight, args.temp))
    print(f"\nWrote {out_path} (feed it to stake-sizer/scripts/stake.py)")
    if args.json:
        print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
