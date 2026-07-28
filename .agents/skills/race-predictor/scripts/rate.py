#!/usr/bin/env python3
"""Turn a structured racecard into calibrated win probabilities.

Pure standard library. Reads a race JSON with whatever signals were gathered and
produces a model win-probability for each runner, plus a canonical race.json that
the stake-sizer's stake.py consumes directly.

Model, in one paragraph
-----------------------
Betting markets are hard to beat, so the de-vigged market probability is the
anchor. On top of it sits a HANDICAPPING RATING built in speed-figure points:
a base speed figure, adjusted by recent form, layoff, going/track suitability,
distance-&-surface suitability, jockey and trainer strike rates, weight, and the
race's pace shape. Ratings go through a softmax to a figure-probability, which is
blended with the market:

    model_prob = market_weight * market_prob + (1 - market_weight) * figure_prob

Every adjustment is modest and capped, so no single soft factor can run away with
the price, and the market keeps everything honest. All coefficients live in the
FACTORS dict below and are explained in references/method.md.

Only `name` and `odds` are required per runner. Everything else is optional and
degrades gracefully when missing — a runner with no data simply sits at its
market estimate. Use `--explain` to see each runner's rating breakdown.

Per-runner fields
-----------------
    name, odds                 required (odds decimal or fractional)
    speed        int           recent speed figure (the backbone)
    form         [int,...]     recent finish positions, most recent first
    days         int           days since last run
    going_suit   -2..+2        suited to today's going/track condition
    dist_suit    -2..+2        suited to today's distance & surface
    jockey_sr    0..1          jockey recent win strike rate
    trainer_sr   0..1          trainer recent win strike rate
    weight       lbs           weight carried
    draw         int           post position (needs race-level draw_bias to act)
    pace         E|EP|P|S      running style: Early / Early-Presser / Presser / Sustained(closer)

Race-level context (top-level keys alongside "runners")
    going        str           free text, informational
    draw_bias    low|high|none + optional draw_bias_strength (0..1)
"""

import argparse
import json
import sys
from math import exp

# All adjustments are in speed-figure points and capped so soft factors inform
# but never dominate the speed figure + market anchor. Tune in one place.
FACTORS = {
    "form_per_place": 1.5,   # points per place better than mid-pack (3rd)
    "layoff_35_60": -1.0,    # 35-60 days off
    "layoff_60_plus": -3.0,  # >60 days off
    "going_per_point": 2.5,  # x going_suit (-2..+2)
    "dist_per_point": 2.5,   # x dist_suit  (-2..+2)
    "jk_scale": 30.0, "jk_base": 0.12, "jk_cap": 4.0,   # jockey strike rate
    "tr_scale": 25.0, "tr_base": 0.12, "tr_cap": 4.0,   # trainer strike rate
    "weight_per_lb": 0.4, "weight_cap": 5.0,            # lighter = advantage
    "pace_lone_speed": 2.5,  # uncontested front-runner
    "pace_collapse": 2.0,    # closers when the pace is hot
    "pace_early_penalty": -1.5,  # early types in a speed duel
    "draw_scale": 2.5,       # x draw_bias_strength x normalized draw edge
}


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


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def build_context(runners):
    speeds = [float(r["speed"]) for r in runners if r.get("speed") is not None]
    weights = [float(r["weight"]) for r in runners if r.get("weight") is not None]
    paces = [str(r.get("pace", "")).upper() for r in runners]
    early = sum(1 for p in paces if p in ("E", "EP"))
    draws = [r["draw"] for r in runners if r.get("draw") is not None]
    return {
        "avg_speed": sum(speeds) / len(speeds) if speeds else 90.0,
        "avg_weight": sum(weights) / len(weights) if weights else None,
        "early_count": early,
        "field": len(runners),
        "draw_min": min(draws) if draws else None,
        "draw_max": max(draws) if draws else None,
    }


def adjustments(r, ctx, draw_bias, draw_strength):
    """Return an ordered list of (label, points) so it can be explained."""
    adj = []
    form = r.get("form")
    if form:
        recent = form[:3]
        avg_finish = sum(recent) / len(recent)
        adj.append(("form", (3.0 - avg_finish) * FACTORS["form_per_place"]))
    days = r.get("days")
    if days is not None:
        if days > 60:
            adj.append(("layoff", FACTORS["layoff_60_plus"]))
        elif days > 35:
            adj.append(("layoff", FACTORS["layoff_35_60"]))
    if r.get("going_suit") is not None:
        adj.append(("going", float(r["going_suit"]) * FACTORS["going_per_point"]))
    if r.get("dist_suit") is not None:
        adj.append(("dist/surf", float(r["dist_suit"]) * FACTORS["dist_per_point"]))
    if r.get("jockey_sr") is not None:
        v = (float(r["jockey_sr"]) - FACTORS["jk_base"]) * FACTORS["jk_scale"]
        adj.append(("jockey", clamp(v, -FACTORS["jk_cap"], FACTORS["jk_cap"])))
    if r.get("trainer_sr") is not None:
        v = (float(r["trainer_sr"]) - FACTORS["tr_base"]) * FACTORS["tr_scale"]
        adj.append(("trainer", clamp(v, -FACTORS["tr_cap"], FACTORS["tr_cap"])))
    if r.get("weight") is not None and ctx["avg_weight"] is not None:
        v = (ctx["avg_weight"] - float(r["weight"])) * FACTORS["weight_per_lb"]
        adj.append(("weight", clamp(v, -FACTORS["weight_cap"], FACTORS["weight_cap"])))
    # Pace shape: lone front-runner gets a lift; in a speed duel, closers gain
    # and early types are penalised.
    pace = str(r.get("pace", "")).upper()
    if pace:
        if ctx["early_count"] == 1 and pace in ("E", "EP"):
            adj.append(("pace(lone speed)", FACTORS["pace_lone_speed"]))
        elif ctx["early_count"] >= 3:
            if pace == "S":
                adj.append(("pace(collapse)", FACTORS["pace_collapse"]))
            elif pace in ("E", "EP"):
                adj.append(("pace(duel)", FACTORS["pace_early_penalty"]))
    # Draw bias, only if the race declares one and draws are present.
    if (draw_bias in ("low", "high") and r.get("draw") is not None
            and ctx["draw_min"] is not None and ctx["draw_max"] > ctx["draw_min"]):
        span = ctx["draw_max"] - ctx["draw_min"]
        norm = (r["draw"] - ctx["draw_min"]) / span  # 0 = lowest, 1 = highest
        edge = (0.5 - norm) if draw_bias == "low" else (norm - 0.5)  # -0.5..+0.5
        adj.append(("draw", edge * 2 * draw_strength * FACTORS["draw_scale"]))
    return adj


def rating(r, ctx, draw_bias, draw_strength):
    base = float(r["speed"]) if r.get("speed") is not None else ctx["avg_speed"]
    return base + sum(pts for _, pts in adjustments(r, ctx, draw_bias, draw_strength))


def softmax(scores, temp):
    m = max(scores)
    exps = [exp((s - m) / temp) for s in scores]
    tot = sum(exps) or 1.0
    return [e / tot for e in exps]


def compute(runners, market_weight, temp, draw_bias, draw_strength):
    ctx = build_context(runners)
    dec = [parse_odds(r["odds"]) for r in runners]
    implied = [1.0 / d for d in dec]
    overround = sum(implied)
    market_prob = [i / overround for i in implied]

    ratings = [rating(r, ctx, draw_bias, draw_strength) for r in runners]
    any_signal = any(r.get("speed") is not None or adjustments(r, ctx, draw_bias, draw_strength)
                     for r in runners)
    fig_prob = softmax(ratings, temp) if any_signal else market_prob[:]

    raw = [market_weight * mp + (1.0 - market_weight) * fp
           for mp, fp in zip(market_prob, fig_prob)]
    tot = sum(raw) or 1.0
    model_prob = [x / tot for x in raw]

    rows = []
    for r, d, mp, fp, mo in zip(runners, dec, market_prob, fig_prob, model_prob):
        rows.append({
            "name": r.get("name", "?"), "odds": d, "odds_frac": decimal_to_fractional(d),
            "market_prob": mp, "figure_prob": fp, "model_prob": mo,
            "edge": mo * d - 1.0, "rating": rating(r, ctx, draw_bias, draw_strength),
            "breakdown": adjustments(r, ctx, draw_bias, draw_strength),
            "base_speed": r.get("speed"),
        })
    return rows, overround, ctx


def render(rows, overround, market_weight, temp, ctx, going, explain):
    lines = []
    hdr = f"market_weight={market_weight:g}  temp={temp:g}  overround={overround*100:.1f}%"
    if going:
        hdr += f"  going={going}"
    lines.append(hdr)
    header = (f"{'Runner':<18}{'Odds':>7}{'Rating':>8}{'Market':>8}"
              f"{'Figure':>8}{'Model':>8}{'Edge':>8}")
    lines.append(header)
    lines.append("-" * len(header))
    ordered = sorted(rows, key=lambda x: x["model_prob"], reverse=True)
    for x in ordered:
        lines.append(
            f"{x['name'][:17]:<18}{x['odds_frac']:>7}{x['rating']:>8.1f}"
            f"{x['market_prob']*100:>7.1f}%{x['figure_prob']*100:>7.1f}%"
            f"{x['model_prob']*100:>7.1f}%{x['edge']*100:>7.1f}%")
    lines.append("")
    if explain:
        lines.append("Rating breakdown (base speed + adjustments, in figure points):")
        for x in ordered:
            base = x["base_speed"] if x["base_speed"] is not None else f"~{ctx['avg_speed']:.0f}(no fig)"
            parts = "  ".join(f"{lbl}{pts:+.1f}" for lbl, pts in x["breakdown"]) or "(none)"
            lines.append(f"  {x['name'][:17]:<18} base {base}  {parts}  = {x['rating']:.1f}")
        lines.append("")
    top = ordered[0]
    lines.append(f"MOST PROBABLE: {top['name']} at {top['odds_frac']} — model "
                 f"{top['model_prob']*100:.1f}% (market {top['market_prob']*100:.1f}%), "
                 f"edge {top['edge']*100:+.1f}%")
    value = [x for x in ordered if x["edge"] > 0]
    if value:
        v = ", ".join(f"{x['name']} ({x['edge']*100:+.1f}%)" for x in value)
        lines.append(f"Positive-edge runners: {v}")
        lines.append("Next: size with stake-sizer/scripts/stake.py (applies odds band + Kelly). "
                     "A short-priced 'most probable' runner is often NOT a value bet.")
    else:
        lines.append("No positive-edge runner: the model agrees with the market — nothing to back.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Racecard -> win probabilities.")
    ap.add_argument("--race", required=True)
    ap.add_argument("--out")
    ap.add_argument("--market-weight", type=float, default=0.70)
    ap.add_argument("--temp", type=float, default=8.0)
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    with open(args.race) as fh:
        data = json.load(fh)
    runners = data["runners"] if isinstance(data, dict) else data
    going = data.get("going") if isinstance(data, dict) else None
    draw_bias = (data.get("draw_bias", "none") if isinstance(data, dict) else "none") or "none"
    draw_strength = float(data.get("draw_bias_strength", 1.0)) if isinstance(data, dict) else 1.0

    rows, overround, ctx = compute(runners, args.market_weight, args.temp,
                                   draw_bias, draw_strength)

    out_path = args.out or "race.json"
    with open(out_path, "w") as fh:
        json.dump({"runners": [{"name": x["name"],
                                "model_prob": round(x["model_prob"], 4),
                                "odds": round(x["odds"], 3)} for x in rows]}, fh, indent=2)

    print(render(rows, overround, args.market_weight, args.temp, ctx, going, args.explain))
    print(f"\nWrote {out_path} (feed it to stake-sizer/scripts/stake.py)")
    if args.json:
        print(json.dumps([{k: v for k, v in r.items() if k != "breakdown"} for r in rows], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
