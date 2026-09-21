"""
Does the in-house rating replace Racing Post's?

Walk-forward comparison of four feature sets against the market, then the SAME
production betting gates applied to each:

  MARKET    the de-vigged BSP price alone
  RP        the original 74 features (includes rp_*, excludes ih_*)
  IH        rp_* REMOVED, ih_* in its place  <-- the question
  BOTH      everything

IH is the one that matters. If it holds the edge, the system no longer depends on
a proprietary feed that is being withdrawn.
"""
import argparse

import numpy as np
import pandas as pd

from racing import ev as evmod
from racing.backtest import build_bets, flat_roi
from racing.ev import Gates
from racing.features import feature_columns
from racing.model import fit_predict, load_data, log_loss, prepare

OOS = "racing/data/oos_rating_test.parquet"
GATES = Gates(min_ev=0.06, min_edge=0.0, max_price=4.0, min_liquidity=500)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-year", type=int, default=2018)
    args = ap.parse_args()

    df = load_data()
    df, all_cols, _ = prepare(df)
    sets = {
        "RP": [c for c in all_cols if not c.startswith("ih_")],
        "IH": [c for c in all_cols if not c.startswith("rp_")],
        "BOTH": all_cols,
    }
    for k, v in sets.items():
        print(f"  {k}: {len(v)} features")
    print()

    rows = []
    for y in range(args.start_year, 2027):
        tr, te = df[df.date.dt.year < y], df[df.date.dt.year == y]
        if len(tr) < 50_000 or len(te) < 5_000:
            continue
        res = te[["race_key", "horse_key", "date", "win", "bsp", "bsp_prob_norm",
                  "field", "pptradedvol"]].copy()
        res["year"] = y
        res["p_market"] = te.bsp_prob_norm.values
        line = f"  {y}: market {log_loss(res.p_market.values, res.win.values):.5f}"
        for name, cols in sets.items():
            p, bst = fit_predict(tr, te, cols, init_col="mkt_logit")
            res[f"p_{name}"] = p
            line += f" | {name} {log_loss(p, res.win.values):.5f} ({bst.num_trees()}t)"
        rows.append(res)
        print(line, flush=True)

    oos = pd.concat(rows, ignore_index=True)
    oos.to_parquet(OOS, index=False)
    sel, val = oos[oos.year < 2024], oos[oos.year >= 2024]

    print("\n" + "=" * 78)
    print("SAME production gates (EV>=6%, price<=4, liquidity>=£500)")
    print("=" * 78)
    for tag, dset in (("SELECTION 2018-23", sel), ("VALIDATION 2024-26", val)):
        print(f"\n{tag}")
        print(f"  {'features':>8} {'n':>7} {'strike':>7} {'meanBSP':>8} {'ROI':>9} {'95% CI':>19} {'t':>6}")
        for name in sets:
            col = f"p_{name}"
            unc = evmod.calibration_uncertainty(sel, col)
            b = build_bets(dset, GATES, col, unc)
            if len(b) < 10:
                print(f"  {name:>8} {len(b):>7,}  (too few bets to judge)")
                continue
            ro, se = flat_roi(b)
            print(f"  {name:>8} {len(b):>7,} {b.win.mean():>7.3f} {b.bsp.mean():>8.2f} "
                  f"{ro:>+8.2%} [{ro - 1.96 * se:>+6.2%},{ro + 1.96 * se:>+6.2%}] {ro / se:>6.2f}")

    print("\nper-year ROI on validation:")
    for name in sets:
        unc = evmod.calibration_uncertainty(sel, f"p_{name}")
        b = build_bets(val, GATES, f"p_{name}", unc)
        if len(b) < 10:
            continue
        parts = []
        for y, g in b.groupby("year"):
            r, _ = flat_roi(g)
            parts.append(f"{y} {r:+.1%} (n={len(g)})")
        print(f"  {name:>8}: " + "  ".join(parts))


if __name__ == "__main__":
    main()
