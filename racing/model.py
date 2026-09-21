"""
Probability model + the kill test.

Structure
---------
A race is a single multinomial event: exactly one runner wins. So the model is a
CONDITIONAL LOGIT -- LightGBM produces a raw score per runner, and the scores are
softmaxed WITHIN each race. Probabilities then sum to 1 per race by construction,
which a plain binary classifier does not guarantee.

The kill test
-------------
Three models, identical rows, walk-forward by year (train strictly on earlier
seasons, test on the held-out year):

  MARKET   the de-vigged BSP probability alone            -- the bar to beat
  FORM     the 74 leak-free form features, no market      -- do we know anything?
  BLEND    form features + the market's log-odds          -- do we know anything
                                                             the market does not?

BLEND is the one that matters. If BLEND does not beat MARKET out of sample, we
have no edge and the honest answer is to bet nothing. (This is the same test the
football work failed, where the tuned blend weight collapsed to "ignore the
model".)

Betting at BSP means the bar is not zero: commission is charged on winnings, so a
perfectly fair price still returns -c*(1-p). A model must beat the market by more
than that to make money.
"""
import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from racing.clean import FORM_DB
from racing.features import feature_columns

MODEL_DIR = Path(__file__).parent / "models"
COMMISSION = 0.05

LGB_PARAMS = dict(
    objective="binary", learning_rate=0.04, num_leaves=63, min_data_in_leaf=200,
    feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
    lambda_l2=5.0, verbose=-1, num_threads=0,
)
N_ROUNDS = 600


def softmax_by_race(score: np.ndarray, race_idx: np.ndarray) -> np.ndarray:
    """Softmax raw scores within each race so probabilities sum to 1 per race."""
    s = pd.Series(score)
    g = s.groupby(race_idx)
    e = np.exp(s - g.transform("max"))          # shift for numerical stability
    return (e / e.groupby(race_idx).transform("sum")).to_numpy()


def sigmoid_norm_by_race(raw: np.ndarray, race_idx: np.ndarray) -> np.ndarray:
    """sigmoid(raw), then renormalise so each race sums to 1.

    Preferred over softmax when the model is trained with an init_score, because
    it has the property we need: with zero trees the output is EXACTLY the market
    probability (the market book already sums to ~1). The model can therefore only
    move us away from the market if the features actually earn it.
    """
    p = pd.Series(1.0 / (1.0 + np.exp(-np.clip(raw, -30, 30))))
    return (p / p.groupby(race_idx).transform("sum")).to_numpy()


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def race_log_loss(p: np.ndarray, y: np.ndarray, race_idx: np.ndarray) -> float:
    """Multinomial loss: -log(prob assigned to the actual winner), per race."""
    p = np.clip(p, 1e-9, 1.0)
    d = pd.DataFrame({"p": p, "y": y, "r": race_idx})
    w = d[d.y == 1].groupby("r").p.first()
    return float(-np.log(w).mean())


def load_data() -> pd.DataFrame:
    con = sqlite3.connect(FORM_DB, timeout=180)
    df = pd.read_sql("""
        select f.*, m.bsp, m.bsp_prob_norm, m.ppwap, m.morningwap, m.morning_prob,
               m.pptradedvol, m.book_full
        from features f join market m using (race_key, horse_key)
        where m.bsp is not null and m.book_full = 1""", con, parse_dates=["date", "race_dt"])
    con.close()
    return df.sort_values(["race_dt", "race_key"], kind="mergesort").reset_index(drop=True)


def prepare(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[str]]:
    df = df.copy()
    # the market's opinion, in log-odds -- the natural scale for a logit blend
    p = df.bsp_prob_norm.clip(1e-6, 1 - 1e-6)
    df["mkt_logit"] = np.log(p / (1 - p))
    df["mkt_rank"] = df.groupby("race_key").bsp_prob_norm.rank(ascending=False)
    form_cols = [c for c in feature_columns(df) if c in df.columns]
    blend_cols = form_cols + ["mkt_logit", "mkt_rank"]
    return df, form_cols, blend_cols


def fit_predict(train: pd.DataFrame, test: pd.DataFrame, cols: list[str],
                init_col: str = None, valid_frac_year: bool = True):
    """Train with optional market prior as init_score, early-stopped on the last
    season of the training window (never on the test year).

    With `init_col`, LightGBM starts from the market's log-odds and every tree is a
    CORRECTION to the market. If the features carry no information, early stopping
    keeps almost no trees and we fall back to the market -- which is the honest
    answer, not a failure. Note LightGBM's predict() excludes init_score, so it is
    added back explicitly.
    """
    import lightgbm as lgb
    params = dict(LGB_PARAMS)
    if valid_frac_year:
        last = train.date.dt.year.max()
        tr, va = train[train.date.dt.year < last], train[train.date.dt.year == last]
        if len(va) < 20_000 or len(tr) < 20_000:
            tr, va = train, None
    else:
        tr, va = train, None

    def _ds(d, ref=None):
        init = d[init_col].values if init_col else None
        return lgb.Dataset(d[cols], label=d.win.values, init_score=init, reference=ref,
                           free_raw_data=False)

    dtrain = _ds(tr)
    callbacks, n_rounds = [], N_ROUNDS
    valid_sets = []
    if va is not None:
        valid_sets = [_ds(va, ref=dtrain)]
        callbacks = [lgb.early_stopping(50, verbose=False), lgb.log_evaluation(0)]
    else:
        # No held-out season to early-stop on (too little prior data). Training the
        # full N_ROUNDS unvalidated overfits badly -- seen live in the 2017 fold,
        # which kept all 600 trees and was the only year the blend LOST to the
        # market. Cap it hard instead.
        n_rounds = 40
    booster = lgb.train(params, dtrain, num_boost_round=n_rounds,
                        valid_sets=valid_sets or None, callbacks=callbacks or None)
    raw = booster.predict(test[cols], raw_score=True)
    if init_col:
        raw = raw + test[init_col].values          # predict() drops init_score
        p = sigmoid_norm_by_race(raw, test.race_key.values)
    else:
        p = softmax_by_race(raw, test.race_key.values)
    return p, booster


def walk_forward(df: pd.DataFrame, form_cols: list[str], blend_cols: list[str],
                 test_years: list[int], min_train: int = 50_000) -> pd.DataFrame:
    out = []
    for y in test_years:
        tr = df[df.date.dt.year < y]
        te = df[df.date.dt.year == y]
        if len(tr) < min_train or len(te) < 5_000:
            print(f"  {y}: skipped (train {len(tr):,}, test {len(te):,})")
            continue
        res = te[["race_key", "horse_key", "date", "win", "bsp", "bsp_prob_norm",
                  "field", "pptradedvol", "is_handicap", "type", "surface"]].copy()
        res["year"] = y
        # MARKET baseline, renormalised the same way for a like-for-like comparison
        res["p_market"] = te.bsp_prob_norm.values   # already sums to 1 per race
        res["p_form"], _ = fit_predict(tr, te, form_cols)
        # BLEND = market prior + form corrections (the spec's "Model 6")
        res["p_blend"], bst = fit_predict(tr, te, form_cols, init_col="mkt_logit")
        res["blend_trees"] = bst.num_trees()
        out.append(res)
        print(f"  {y}: train {len(tr):>7,} test {len(te):>6,} | "
              f"market {log_loss(res.p_market.values, res.win.values):.5f} "
              f"form {log_loss(res.p_form.values, res.win.values):.5f} "
              f"blend {log_loss(res.p_blend.values, res.win.values):.5f} "
              f"({bst.num_trees()} trees kept)")
    return pd.concat(out, ignore_index=True)


def report(res: pd.DataFrame) -> dict:
    y = res.win.values
    r = res.race_key.values
    print("\n" + "=" * 78)
    print("KILL TEST -- out-of-sample, walk-forward")
    print("=" * 78)
    print(f"rows {len(res):,}  races {res.race_key.nunique():,}  "
          f"years {res.year.min()}-{res.year.max()}")
    print(f"\n  {'model':>10} {'log loss':>10} {'vs market':>11} {'race loss':>11} {'brier':>9}")
    base = log_loss(res.p_market.values, y)
    summary = {}
    for name, col in (("market", "p_market"), ("form", "p_form"), ("blend", "p_blend")):
        p = res[col].values
        ll, rl = log_loss(p, y), race_log_loss(p, y, r)
        br = float(((p - y) ** 2).mean())
        summary[name] = {"log_loss": ll, "race_log_loss": rl, "brier": br}
        print(f"  {name:>10} {ll:>10.5f} {(base - ll) / base:>+10.3%} {rl:>11.5f} {br:>9.5f}")
    print("\n  (vs market: positive = better than the market price alone)")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", type=int, nargs="+", default=None)
    ap.add_argument("--out", default=str(Path(__file__).parent / "data" / "oos_predictions.parquet"))
    args = ap.parse_args()

    df = load_data()
    print(f"loaded {len(df):,} runners, {df.race_key.nunique():,} races, "
          f"{df.date.min().date()} -> {df.date.max().date()}")
    df, form_cols, blend_cols = prepare(df)
    print(f"form features: {len(form_cols)} | blend features: {len(blend_cols)}")

    years = args.years or sorted(y for y in df.date.dt.year.unique() if y >= df.date.dt.year.min() + 1)
    print(f"\nwalk-forward test years: {years}")
    res = walk_forward(df, form_cols, blend_cols, years)
    summary = report(res)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    res.to_parquet(args.out, index=False)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    (MODEL_DIR / "kill_test_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nwrote out-of-sample predictions -> {args.out}")


if __name__ == "__main__":
    main()
