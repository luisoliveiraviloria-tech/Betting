#!/usr/bin/env python3
"""KPI dashboard for the bet log.

Reads the tracker CSV (see tracker/bet_log_template.csv) and prints:
ROI/yield, CLV, hit rate, average odds/edge, max drawdown, Brier score,
and volume stats. Settled bets only (result W or L); voids are excluded
from ROI but counted in volume.

Usage:
    python3 kpi.py ../tracker/bet_log_template.csv
"""

import csv
import sys


def f(row, key, default=None):
    v = row.get(key, "").strip()
    if v == "":
        return default
    return float(v)


def main(path: str) -> None:
    with open(path, newline="") as fh:
        rows = [r for r in csv.DictReader(fh)]

    settled = [r for r in rows if r.get("result", "").strip().upper() in ("W", "L")]
    if not settled:
        sys.exit("no settled bets in log")

    total_stake = sum(f(r, "stake", 0.0) for r in settled)
    total_pnl = sum(f(r, "pnl", 0.0) for r in settled)
    wins = sum(1 for r in settled if r["result"].strip().upper() == "W")

    clv = [f(r, "clv_pct") for r in settled if f(r, "clv_pct") is not None]
    edges = [f(r, "edge") for r in settled if f(r, "edge") is not None]
    odds = [f(r, "odds_taken") for r in settled if f(r, "odds_taken") is not None]

    # Brier: model_prob is P(selection wins)
    briers = []
    for r in settled:
        p = f(r, "model_prob")
        if p is not None:
            outcome = 1.0 if r["result"].strip().upper() == "W" else 0.0
            briers.append((p - outcome) ** 2)

    # max drawdown on cumulative pnl, chronological order assumed
    peak = cum = 0.0
    max_dd = 0.0
    for r in settled:
        cum += f(r, "pnl", 0.0)
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    n = len(settled)
    print(f"settled bets:        {n}   (voids/open: {len(rows) - n})")
    print(f"total staked:        {total_stake:.2f}")
    print(f"total pnl:           {total_pnl:+.2f}")
    print(f"ROI / yield:         {total_pnl / total_stake:+.2%}" if total_stake else "ROI: n/a")
    print(f"hit rate:            {wins / n:.1%}  (diagnostic only)")
    if odds:
        print(f"avg odds taken:      {sum(odds) / len(odds):.2f}")
    if edges:
        print(f"avg edge at bet:     {sum(edges) / len(edges):+.2%}")
    if clv:
        avg_clv = sum(clv) / len(clv)
        flag = "healthy" if avg_clv > 0 else "WARNING: not beating the close"
        print(f"avg CLV:             {avg_clv:+.2%}  ({flag})")
    else:
        print("avg CLV:             n/a  (record close_odds -> clv_pct on every bet!)")
    if briers:
        print(f"Brier score:         {sum(briers) / len(briers):.4f}  (target < 0.24)")
    print(f"max drawdown:        {max_dd:.2f} units")
    if n < 100:
        print("\nnote: fewer than 100 settled bets - judge nothing yet (SYSTEM.md section 7)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
