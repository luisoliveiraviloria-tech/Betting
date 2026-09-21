"""
Correctness gates for the racing pipeline. Run:  python -m racing.check_features

1. parsers        -- unit checks on the odd formats found in the real data
2. truncation     -- THE leakage test: rebuild features from data cut off at a date;
                     every feature of every earlier row must be bit-identical.
                     If any future row influences a past row, this fails.
3. target guard   -- targets / post-race columns are not in the model-input list
4. strike sanity  -- pick-the-best-runner-by-feature strike rates vs random and vs
                     the SP favourite. A pre-race feature that BEATS the SP favourite
                     by a wide margin is a leak alarm, not a discovery.
"""
import sqlite3
import sys

import numpy as np
import pandas as pd

from racing import clean
from racing.clean import FORM_DB
from racing.features import build_features, feature_columns
from racing.rating import build_rating


def check_parsers() -> None:
    assert clean.parse_dist_f("5f") == 5
    assert clean.parse_dist_f("1m") == 8
    assert clean.parse_dist_f("2m3½f") == 19.5
    assert clean.parse_dist_f("1m7�f") == 15.5            # corrupted half
    assert clean.parse_dist_f("4m�f") == 32.5
    assert abs(clean.parse_dist_f("1m1f209y") - (9 + 209 / 220)) < 1e-9
    assert np.isnan(clean.parse_dist_f("garbage"))
    assert clean.parse_weight_lb("11-6") == 160
    assert clean.parse_weight_lb("7-11") == 109
    assert clean.parse_sp_decimal("5/1") == 6.0
    assert clean.parse_sp_decimal("1/3F") == 1 + 1 / 3
    assert clean.parse_sp_decimal("EvensF") == 2.0 and clean.parse_sp_decimal("Evs") == 2.0
    assert np.isnan(clean.parse_sp_decimal("F"))
    assert clean.classify_region("Kempton (AW)") == "GB"        # brackets != foreign
    assert clean.classify_region("Dundalk (AW) (IRE)") == "IRE"
    assert clean.classify_region("Dundalk (AW)") == "IRE"
    assert clean.classify_region("Newmarket (July)") == "GB"
    assert clean.classify_region("Naas") == "IRE" and clean.classify_region("Naas (IRE)") == "IRE"
    assert clean.classify_region("Aqueduct") is None and clean.classify_region("Auteuil") is None
    assert clean.classify_region("Belmont Park (Perth) (AUS)") is None
    d = pd.Series(pd.to_datetime(["2024-01-01"] * 3))
    got = clean.parse_off_dt(d, pd.Series(["1:42", "16:18", "12:30"]))
    assert list(got.dt.strftime("%H:%M")) == ["13:42", "16:18", "12:30"]
    print("parsers: OK")


def check_truncation(runs: pd.DataFrame, cutoff: str = "2019-03-31") -> None:
    """Rebuild features from truncated data; every earlier row must be identical.

    The in-house rating is REBUILT inside this test rather than reused from the
    database. That is deliberate: rating.py derives standard times from an
    expanding median of earlier races, and recomputing here is the only way to
    prove those standards never peek at races that had not yet been run.
    """
    sub = runs[(runs.date >= "2018-01-01") & (runs.date <= "2019-12-31")]
    full = build_features(build_rating(sub))
    trunc = build_features(build_rating(sub[sub.date <= cutoff]))
    cols = feature_columns(full)
    a = full[full.date <= cutoff].set_index(["race_key", "horse_key"]).sort_index()[cols]
    b = trunc.set_index(["race_key", "horse_key"]).sort_index()[cols]
    assert a.index.equals(b.index), "row sets differ between full and truncated builds"
    bad = []
    for c in cols:
        x, y = a[c].to_numpy(dtype=float), b[c].to_numpy(dtype=float)
        same = np.isclose(x, y, rtol=1e-9, atol=1e-9, equal_nan=True)
        if not same.all():
            bad.append((c, int((~same).sum())))
    assert not bad, f"LEAKAGE: features changed when future rows were removed: {bad}"
    print(f"truncation: OK  ({len(cols)} features x {len(a):,} rows identical with/without data after {cutoff})")


def check_target_guard(feats: pd.DataFrame) -> None:
    cols = set(feature_columns(feats))
    forbidden = {"win", "place3", "finished", "pos_num", "pos_raw", "time_s", "rpr", "ts", "sp_dec",
                 "bm_sp_dec", "bm_sp_prob", "num"}
    assert not (cols & forbidden), f"forbidden columns in model inputs: {cols & forbidden}"
    print("target guard: OK")


def leak_scan(feats: pd.DataFrame, fail: bool = True) -> pd.DataFrame:
    """Automated leak detector -- runs over EVERY feature, not a hand-picked list.

    For each feature, pick the best runner in each race by that feature alone and
    measure the strike rate. Nothing knowable before a race should beat the SP
    favourite (~34%) by much: the market has already priced in everything public.
    A single feature scoring far above that is encoding the result.

    This exists because a hand-written check missed `prize` (the prize money the
    runner WON -- post-race), which made the model look 96% better than the market.
    Never trust a curated list to catch the next one.
    """
    f = feats[(feats.date >= "2021-01-01") & feats.bm_sp_prob.notna() & (feats.field >= 4)]
    fav = f.loc[f.groupby("race_key").bm_sp_prob.idxmax()].win.mean()
    rows = []
    for c in feature_columns(f):
        s = pd.to_numeric(f[c], errors="coerce")
        if s.notna().sum() < 5_000 or s.nunique() < 3:
            continue
        g = f.assign(_v=s).dropna(subset=["_v"])
        # only races where every runner has the feature, so the pick is fair
        full = g.groupby("race_key")._v.transform("size") == g.groupby("race_key").field.transform("first")
        g = g[full]
        if g.race_key.nunique() < 1_000:
            continue
        hi = g.loc[g.groupby("race_key")._v.idxmax()].win.mean()
        lo = g.loc[g.groupby("race_key")._v.idxmin()].win.mean()
        rows.append({"feature": c, "races": g.race_key.nunique(),
                     "strike": max(hi, lo), "corr": abs(s.corr(f.win.astype(float)))})
    r = pd.DataFrame(rows).sort_values("strike", ascending=False)
    bad = r[r.strike > fav + 0.03]
    print(f"\nleak scan: {len(r)} features tested, SP favourite = {fav:.3f}, "
          f"alarm threshold = {fav + 0.03:.3f}")
    print("  highest single-feature strike rates:")
    for _, x in r.head(6).iterrows():
        mark = "  <== LEAK" if x.strike > fav + 0.03 else ""
        print(f"    {x.feature:<22} strike {x.strike:.3f}  |corr| {x['corr']:.3f}  "
              f"races {x.races:>6,}{mark}")
    if len(bad) and fail:
        raise AssertionError(f"LEAK: {list(bad.feature)} beat the SP favourite -- "
                             f"these encode the race result")
    print("  no feature beats the market favourite -> no obvious leak")
    return r


def strike_sanity(feats: pd.DataFrame) -> None:
    f = feats[(feats.date >= "2017-01-01") & feats.bm_sp_prob.notna()]
    f = f[f.field >= 4]
    races = f.race_key.nunique()
    rand = (1.0 / f.groupby("race_key").field.first()).mean()
    fav = f.loc[f.groupby("race_key").bm_sp_prob.idxmax()].win.mean()
    print(f"\nstrike sanity ({races:,} races 2017+, SP known): random {rand:.3f} | SP favourite {fav:.3f}")
    tests = [("or_ (highest OR)", "or_", True), ("h_ae (horse record)", "h_ae", True),
             ("h_place_ae", "h_place_ae", True), ("t_ae (trainer)", "t_ae", True), ("j_ae (jockey)", "j_ae", True),
             ("tj_ae (combo)", "tj_ae", True), ("t_r14_ae (trainer form)", "t_r14_ae", True),
             ("h_finpct3 (lowest = best)", "h_finpct3", False), ("rp_rpr_last (RPR)", "rp_rpr_last", True),
             ("rp_rpr_avg3", "rp_rpr_avg3", True)]
    for label, col, hi in tests:
        g = f.dropna(subset=[col])
        n_ok = g.groupby("race_key")[col].transform("count") == g.groupby("race_key")[col].transform("size")
        g = g[n_ok]  # only races where every runner has the feature -> fair pick
        pick = g.loc[(g.groupby("race_key")[col].idxmax() if hi else g.groupby("race_key")[col].idxmin())]
        base = (1.0 / g.groupby("race_key").field.first()).mean()
        print(f"  {label:<28} strike {pick.win.mean():.3f}  (random {base:.3f})  races {pick.shape[0]:>6,}")
    print("  -> a pre-race feature beating the SP favourite by >3 points would be a LEAK ALARM")


def main() -> None:
    check_parsers()
    con = sqlite3.connect(FORM_DB)
    runs = pd.read_sql("select * from runs", con, parse_dates=["date", "race_dt"])
    check_truncation(runs)
    feats = pd.read_sql("select * from features", con, parse_dates=["date", "race_dt"]) \
        if "--no-strike" not in sys.argv else None
    if feats is not None:
        check_target_guard(feats)
        leak_scan(feats)
        strike_sanity(feats)
    con.close()


if __name__ == "__main__":
    main()
