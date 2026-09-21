"""
Turn a LightGBM prediction into a human-readable case for (or against) a horse.

Uses SHAP contributions, which decompose a single prediction additively:

    raw_score = base_value + sum(contribution of every feature)

Contributions are then summed into the categories the decision actually cares
about, so a runner's case reads as "form +0.42, jockey/trainer +0.11,
course & conditions -0.05, field quality +0.20" rather than 74 numbers.

Units are log-odds of the raw score, before the within-race softmax. Positive
means the feature pushed this runner's chance UP relative to the model's base
rate. They are not probabilities and do not sum to one.
"""
import numpy as np
import pandas as pd

# order matters: first matching rule wins
CATEGORY_RULES = [
    ("Market",            lambda c: c.startswith("mkt_")),
    ("Recent form",       lambda c: c.startswith(("h_last", "h_finpct", "h_nonfinish", "h_ae", "h_place_ae",
                                                  "h_win_prior", "h_n_prior", "finpct3_rank")) or c == "days_since"),
    ("Ratings",           lambda c: c.startswith(("or_", "rp_"))),
    ("Jockey & trainer",  lambda c: c.startswith(("j_", "t_", "tj_"))),
    ("Course & going",    lambda c: c.startswith(("h_course", "h_going", "h_surface", "h_dist", "h_type"))
                                     or c in ("going_ord", "dist_f", "surface", "is_aw", "is_jumps", "is_ire",
                                              "dist_change", "draw_pct")),
    ("Class & weight",    lambda c: c in ("class_num", "class_change", "wgt_lb", "wgt_rel", "wgt_change",
                                          "prize", "is_handicap", "hg_first", "age", "age_rel", "is_female")),
    ("Field / rivals",    lambda c: c.endswith(("_rel", "_rank")) or c == "field"),
]
READABLE = {
    "h_ae": "career win rate vs expectation", "h_place_ae": "career place rate vs expectation",
    "h_finpct3": "average finish, last 3 runs", "h_finpct5": "average finish, last 5 runs",
    "h_last_finpct": "last run's finishing position", "days_since": "days since last run",
    "or_": "official rating", "or_rel": "official rating vs this field",
    "or_rank": "official rating rank in field", "or_change": "change in official rating",
    "rp_rpr_last": "Racing Post rating, last run", "rp_rpr_avg3": "Racing Post rating, last 3",
    "rp_rpr_rel": "Racing Post rating vs this field", "rp_rpr_minus_or": "RPR above official mark",
    "j_ae": "jockey strike rate vs expectation", "t_ae": "trainer strike rate vs expectation",
    "tj_ae": "trainer/jockey combination", "t_r14_ae": "trainer form, last 14 days",
    "j_r14_ae": "jockey form, last 14 days", "t_r30_ae": "trainer form, last 30 days",
    "h_course_ae": "record at this course", "h_dist_ae": "record at this distance",
    "h_going_ae": "record on this going", "h_surface_ae": "record on this surface",
    "h_type_ae": "record in this race type", "field": "field size",
    "mkt_logit": "market price", "mkt_rank": "market rank in field",
    "j_n": "jockey's career rides", "t_n": "trainer's career runners", "tj_n": "combo runs together",
    "h_n_prior": "career runs", "h_win_prior": "career wins", "h_nonfinish5": "recent non-finishes",
    "j_r30_ae": "jockey form, last 30 days", "t_r30_ae": "trainer form, last 30 days",
    "h_course_n": "runs at this course", "h_dist_n": "runs at this distance",
    "h_going_n": "runs on this going", "h_surface_n": "runs on this surface",
    "h_type_n": "runs in this race type", "race_prize": "race prize fund",
    "class_num": "race class", "dist_f": "race distance", "going_ord": "going (firm->heavy)",
    "is_handicap": "handicap race", "age_rel": "age vs this field", "wgt_lb": "weight carried",
    "or_last": "last official rating", "rp_ts_last": "Topspeed, last run",
    "rp_rpr_max3": "best Racing Post rating, last 3", "hg_first": "headgear first time",
    "is_first_run": "first career run", "finpct3_rank": "recent-form rank in field",
    "h_ae_rel": "career record vs this field", "h_ae_rank": "career record rank in field",
    "wgt_rel": "weight carried vs this field", "dist_change": "distance change from last run",
    "class_change": "class change from last run", "draw_pct": "draw position",
}


def categorise(col: str) -> str:
    for name, rule in CATEGORY_RULES:
        if rule(col):
            return name
    return "Other"


def shap_frame(booster, X: pd.DataFrame) -> pd.DataFrame:
    """SHAP contributions, one column per feature (drops the trailing base value)."""
    raw = booster.predict(X, pred_contrib=True)
    return pd.DataFrame(raw[:, :-1], columns=list(X.columns), index=X.index), raw[:, -1]


def category_breakdown(contrib: pd.DataFrame) -> pd.DataFrame:
    cats = pd.Series({c: categorise(c) for c in contrib.columns})
    return contrib.T.groupby(cats).sum().T


def top_drivers(contrib_row: pd.Series, n: int = 4) -> list[tuple[str, float]]:
    s = contrib_row.reindex(contrib_row.abs().sort_values(ascending=False).index).head(n)
    return [(READABLE.get(k, k.replace("_", " ")), float(v)) for k, v in s.items()]


def explain_runners(booster, X: pd.DataFrame, n_drivers: int = 4) -> pd.DataFrame:
    contrib, base = shap_frame(booster, X)
    cats = category_breakdown(contrib)
    out = cats.copy()
    out["_base"] = base
    out["top_drivers"] = [top_drivers(contrib.iloc[i], n_drivers) for i in range(len(contrib))]
    return out


def format_case(cat_row: pd.Series, drivers: list[tuple[str, float]]) -> str:
    cats = {k: v for k, v in cat_row.items() if not k.startswith("_") and k != "top_drivers"}
    parts = [f"{k} {v:+.2f}" for k, v in sorted(cats.items(), key=lambda kv: -abs(kv[1])) if abs(v) >= 0.01]
    line = "; ".join(parts[:4])
    drv = "; ".join(f"{name} ({v:+.2f})" for name, v in drivers)
    return f"{line}\n      drivers: {drv}"
