"""
Market baseline: how good is the Betfair price as a probability estimate?

This establishes the number any model must beat. It answers three questions:

  1. CALIBRATION -- when the market implies 70%, do 70% of those horses win?
     Reported per price band with a 95% CI, so a band with 300 runners is not
     read like one with 300,000.

  2. FAVOURITE-LONGSHOT BIAS -- flat-stake ROI backing every runner in a band at
     BSP, after commission. A market with no bias returns ~-commission in every
     band. Systematic drift from short to long prices is the classic bias.

  3. INFORMATION TIMING -- the same scoring for the morning price, the pre-play
     weighted average and the BSP. If BSP scores best, information arrives
     during the day and an early bet is a worse-informed bet. That sets how
     much edge we would need to justify betting early.

Scoring uses log loss and Brier score against the `1/field` no-information
baseline. Commission is applied per bet on winnings (Betfair charges on net
market profit, so this is a slight over-estimate of the cost -- deliberately
conservative). Losing bets pay none.
"""
import argparse
import sqlite3

import numpy as np
import pandas as pd

from racing.clean import FORM_DB

COMMISSION = 0.05
BANDS = [(1.01, 1.3), (1.3, 1.5), (1.5, 1.7), (1.7, 1.9), (1.9, 2.2), (2.2, 2.75),
         (2.75, 3.5), (3.5, 5.0), (5.0, 8.0), (8.0, 13.0), (13.0, 25.0), (25.0, 1000.0)]


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def roi_at(price: pd.Series, win: pd.Series, commission: float = COMMISSION) -> float:
    ret = np.where(win == 1, (price - 1.0) * (1 - commission), -1.0)
    return float(np.mean(ret))


def calibration_table(df: pd.DataFrame, price_col: str = "bsp", prob_col: str = "bsp_prob_norm") -> pd.DataFrame:
    rows = []
    for lo, hi in BANDS:
        s = df[(df[price_col] >= lo) & (df[price_col] < hi)]
        if len(s) < 50:
            continue
        n, k = len(s), int(s.win.sum())
        clo, chi = _wilson(k, n)
        rows.append({
            "band": f"{lo:g}-{hi:g}", "n": n,
            "implied": s[prob_col].mean(), "actual": k / n,
            "ci_lo": clo, "ci_hi": chi,
            "diff_pp": (k / n - s[prob_col].mean()) * 100,
            "roi": roi_at(s[price_col], s.win),
            # a perfectly fair price still loses commission on the winning bets:
            # E[return] = -c * (1 - p).  This is the break-even bar, not zero.
            "roi_fair": -COMMISSION * (1 - k / n),
        })
    return pd.DataFrame(rows)


def score(prob: pd.Series, win: pd.Series) -> dict:
    p = prob.clip(1e-6, 1 - 1e-6)
    return {"n": len(p), "log_loss": float(-(win * np.log(p) + (1 - win) * np.log(1 - p)).mean()),
            "brier": float(((p - win) ** 2).mean())}


def print_table(t: pd.DataFrame, title: str) -> None:
    print(f"\n{title}")
    print(f"  {'band':>12} {'n':>8} {'implied':>8} {'actual':>8} {'95% CI':>16} {'diff':>7} "
          f"{'ROI':>8} {'fair':>7} {'vs fair':>7}")
    for _, r in t.iterrows():
        flag = "" if r.ci_lo <= r.implied <= r.ci_hi else "  <-- outside CI"
        print(f"  {r.band:>12} {r.n:>8,.0f} {r.implied:>8.3f} {r.actual:>8.3f} "
              f"[{r.ci_lo:>6.3f},{r.ci_hi:>6.3f}] {r.diff_pp:>+6.1f}pp {r.roi:>+7.1%} "
              f"{r.roi_fair:>+7.1%} {r.roi - r.roi_fair:>+7.1%}{flag}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-liquidity", type=float, default=0.0,
                    help="require at least this much pre-play traded volume (GBP)")
    args = ap.parse_args()

    con = sqlite3.connect(FORM_DB, timeout=180)
    df = pd.read_sql("""
        select m.*, f.field, f.win, f.is_handicap, f.type, f.region as form_region, f.surface, f.date as d
        from market m join features f using (race_key, horse_key)
        where m.bsp is not null and m.book_full = 1""", con, parse_dates=["date"])
    con.close()
    if args.min_liquidity:
        df = df[df.pptradedvol >= args.min_liquidity]
    print(f"runners: {len(df):,}  races: {df.race_key.nunique():,}  "
          f"{df.date.min().date()} -> {df.date.max().date()}  (full-book races only)")
    print(f"book (sum of 1/bsp) mean {df.groupby('race_key').book.first().mean():.4f}")

    print_table(calibration_table(df), "1. CALIBRATION + ROI at BSP (commission 5%)")
    overall, fair = roi_at(df.bsp, df.win), -COMMISSION * (1 - df.win.mean())
    print(f"\n  overall flat-stake ROI at BSP: {overall:+.2%}   "
          f"break-even bar for a perfectly fair market: {fair:+.2%}   excess: {overall - fair:+.2%}")
    print("  ('fair' = -commission x (1 - strike): even a perfectly priced market loses this much,")
    print("   because commission is charged on winnings. 'excess' is the real edge against us.)")

    print("\n2. INFORMATION TIMING -- scoring each price as a probability estimate")
    base = 1.0 / df.field
    cands = {"no-info (1/field)": base, "morning WAP": df.morning_prob,
             "pre-play WAP": df.ppwap_prob, "BSP (raw 1/bsp)": df.bsp_prob,
             "BSP (book-normalised)": df.bsp_prob_norm}
    # restrict to rows where EVERY estimator exists, so the numbers are comparable
    ok = pd.concat([p.notna() & np.isfinite(p) for p in cands.values()], axis=1).all(axis=1)
    print(f"  common rows: {int(ok.sum()):,} of {len(df):,}")
    print(f"  {'estimator':>24} {'log loss':>10} {'brier':>9} {'vs no-info':>11}")
    base_ll = score(base[ok], df.win[ok])["log_loss"]
    for name, p in cands.items():
        sc = score(p[ok], df.win[ok])
        print(f"  {name:>24} {sc['log_loss']:>10.4f} {sc['brier']:>9.5f} "
              f"{(base_ll - sc['log_loss']) / base_ll:>10.1%}")
    print("  (lower is better; 'vs no-info' = % of the 1/field log loss removed)")

    print("\n3. ROI by segment at BSP (5% commission)")
    segs = [("all", df), ("handicap", df[df.is_handicap == 1]), ("non-handicap", df[df.is_handicap == 0]),
            ("flat", df[df.type == "Flat"]), ("jumps", df[df.type != "Flat"]),
            ("AW", df[df.surface == "AW"]), ("turf", df[df.surface == "Turf"]),
            ("GB", df[df.form_region == "GB"]), ("IRE", df[df.form_region == "IRE"]),
            ("field<=8", df[df.field <= 8]), ("field 9-14", df[(df.field > 8) & (df.field <= 14)]),
            ("field>14", df[df.field > 14]),
            ("price 1.0-2.2", df[df.bsp < 2.2]), ("price 2.2-5", df[(df.bsp >= 2.2) & (df.bsp < 5)]),
            ("price 5+", df[df.bsp >= 5])]
    print(f"  {'segment':>16} {'n':>9} {'strike':>8} {'ROI':>8}")
    for name, s in segs:
        if len(s) < 100:
            continue
        print(f"  {name:>16} {len(s):>9,} {s.win.mean():>8.3f} {roi_at(s.bsp, s.win):>+8.2%}")

    print("\n4. FAVOURITES ONLY (shortest price in each race)")
    fav = df.loc[df.groupby("race_key").bsp.idxmin()]
    print(f"  n={len(fav):,}  strike {fav.win.mean():.3f}  ROI {roi_at(fav.bsp, fav.win):+.2%}"
          f"  mean BSP {fav.bsp.mean():.2f}")


if __name__ == "__main__":
    main()
