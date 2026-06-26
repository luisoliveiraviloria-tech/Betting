#!/usr/bin/env python3
"""
Backtest the Elo model against historical results AND closing odds
(data/{tour}_odds.csv from fetch_odds_history.py).

Answers two questions the live tool can't:
  1. Is the model CALIBRATED? When it says 65%, do those win ~65%?
  2. Does the value signal actually MAKE MONEY vs. real bookmaker odds?

It walks every match in date order, computes the pre-match model probability
BEFORE updating ratings (no lookahead), then scores those probabilities for
calibration (log-loss, Brier, reliability curve) and simulates flat-stake
value betting at market-average and Pinnacle prices.

The model has two tunables that matter for calibration:
  - prediction scale `s`: p = 1 / (1 + 10 ** (-(ra-rb)/400 * s)).
    s>1 makes favourites stronger (fixes "underrates favourites"); s=1 is
    textbook Elo. This is a logit-space calibration knob.
  - surface blend (scale, max weight) as in elo_predict.py.

Usage:
  python3 backtest.py --tour atp
  python3 backtest.py --tour atp --tune
  python3 backtest.py --tour wta --scale 1.15
"""
import argparse
import csv
import datetime
import math
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
INITIAL_ELO = 1500.0


def parse_date(s):
    return datetime.date.fromisoformat(s)


def load_rows(tour):
    path = os.path.join(DATA_DIR, f"{tour}_odds.csv")
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r["Winner"] or not r["Loser"] or not r["Date"]:
                continue
            try:
                date = parse_date(r["Date"])
            except ValueError:
                continue

            def fnum(x):
                try:
                    return float(x)
                except (TypeError, ValueError):
                    return None

            rows.append({
                "date": date,
                "surface": (r["Surface"] or "").strip() or None,
                "winner": r["Winner"].strip(),
                "loser": r["Loser"].strip(),
                "psw": fnum(r["PSW"]), "psl": fnum(r["PSL"]),
                "avgw": fnum(r["AvgW"]), "avgl": fnum(r["AvgL"]),
                "comment": (r["Comment"] or "").strip(),
            })
    rows.sort(key=lambda r: r["date"])
    return rows


def k_factor(n, scale=250.0, offset=5.0, exp=0.4):
    return scale / (n + offset) ** exp


def devig_two_way(price_a, price_b):
    ra, rb = 1.0 / price_a, 1.0 / price_b
    t = ra + rb
    return ra / t, rb / t


class EloEngine:
    """Configurable surface-aware Elo, mirroring elo_predict.py's update rules
    but with the prediction scale and blend exposed for tuning."""

    def __init__(self, scale=1.0, blend_scale=20.0, max_surface_weight=0.7):
        self.scale = scale
        self.blend_scale = blend_scale
        self.max_surface_weight = max_surface_weight
        self.r = {}

    def _p(self, name):
        if name not in self.r:
            self.r[name] = {"o": INITIAL_ELO, "on": 0, "s": {}, "sn": {}}
        return self.r[name]

    def blended(self, name, surface):
        p = self._p(name)
        overall = p["o"]
        if not surface or surface not in p["s"]:
            return overall
        n = p["sn"].get(surface, 0)
        w = min(self.max_surface_weight, n / (n + self.blend_scale))
        return w * p["s"][surface] + (1 - w) * overall

    def prob(self, a, b, surface):
        ra, rb = self.blended(a, surface), self.blended(b, surface)
        return 1.0 / (1.0 + 10 ** (-(ra - rb) / 400.0 * self.scale))

    def update(self, winner, loser, surface):
        pw, pl = self._p(winner), self._p(loser)
        ew = 1.0 / (1.0 + 10 ** ((pl["o"] - pw["o"]) / 400.0))
        kw, kl = k_factor(pw["on"]), k_factor(pl["on"])
        pw["o"] += kw * (1 - ew)
        pl["o"] += kl * (0 - (1 - ew))
        pw["on"] += 1
        pl["on"] += 1
        if surface:
            sw = pw["s"].setdefault(surface, INITIAL_ELO)
            sl = pl["s"].setdefault(surface, INITIAL_ELO)
            nw = pw["sn"].setdefault(surface, 0)
            nl = pl["sn"].setdefault(surface, 0)
            esw = 1.0 / (1.0 + 10 ** ((sl - sw) / 400.0))
            ksw, ksl = k_factor(nw), k_factor(nl)
            pw["s"][surface] = sw + ksw * (1 - esw)
            pl["s"][surface] = sl + ksl * (0 - (1 - esw))
            pw["sn"][surface] = nw + 1
            pl["sn"][surface] = nl + 1


def run(tour, scale=1.0, blend_scale=20.0, max_surface_weight=0.7,
        burn_in_year=2016):
    """Walk all matches; return per-match prediction records (post burn-in)."""
    rows = load_rows(tour)
    eng = EloEngine(scale, blend_scale, max_surface_weight)
    records = []
    for m in rows:
        w, l, surf, date = m["winner"], m["loser"], m["surface"], m["date"]
        # pre-match prediction (no lookahead)
        p_w = eng.prob(w, l, surf)
        if date.year > burn_in_year and m["comment"] in ("Completed", "Retired", ""):
            records.append({
                "date": date, "p_w": p_w,
                "psw": m["psw"], "psl": m["psl"],
                "avgw": m["avgw"], "avgl": m["avgl"],
            })
        eng.update(w, l, surf)
    return records


# ---- metrics -------------------------------------------------------------

def calibration_metrics(records):
    n = len(records)
    if not n:
        return {}
    ll = -sum(math.log(max(r["p_w"], 1e-12)) for r in records) / n
    brier = sum((1 - r["p_w"]) ** 2 for r in records) / n
    # accuracy: did the model's favourite (p_w>0.5 means winner was favoured) win?
    acc = sum(1 for r in records if r["p_w"] > 0.5) / n
    return {"n": n, "log_loss": ll, "brier": brier, "fav_accuracy": acc}


def market_metrics(records):
    """Same metrics for the de-vigged market (Avg odds) - the benchmark to beat."""
    rs = [r for r in records if r["avgw"] and r["avgl"]]
    n = len(rs)
    if not n:
        return {}
    ll = brier = acc = 0.0
    for r in rs:
        pw, _ = devig_two_way(r["avgw"], r["avgl"])
        ll += -math.log(max(pw, 1e-12))
        brier += (1 - pw) ** 2
        acc += 1 if pw > 0.5 else 0
    return {"n": n, "log_loss": ll / n, "brier": brier / n, "fav_accuracy": acc / n}


def reliability_curve(records, bins=10):
    """Bin by predicted prob (from the favourite's perspective) vs win rate."""
    buckets = [[] for _ in range(bins)]
    for r in records:
        # fold to favourite perspective so bins span 0.5-1.0 meaningfully
        p_fav = max(r["p_w"], 1 - r["p_w"])
        won = 1 if r["p_w"] > 0.5 else 0
        idx = min(bins - 1, int(p_fav * bins))
        buckets[idx].append((p_fav, won))
    out = []
    for i, b in enumerate(buckets):
        if not b:
            continue
        pred = sum(x[0] for x in b) / len(b)
        emp = sum(x[1] for x in b) / len(b)
        out.append((pred, emp, len(b)))
    return out


def roi_sim(records, edge_thresh=0.0, min_odds=1.3, max_odds=5.0, price="avg"):
    """Flat-stake (1u) value betting at `price` (avg|ps). Bet any side whose
    model prob exceeds its de-vigged market prob by >= edge_thresh."""
    wcol, lcol = (f"{price}w", f"{price}l")
    staked = profit = bets = wins = 0
    for r in records:
        ow, ol = r[wcol], r[lcol]
        if not ow or not ol:
            continue
        mkt_w, mkt_l = devig_two_way(ow, ol)
        p_w, p_l = r["p_w"], 1 - r["p_w"]
        # winner side
        if min_odds <= ow <= max_odds and (p_w - mkt_w) >= edge_thresh:
            staked += 1
            profit += ow - 1
            bets += 1
            wins += 1
        # loser side
        if min_odds <= ol <= max_odds and (p_l - mkt_l) >= edge_thresh:
            staked += 1
            profit += -1
            bets += 1
    roi = (profit / staked) if staked else 0.0
    return {"bets": bets, "staked": staked, "profit": profit,
            "roi": roi, "hit_rate": (wins / bets) if bets else 0.0}


# ---- tuning --------------------------------------------------------------

def split(records, val_end_year=2022):
    val = [r for r in records if r["date"].year <= val_end_year]
    test = [r for r in records if r["date"].year > val_end_year]
    return val, test


def tune_scale(records_val):
    """1-D search for the prediction scale minimising validation log-loss.
    Note: changing scale only rescales probs, so we can recompute log-loss
    from stored p_w by inverting to a rating-diff proxy."""
    best = (None, float("inf"))
    # we stored p_w at scale=1.0; logit(p) scales linearly with `scale`
    for s in [round(0.80 + 0.025 * i, 3) for i in range(25)]:  # 0.80 .. 1.40
        ll = 0.0
        for r in records_val:
            p1 = min(max(r["p_w"], 1e-9), 1 - 1e-9)
            logit = math.log(p1 / (1 - p1))
            p = 1.0 / (1.0 + math.exp(-logit * s))
            ll += -math.log(max(p, 1e-12))
        ll /= len(records_val)
        if ll < best[1]:
            best = (s, ll)
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tour", choices=["atp", "wta"], default="atp")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--blend-scale", type=float, default=20.0)
    ap.add_argument("--max-surface-weight", type=float, default=0.7)
    ap.add_argument("--edge", type=float, default=0.0, help="min edge to bet")
    ap.add_argument("--tune", action="store_true")
    args = ap.parse_args()

    records = run(args.tour, scale=args.scale, blend_scale=args.blend_scale,
                  max_surface_weight=args.max_surface_weight)
    print(f"== {args.tour.upper()} | {len(records)} scored matches (post burn-in) ==\n")

    if args.tune:
        val, test = split(records)
        s, ll = tune_scale(val)
        print(f"Tuned prediction scale on validation (<=2022): s={s}  (val log-loss {ll:.4f})")
        print("Re-running test set (>2022) with tuned scale...\n")
        records = run(args.tour, scale=s, blend_scale=args.blend_scale,
                      max_surface_weight=args.max_surface_weight)
        _, records = split(records)
        tag = f"TEST SET (2023+), scale={s}"
    else:
        tag = f"ALL (2017+), scale={args.scale}"

    cm = calibration_metrics(records)
    mm = market_metrics(records)
    print(f"--- Calibration [{tag}] ---")
    print(f"  model : log-loss {cm['log_loss']:.4f}  Brier {cm['brier']:.4f}  "
          f"fav-acc {cm['fav_accuracy']*100:.1f}%  (n={cm['n']})")
    print(f"  market: log-loss {mm['log_loss']:.4f}  Brier {mm['brier']:.4f}  "
          f"fav-acc {mm['fav_accuracy']*100:.1f}%  (n={mm['n']})")

    print("\n--- Reliability (model favourite) ---")
    print("  pred%   emp%    n")
    for pred, emp, n in reliability_curve(records):
        flag = "  <-- overconfident" if emp < pred - 0.03 else ("  <-- underconfident" if emp > pred + 0.03 else "")
        print(f"  {pred*100:5.1f}  {emp*100:5.1f}  {n:5d}{flag}")

    print("\n--- Value-bet ROI ---")
    for price in ("avg", "ps"):
        for edge in sorted(set([args.edge, 0.0, 0.03, 0.05])):
            r = roi_sim(records, edge_thresh=edge, price=price)
            print(f"  {price.upper():3s} edge>={edge*100:4.1f}pp: "
                  f"{r['bets']:5d} bets  ROI {r['roi']*100:+6.1f}%  "
                  f"hit {r['hit_rate']*100:4.1f}%  profit {r['profit']:+8.1f}u")


if __name__ == "__main__":
    main()
