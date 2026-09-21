"""
Leak-free, as-of feature builder for UK+IRE horse racing.

Contract: every feature for a runner in a race at time T uses only information
that existed strictly BEFORE that race.
  * horse history    -> the horse's own earlier runs (groupby + shift(1) / cumsum-minus-self)
  * jockey / trainer -> aggregates over strictly EARLIER CALENDAR DAYS (same-day rides excluded)
  * race-relative    -> other runners' pre-race features in the SAME race only
  * post-race columns (pos, rpr, ts, time...) appear only as previous-run values
Verified by `racing.check_features` (truncation test: dropping all rows after a
cutoff must not change any earlier row's features).

Small-sample handling: rates are "shrunk actual/expected wins":
      ae = (wins + K) / (expected_wins + K),   expected_win per run = 1/field_size
so 2-from-3 does not beat 30-from-100 (a thin record stays near 1.0 = neutral).

Feature groups by prefix:
      rp_*  derived from Racing Post RPR/Topspeed. These are decaying at source
            (missing 5% -> 20-30% since Oct-2025; see RECON.md s.10). Every model
            must be benchmarked with and without them.
      ih_*  our OWN performance rating (racing/rating.py), built from beaten
            lengths, per-runner times, weight, class and going -- all data we
            control. Exists to replace rp_*, which carries 100% of the measured
            edge and is being withdrawn from every affordable feed.
      bm_*  benchmark only (SP-implied probability). NEVER a model input.
"""
import argparse
import sqlite3

import numpy as np
import pandas as pd

from racing.clean import FORM_DB

K_HORSE = 1.0     # pseudo expected-wins: ~10 runs of neutral prior
K_ENTITY = 3.0    # jockey / trainer / combo, all-time
K_WINDOW = 1.0    # 14d / 30d rolling windows (small samples)
K_SLICE = 0.5     # horse x course/distance/going/surface/type
WINDOWS = (14, 30)


def _roll_prev(s: pd.Series, key: pd.Series, window: int, how: str = "mean") -> pd.Series:
    """Rolling stat over the previous `window` *runs* of the same horse. `s` must already be shifted."""
    r = getattr(s.groupby(key, sort=False).rolling(window, min_periods=1), how)()
    return r.droplevel(0).reindex(s.index)


def _ffill_prev(col: pd.Series, key: pd.Series) -> pd.Series:
    """Last KNOWN value of `col` from the horse's earlier runs (shift then forward-fill within horse)."""
    return col.groupby(key, sort=False).shift(1).groupby(key, sort=False).ffill()


def _entity_features(df: pd.DataFrame, key: str, name: str) -> pd.DataFrame:
    """All-time and rolling-window a/e for jockey/trainer/combo using strictly earlier days."""
    daily = (df.groupby([key, "date"], sort=False)
               .agg(runs=("win", "size"), wins=("win", "sum"), exp=("exp_win", "sum"))
               .reset_index().sort_values([key, "date"], kind="mergesort").reset_index(drop=True))
    cols = ["runs", "wins", "exp"]
    prior = daily.groupby(key, sort=False)[cols].cumsum() - daily[cols]  # everything before today
    out = daily[[key, "date"]].copy()
    out[f"{name}_n"] = prior["runs"].values
    out[f"{name}_ae"] = ((prior["wins"] + K_ENTITY) / (prior["exp"] + K_ENTITY)).values
    ts = daily.set_index("date")
    for w in WINDOWS:
        r = ts.groupby(key, sort=False)[cols].rolling(f"{w}D", closed="left").sum().reset_index()
        r = r.rename(columns={"runs": "r_runs", "wins": "r_wins", "exp": "r_exp"})
        assert (r[key].values == daily[key].values).all() and (r["date"].values == daily["date"].values).all(),             "rolling-window rows are not aligned with the daily table"
        # align on (key,date): both frames are sorted by (key,date) and 1 row per pair
        out[f"{name}_r{w}_n"] = r["r_runs"].fillna(0).values
        out[f"{name}_r{w}_ae"] = ((r["r_wins"].fillna(0) + K_WINDOW) / (r["r_exp"].fillna(0) + K_WINDOW)).values
    return out


def _slice_features(df: pd.DataFrame, cols: list[str], name: str) -> None:
    """Horse's own prior record within a slice (same course / distance / going / surface / type)."""
    g = df.groupby(["horse_key"] + cols, sort=False)
    n = g.cumcount()
    wins = g["win"].cumsum() - df["win"]
    exp = g["exp_win"].cumsum() - df["exp_win"]
    df[f"h_{name}_n"] = n
    df[f"h_{name}_ae"] = (wins + K_SLICE) / (exp + K_SLICE)


def build_features(runs: pd.DataFrame) -> pd.DataFrame:
    df = runs.sort_values(["horse_key", "race_dt", "race_key"], kind="mergesort").reset_index(drop=True)
    hk = df["horse_key"]
    g = df.groupby("horse_key", sort=False)

    # ---- race size & neutral expectations (pre-race knowable)
    df["field"] = df.groupby("race_key")["horse_key"].transform("size")
    ran_eff = df["ran"].where(df["ran"] >= 2, df["field"]).astype(float)
    df["exp_win"] = 1.0 / ran_eff
    df["exp_place"] = np.minimum(3.0 / ran_eff, 1.0)

    # ---- horse record before this run
    df["h_n_prior"] = g.cumcount()
    wins_prior = g["win"].cumsum() - df["win"]
    exp_prior = g["exp_win"].cumsum() - df["exp_win"]
    df["h_ae"] = (wins_prior + K_HORSE) / (exp_prior + K_HORSE)
    pl_prior = g["place3"].cumsum() - df["place3"]
    epl_prior = g["exp_place"].cumsum() - df["exp_place"]
    df["h_place_ae"] = (pl_prior + K_HORSE) / (epl_prior + K_HORSE)
    df["h_win_prior"] = wins_prior

    prev_date = g["date"].shift(1)
    df["days_since"] = (df["date"] - prev_date).dt.days

    fin_pct = (df["pos_num"] / ran_eff).where(df["finished"] == 1)
    prev_fin = fin_pct.groupby(hk, sort=False).shift(1)
    df["h_last_finpct"] = prev_fin
    df["h_finpct3"] = _roll_prev(prev_fin, hk, 3)
    df["h_finpct5"] = _roll_prev(prev_fin, hk, 5)
    prev_nf = (1 - df["finished"]).groupby(hk, sort=False).shift(1)
    df["h_nonfinish5"] = _roll_prev(prev_nf, hk, 5)

    # official rating (BHA mark, known pre-race)
    df["or_last"] = _ffill_prev(df["or_"], hk)
    df["or_change"] = df["or_"] - df["or_last"]
    df["dist_change"] = df["dist_f"] - _ffill_prev(df["dist_f"], hk)
    df["class_change"] = df["class_num"] - _ffill_prev(df["class_num"], hk)
    df["wgt_change"] = df["wgt_lb"] - _ffill_prev(df["wgt_lb"], hk)
    df["hg_first"] = (df["hg"].notna() & g["hg"].shift(1).isna() & (df["h_n_prior"] > 0)).astype(int)

    # Racing Post ratings: PREVIOUS runs only (rp_ group, decaying at source)
    df["rp_rpr_last"] = _ffill_prev(df["rpr"], hk)
    df["rp_ts_last"] = _ffill_prev(df["ts"], hk)
    prev_rpr = df["rpr"].groupby(hk, sort=False).shift(1)
    df["rp_rpr_avg3"] = _roll_prev(prev_rpr, hk, 3)
    df["rp_rpr_max3"] = _roll_prev(prev_rpr, hk, 3, "max")
    df["rp_rpr_minus_or"] = df["rp_rpr_last"] - df["or_last"]

    # ---- in-house performance rating: PREVIOUS runs only (mirrors the rp_ block)
    if "ih_perf" in df.columns:
        df["ih_perf_last"] = _ffill_prev(df["ih_perf"], hk)
        prev_ih = df["ih_perf"].groupby(hk, sort=False).shift(1)
        df["ih_perf_avg3"] = _roll_prev(prev_ih, hk, 3)
        df["ih_perf_max3"] = _roll_prev(prev_ih, hk, 3, "max")
        df["ih_perf_best"] = _roll_prev(prev_ih, hk, 20, "max")
        # the key signal: did the horse run ABOVE its official mark last time?
        df["ih_perf_minus_or"] = df["ih_perf_last"] - df["or_last"]
        df["ih_perf_trend"] = df["ih_perf_last"] - df["ih_perf_avg3"]

    # ---- horse x slice experience
    df["dist_b"] = df["dist_f"].round()
    df["going_b"] = df["going_ord"].round()
    _slice_features(df, ["course_base"], "course")
    _slice_features(df, ["dist_b"], "dist")
    _slice_features(df, ["going_b"], "going")
    _slice_features(df, ["surface"], "surface")
    _slice_features(df, ["type"], "type")

    # ---- jockey / trainer / combo, strictly earlier days
    df["combo"] = df["trainer"].fillna("") + "|" + df["jockey"].fillna("")
    for key, name in (("jockey", "j"), ("trainer", "t"), ("combo", "tj")):
        ef = _entity_features(df, key, name)
        df = df.merge(ef, on=[key, "date"], how="left")

    # ---- race-relative (same-race pre-race features only)
    rg = df.groupby("race_key")
    df["or_rel"] = df["or_"] - rg["or_"].transform("mean")
    df["or_rank"] = rg["or_"].rank(pct=True, ascending=False)
    df["wgt_rel"] = df["wgt_lb"] - rg["wgt_lb"].transform("mean")
    df["age_rel"] = df["age"] - rg["age"].transform("mean")
    df["h_ae_rel"] = df["h_ae"] - rg["h_ae"].transform("mean")
    df["h_ae_rank"] = rg["h_ae"].rank(pct=True, ascending=False)
    df["t_ae_rel"] = df["t_ae"] - rg["t_ae"].transform("mean")
    df["j_ae_rel"] = df["j_ae"] - rg["j_ae"].transform("mean")
    df["finpct3_rank"] = rg["h_finpct3"].rank(pct=True, ascending=True)
    df["rp_rpr_rel"] = df["rp_rpr_last"] - rg["rp_rpr_last"].transform("mean")
    df["rp_rpr_rank"] = rg["rp_rpr_last"].rank(pct=True, ascending=False)
    if "ih_perf_last" in df.columns:
        df["ih_perf_rel"] = df["ih_perf_last"] - rg["ih_perf_last"].transform("mean")
        df["ih_perf_rank"] = rg["ih_perf_last"].rank(pct=True, ascending=False)
        df["ih_perf_best_rel"] = df["ih_perf_best"] - rg["ih_perf_best"].transform("mean")
    if "ih_elo" in df.columns:
        # Elo is already the rating held BEFORE this race, so it needs no shifting
        df["ih_elo_rel"] = df["ih_elo"] - rg["ih_elo"].transform("mean")
        df["ih_elo_rank"] = rg["ih_elo"].rank(pct=True, ascending=False)
    df["draw_pct"] = df["draw"] / df["field"]
    # `prize` in the source is the prize money THIS RUNNER WON -- a post-race outcome
    # (the biggest prize in a race belongs to the winner 99.8% of the time). It must never
    # be a runner-level feature. The SUM over a race is the advertised prize fund, which is
    # known in advance and is constant within a race, so it cannot reveal the winner.
    df["race_prize"] = df.groupby("race_key")["prize"].transform("sum")
    df["is_first_run"] = (df["h_n_prior"] == 0).astype(int)
    df["is_jumps"] = df["type"].isin(["Hurdle", "Chase", "NH Flat"]).astype(int)
    df["is_aw"] = (df["surface"] == "AW").astype(int)
    df["is_ire"] = (df["region"] == "IRE").astype(int)
    df["is_female"] = df["sex"].isin(["F", "M", "f", "m"]).astype(int)

    # ---- benchmark only (post-race final price; NOT a model input)
    df["bm_sp_dec"] = df["sp_dec"]
    inv = 1.0 / df["sp_dec"]
    tot = inv.groupby(df["race_key"]).transform("sum")
    full = df["sp_dec"].notna().groupby(df["race_key"]).transform("all")
    df["bm_sp_prob"] = (inv / tot).where(full)

    return df.sort_values(["race_dt", "race_key", "num"], kind="mergesort").reset_index(drop=True)


ID_COLS = ["race_key", "race_id", "race_dt", "date", "horse_key", "horse", "region", "course_base", "surface", "type",
           "is_handicap", "class_num", "dist_f", "going_ord", "field", "num", "win", "place3", "finished",
           "pos_num"]


# Explicit whitelist: a new raw column can never slip into a model by accident.
_PREFIXES = ("h_", "j_", "t_", "tj_", "rp_", "ih_")
_EXPLICIT = ["days_since", "or_", "or_last", "or_change", "or_rel", "or_rank", "dist_change", "class_change",
             "wgt_change", "wgt_lb", "wgt_rel", "age", "age_rel", "hg_first", "finpct3_rank", "draw_pct",
             "is_first_run", "field", "is_handicap", "class_num", "dist_f", "going_ord", "race_prize",
             "is_jumps", "is_aw", "is_ire", "is_female"]


def feature_columns(df: pd.DataFrame, include_rp: bool = True,
                    include_ih: bool = False) -> list[str]:
    """Model inputs.

    `include_ih` defaults to FALSE. The in-house rating (racing/rating.py) is built
    and leak-free, but measured on 2026-09-22 it carries no edge: on its own it
    produces 17 validated bets at -21.8%, and added ALONGSIDE rp_ it dilutes the
    signal (869 bets @ +23.9% -> 611 @ +15.9%). Production therefore uses the
    validated 74-feature set. Pass include_ih=True only to re-test the rating.
    """
    cols = [c for c in df.columns if c.startswith(_PREFIXES) or c in _EXPLICIT]
    cols = [c for c in cols if c not in ("h_dist_b", "ih_perf", "ih_perf_src")]
    if not include_rp:
        cols = [c for c in cols if not c.startswith("rp_")]
    if not include_ih:
        cols = [c for c in cols if not c.startswith("ih_")]
    return cols


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-year", type=int, default=2015)
    args = ap.parse_args()
    con = sqlite3.connect(FORM_DB)
    # only the columns build_features actually reads -- loading the full `runs`
    # table (pedigree, comments, raw text) exhausts memory on a 7.6 GB box.
    cols = ("race_key, race_id, date, race_dt, course_base, region, surface, type, is_handicap, "
            "class_num, dist_f, going_ord, ran, num, draw, horse, horse_key, age, sex, wgt_lb, hg, "
            "jockey, trainer, prize, or_, rpr, ts, pos_num, finished, win, place3, sp_dec")
    runs = pd.read_sql(f"""select {cols}, g.ih_perf, g.ih_elo, g.ih_elo_n from runs r
                           left join rating g using (race_key, horse_key)""",
                       con, parse_dates=["date", "race_dt"])
    for c in runs.select_dtypes("float64").columns:
        runs[c] = runs[c].astype("float32")
    print(f"runs loaded: {len(runs):,}  (in-house rating on {runs.ih_perf.notna().mean():.1%})")
    feats = build_features(runs)
    feats = feats[feats["date"].dt.year >= args.from_year]
    keep = ID_COLS + [c for c in feature_columns(feats) if c not in ID_COLS] + ["bm_sp_dec", "bm_sp_prob"]
    assert not {"pos_num", "win", "place3", "finished"} & set(feature_columns(feats)), "target leaked into features"
    out = feats[keep]
    out.to_sql("features", con, if_exists="replace", index=False, chunksize=50_000)
    con.close()
    print(f"features: {out.shape[0]:,} rows x {out.shape[1]} cols -> {FORM_DB} (table features)")
    print(f"model inputs: {len(feature_columns(feats))}  "
          f"| without rp_: {len(feature_columns(feats, include_rp=False))}  "
          f"| without ih_: {len(feature_columns(feats, include_ih=False))}")


if __name__ == "__main__":
    main()
