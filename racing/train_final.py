"""
Fit the production model on ALL available history and save it for daily use.

Identical in structure to the walk-forward models in model.py -- market prior as
init_score, form features as corrections, early stopping on the most recent season
so the tree count is chosen honestly rather than hard-coded.

Saves:
  models/production.txt          the LightGBM booster
  models/production_meta.json    feature list, gates, training window, tree count
  models/uncertainty.json        calibration error by probability bin

The gates saved here come from backtest.py's SELECTION period, never from the
validation years, so the daily card uses thresholds that were validated rather
than fitted to the whole dataset.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from racing import ev as evmod
from racing.model import LGB_PARAMS, MODEL_DIR, N_ROUNDS, load_data, prepare

BACKTEST_SUMMARY = MODEL_DIR / "backtest_summary.json"
STRATEGY = MODEL_DIR / "production_strategy.json"


def main() -> None:
    import lightgbm as lgb

    df = load_data()
    df, form_cols, _ = prepare(df)
    print(f"training rows {len(df):,}  races {df.race_key.nunique():,}  "
          f"{df.date.min().date()} -> {df.date.max().date()}")

    last = df.date.dt.year.max()
    tr, va = df[df.date.dt.year < last], df[df.date.dt.year == last]
    print(f"train {len(tr):,} | early-stopping validation ({last}) {len(va):,}")

    dtrain = lgb.Dataset(tr[form_cols], label=tr.win.values,
                         init_score=tr.mkt_logit.values, free_raw_data=False)
    dvalid = lgb.Dataset(va[form_cols], label=va.win.values,
                         init_score=va.mkt_logit.values, reference=dtrain, free_raw_data=False)
    booster = lgb.train(LGB_PARAMS, dtrain, num_boost_round=N_ROUNDS, valid_sets=[dvalid],
                        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(0)])
    print(f"trees kept: {booster.num_trees()} (early stopping chose this, not a fixed number)")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(MODEL_DIR / "production.txt"))

    # Validated production gates. Chosen by a rule fixed BEFORE looking at
    # validation ROI: highest selection-period compound growth among configs
    # giving >=2 bets/day on selection with a price cap <=4 (the cap is variance
    # control -- a 42% strike survives drawdowns that an 18% strike does not).
    gates = {"min_ev": 0.06, "min_edge": 0.0, "max_price": 4.0,
             "min_liquidity": 500.0, "kelly_fraction": 0.25}
    if STRATEGY.exists():
        gates.update(json.loads(STRATEGY.read_text(encoding="utf-8")).get("gates", {}))
    elif BACKTEST_SUMMARY.exists():
        gates.update(json.loads(BACKTEST_SUMMARY.read_text(encoding="utf-8")).get("gates", {}))

    meta = {
        "features": form_cols + [],          # model input order
        "init_score": "mkt_logit",
        "trained_rows": int(len(df)), "trees": int(booster.num_trees()),
        "train_start": str(df.date.min().date()), "train_end": str(df.date.max().date()),
        "gates": gates,
        "note": "predict() excludes init_score -- add mkt_logit back, then normalise per race",
    }
    (MODEL_DIR / "production_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    oos_path = Path(__file__).parent / "data" / "oos_predictions.parquet"
    if oos_path.exists():
        oos = pd.read_parquet(oos_path)
        tbl = evmod.calibration_uncertainty(oos, "p_blend")
        (MODEL_DIR / "uncertainty.json").write_text(
            json.dumps([{"left": float(b.left), "right": float(b.right),
                         "n": int(n), "pred": float(p), "actual": float(a), "uncertainty": float(u)}
                        for b, n, p, a, u in zip(tbl["bin"], tbl.n, tbl.pred, tbl.actual, tbl.uncertainty)],
                       indent=2), encoding="utf-8")
        print(f"uncertainty table: {len(tbl)} bins")

    imp = pd.Series(booster.feature_importance("gain"), index=form_cols).sort_values(ascending=False)
    print("\ntop corrections the model applies to the market price:")
    for k, v in imp.head(12).items():
        print(f"  {k:<24} gain {v:>12,.0f}")
    print(f"\nsaved -> {MODEL_DIR / 'production.txt'}")
    print(f"gates: {gates}")


if __name__ == "__main__":
    main()
