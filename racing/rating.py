"""
In-house performance rating — a replacement for Racing Post's RPR.

Why this exists
---------------
Measured on 2026-09-21: removing the seven `rp_*` features turns the validated
strategy from 911 bets at +24.3% into 18 bets at -14.8%. **The entire edge is
Racing Post's rating**, and specifically `rp_rpr_minus_or` — "this horse ran
better than its official mark". RPR coverage is decaying at source (~95% -> 75-80%
since Oct 2025) and The Racing API dropped it in June 2026. That is a single point
of failure on a proprietary feed we do not control.

RPR is not magic. It is a performance figure derived from what a horse actually
did: how fast, how far behind, carrying what weight, against what class of rival,
on what going. We hold all of those. This module rebuilds that figure from data
we own.

The figure
----------
For every completed run we compute two independent views and blend them:

  SPEED   how fast the horse ran versus the going-adjusted standard time for that
          course / distance / race type, converted to pounds.
  BEATEN  how far it finished behind the winner relative to the race's own par,
          converted to pounds at a distance-dependent lengths-to-pounds scale.

Both are expressed on the official-rating scale so that `ih_perf - or_` is
directly comparable to `rpr - or_`.

Leakage
-------
The rating for a run is a POST-RACE quantity — that is fine and intended, exactly
as RPR is: `features.py` only ever uses it shifted to PREVIOUS runs. But the
*standard times* it is measured against must not peek. They are therefore built
as expanding medians over strictly earlier races only (`shift(1)`), so a race is
never judged against a standard that includes itself or anything after it. The
truncation test in `check_features.py` enforces this.

Non-finishers (PU/F/UR) have no time and no beaten distance, so they get no
rating; the feature layer forward-fills from the horse's last rated run.
"""
import numpy as np
import pandas as pd

# Lengths-to-pounds. Standard handicapping practice: a length is worth more at
# sprint distances than over marathon trips, because the field is compressed.
_LB_PER_LENGTH = [(6, 3.0), (7, 2.5), (8, 2.25), (10, 2.0), (12, 1.75),
                  (14, 1.5), (16, 1.25), (20, 1.0), (99, 0.75)]
MIN_STANDARD_N = 30          # races needed before a standard time is trusted
OR_SCALE_CENTRE = 70.0       # typical official mark, used to anchor the scale


def lb_per_length(dist_f: pd.Series) -> pd.Series:
    out = pd.Series(np.nan, index=dist_f.index, dtype=float)
    prev = 0.0
    for upper, lb in _LB_PER_LENGTH:
        out = out.where(~((dist_f > prev) & (dist_f <= upper)), lb)
        prev = upper
    return out.fillna(2.0)


def _band(dist_f: pd.Series) -> pd.Series:
    """Distance bucket for grouping standards (half-furlong granularity)."""
    return (dist_f * 2).round() / 2


def seconds_per_length(runs: pd.DataFrame) -> pd.DataFrame:
    """Empirical seconds-per-length by distance band.

    We hold BOTH a per-runner time and the lengths behind the winner, so the
    conversion between them can be measured rather than assumed: within each race,
    regress (time - winner time) on (lengths behind winner).
    """
    d = runs[(runs.finished == 1) & runs.time_s.notna() & runs.ovr_btn_l.notna()].copy()
    win_t = d[d.pos_num == 1].groupby("race_key").time_s.first()
    d["t_delta"] = d.time_s - d.race_key.map(win_t)
    d = d[(d.ovr_btn_l > 0.5) & (d.t_delta > 0) & (d.t_delta < 60)]
    d["band"] = _band(d.dist_f)
    g = d.groupby("band").apply(
        lambda x: pd.Series({"spl": (x.t_delta / x.ovr_btn_l).median(), "n": len(x)}),
        include_groups=False)
    return g[g.n >= 200].reset_index()


def standard_times(runs: pd.DataFrame) -> pd.Series:
    """As-of standard seconds-per-furlong for each (course, distance, type, going).

    Expanding median over strictly EARLIER races only. A race is never compared
    against a standard that includes itself.
    """
    d = runs.sort_values(["race_dt", "race_key"], kind="mergesort")
    win = d[(d.pos_num == 1) & d.time_s.notna() & (d.dist_f > 0)].copy()
    # dead heats put two winners in a race; one timing row per race is enough
    win = win.drop_duplicates("race_key", keep="first")
    win["spf"] = win.time_s / win.dist_f
    # drop obvious timing errors before they poison a standard
    lo, hi = win.spf.quantile([0.001, 0.999])
    win = win[(win.spf >= lo) & (win.spf <= hi)]
    win["_k"] = (win.course_base.astype(str) + "|" + win.surface.astype(str) + "|"
                 + _band(win.dist_f).astype(str) + "|" + win.type.astype(str) + "|"
                 + win.going_ord.round().astype(str))
    g = win.groupby("_k", sort=False).spf
    std = g.transform(lambda s: s.expanding().median().shift(1))
    n = g.transform(lambda s: s.expanding().count().shift(1))
    win["_std"] = std.where(n >= MIN_STANDARD_N)
    # broadcast the race's standard to every runner in that race
    lookup = win.set_index("race_key")["_std"].dropna()
    return runs.race_key.astype(str).map(lookup)


def build_rating(runs: pd.DataFrame) -> pd.DataFrame:
    """Add `ih_perf` (performance rating, official-rating scale) to `runs`."""
    d = runs.copy()
    d["_lb_len"] = lb_per_length(d.dist_f)
    d["_std_spf"] = standard_times(d)

    spl = seconds_per_length(d)
    d["_spl"] = _band(d.dist_f).map(dict(zip(spl.band, spl.spl)))
    d["_spl"] = d["_spl"].fillna(d["_spl"].median())

    # ---- race par: the quality level of the race, from PRE-RACE marks
    or_mean = d.groupby("race_key").or_.transform("mean")
    d["_par"] = or_mean.fillna(
        d.groupby("race_key").class_num.transform("first").map(
            {1: 95.0, 2: 85.0, 3: 75.0, 4: 65.0, 5: 55.0, 6: 48.0, 7: 42.0}))
    d["_par"] = d["_par"].fillna(OR_SCALE_CENTRE)

    # ---- BEATEN view: lengths behind the winner, relative to the field's average
    mean_btn = d.groupby("race_key").ovr_btn_l.transform("mean")
    d["_perf_btn"] = d._par + (mean_btn - d.ovr_btn_l) * d._lb_len

    # ---- SPEED view: time versus the as-of standard for those conditions
    spf = d.time_s / d.dist_f
    secs_faster = (d._std_spf - spf) * d.dist_f
    d["_perf_spd"] = d._par + (secs_faster / d._spl) * d._lb_len

    # weight carried: more weight for the same performance = a better performance
    wgt_adj = (d.wgt_lb - d.groupby("race_key").wgt_lb.transform("mean")).fillna(0.0)

    # blend: speed where a trustworthy standard exists, otherwise beaten-lengths
    perf = d._perf_spd.where(d._perf_spd.notna(), d._perf_btn)
    both = d._perf_spd.notna() & d._perf_btn.notna()
    perf = perf.where(~both, 0.5 * d._perf_spd + 0.5 * d._perf_btn)
    perf = perf + wgt_adj

    # only completed runs get a rating; clip absurd values from timing errors
    d["ih_perf"] = perf.where(d.finished == 1).clip(-20, 200)
    d["ih_perf_src"] = np.where(d.ih_perf.isna(), "none",
                                np.where(both, "speed+beaten",
                                         np.where(d._perf_spd.notna(), "speed", "beaten")))
    return d.drop(columns=[c for c in d.columns if c.startswith("_")])


def elo_ratings(runs: pd.DataFrame, k: float = 26.0, base: float = 1500.0,
                scale: float = 400.0, regress: float = 0.02) -> pd.DataFrame:
    """Latent-ability rating from the network of who-beat-whom (multi-player Elo).

    Why this exists alongside `ih_perf`: that figure judges a run against the mean
    OFFICIAL RATING of the field, which is missing on 42% of runs (non-handicaps)
    and lags reality. Elo instead infers each horse's strength from every rival it
    has ever met, so the quality of opposition is learned rather than assumed.

    Per race, each finisher's actual score is the fraction of rivals it beat, and
    its expected score is the usual logistic function of the rating differences.
    Non-finishers (PU/F/UR) are excluded from the comparison entirely -- a pulled-up
    horse tells us nothing reliable about merit.

    The rating returned for a run is the one held BEFORE it, so this is a pre-race
    quantity by construction and needs no shifting. Ratings regress gently towards
    the mean each run so that a horse's distant past fades.
    """
    d = runs.sort_values(["race_dt", "race_key"], kind="mergesort").reset_index(drop=True)
    horse = d.horse_key.to_numpy()
    pos = pd.to_numeric(d.pos_num, errors="coerce").to_numpy(dtype=float)
    pre = np.full(len(d), np.nan)
    n_prior = np.zeros(len(d))
    ratings: dict[str, float] = {}
    counts: dict[str, int] = {}

    for idx in d.groupby("race_key", sort=False).indices.values():
        hs = horse[idx]
        r = np.array([ratings.get(h, base) for h in hs], dtype=float)
        pre[idx] = r
        n_prior[idx] = [counts.get(h, 0) for h in hs]
        p = pos[idx]
        ok = ~np.isnan(p)
        if ok.sum() < 2:
            for h in hs:
                counts[h] = counts.get(h, 0) + 1
            continue
        rr, pp = r[ok], p[ok]
        m = len(rr)
        # expected: mean pairwise win probability against the rest of the field
        diff = rr[None, :] - rr[:, None]
        exp = (1.0 / (1.0 + 10.0 ** (-diff / scale)))
        np.fill_diagonal(exp, 0.0)
        e = exp.sum(axis=1) / (m - 1)
        # actual: fraction of rivals finished ahead of (0.5 for a dead heat)
        beat = (pp[None, :] > pp[:, None]).astype(float) + 0.5 * (pp[None, :] == pp[:, None])
        np.fill_diagonal(beat, 0.0)
        a = beat.sum(axis=1) / (m - 1)
        new = rr + k * (a - e)
        new = base + (new - base) * (1.0 - regress)
        for h, v in zip(hs[ok], new):
            ratings[h] = float(v)
        for h in hs:
            counts[h] = counts.get(h, 0) + 1

    d["ih_elo"] = pre
    d["ih_elo_n"] = n_prior
    return d


def report(d: pd.DataFrame) -> None:
    ok = d[d.ih_perf.notna()]
    print(f"rated runs: {len(ok):,} / {len(d):,} ({len(ok) / len(d):.1%})")
    print("source mix:", d.ih_perf_src.value_counts().to_dict())
    print(f"ih_perf: mean {ok.ih_perf.mean():.1f}  sd {ok.ih_perf.std():.1f}  "
          f"range [{ok.ih_perf.quantile(0.01):.0f}, {ok.ih_perf.quantile(0.99):.0f}]")
    both = d[d.ih_perf.notna() & d.rpr.notna()]
    if len(both):
        print(f"\ncorrelation with RPR (the thing we are replacing): "
              f"{both.ih_perf.corr(both.rpr):+.3f}  (n={len(both):,})")
        print(f"  ih_perf - or_ vs rpr - or_ : "
              f"{(both.ih_perf - both.or_).corr(both.rpr - both.or_):+.3f}")
        w = both[both.win == 1]
        print(f"  mean rating of winners {w.ih_perf.mean():.1f} vs all {both.ih_perf.mean():.1f}")


if __name__ == "__main__":
    import sqlite3
    from racing.clean import FORM_DB
    con = sqlite3.connect(FORM_DB, timeout=180)
    runs = pd.read_sql("select * from runs", con, parse_dates=["date", "race_dt"])
    print(f"loaded {len(runs):,} runs")
    rated = build_rating(runs)
    report(rated)
    print("\nbuilding Elo latent-ability ratings ...")
    rated = elo_ratings(rated)
    e = rated[rated.ih_elo_n >= 3]
    print(f"  Elo: mean {e.ih_elo.mean():.0f} sd {e.ih_elo.std():.0f} "
          f"| winners {e[e.win==1].ih_elo.mean():.0f} vs all {e.ih_elo.mean():.0f}")
    both = e[e.rpr.notna()]
    print(f"  corr(Elo, RPR) = {both.ih_elo.corr(both.rpr):+.3f} "
          f"| corr(Elo, win) = {e.ih_elo.corr(e.win.astype(float)):+.3f}")
    rated[["race_key", "horse_key", "ih_perf", "ih_perf_src", "ih_elo", "ih_elo_n"]].to_sql(
        "rating", con, if_exists="replace", index=False, chunksize=50_000)
    con.execute("create index if not exists ix_rating on rating (race_key, horse_key)")
    con.commit()
    con.close()
    print(f"\nwrote rating -> {FORM_DB}")
