"""
Walk-forward backtest: train on seasons [start .. Y], evaluate out-of-sample
on season Y+1, roll forward one season at a time. No look-ahead: a model
is never evaluated on a season it (or any later season) was trained on.

Reports, per model and for the market benchmark:
  - Brier score       (mean squared error of the 3-way probability vector)
  - log loss
  - calibration table (predicted-probability decile vs realised frequency)
  - edge-bucket table (model edge vs market -> realised ROI), the actual
    arbiter of whether raw model edge translates into real-world profit.

Usage:
    quant/.venv/bin/python -m quant.backtest
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant.data import load_matches, load_market
from quant.market import implied_probabilities
from quant.models.dixon_coles import DixonColesModel
from quant.models.market_prior_blend import blend, fit_weight
from quant.models.poisson import PoissonModel

RESULT_TO_IDX = {"H": 0, "D": 1, "A": 2}


def brier_score(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """probs: (n,3) predicted [home,draw,away]; outcomes: (n,) in {0,1,2}."""
    onehot = np.eye(3)[outcomes]
    return float(np.mean(np.sum((probs - onehot) ** 2, axis=1)))


def log_loss(probs: np.ndarray, outcomes: np.ndarray, eps: float = 1e-15) -> float:
    p_true = np.clip(probs[np.arange(len(outcomes)), outcomes], eps, 1 - eps)
    return float(-np.mean(np.log(p_true)))


def calibration_table(probs: np.ndarray, outcomes: np.ndarray, outcome_idx: int, n_bins: int = 10) -> pd.DataFrame:
    p = probs[:, outcome_idx]
    hit = (outcomes == outcome_idx).astype(float)
    bins = pd.qcut(p, q=min(n_bins, len(np.unique(p))), duplicates="drop")
    df = pd.DataFrame({"bin": bins, "predicted": p, "hit": hit})
    return df.groupby("bin", observed=True).agg(
        n=("hit", "size"), predicted_mean=("predicted", "mean"), realised_freq=("hit", "mean")
    )


def edge_bucket_table(model_probs: np.ndarray, market_probs: np.ndarray, odds: np.ndarray, outcomes: np.ndarray, commission: float = 0.05) -> pd.DataFrame:
    """
    For every (match, outcome) pair, computes model edge = model_p - market_p
    and, if a bet had been placed on that outcome at the given decimal odds,
    the realised profit net of Betfair Exchange commission (charged on net
    winnings only, not on losing stakes -- Betfair does not charge
    commission on a loss). Buckets by edge and reports win rate / ROI per
    bucket. odds: (n,3) decimal odds aligned to [home,draw,away].

    commission: Betfair Exchange standard/Basic-plan commission rate on
    net market winnings. 0.05 (5%) is Betfair's long-standing headline
    rate; VERIFY the account's actual current rate before relying on this
    for real staking decisions -- not fetched/confirmed live here.
    """
    n = model_probs.shape[0]
    rows = []
    for i in range(n):
        for j in range(3):
            edge = model_probs[i, j] - market_probs[i, j]
            won = 1 if outcomes[i] == j else 0
            gross_profit = (odds[i, j] - 1) if won else -1
            net_profit = gross_profit * (1 - commission) if won else gross_profit
            rows.append({"edge": edge, "won": won, "profit": net_profit})
    df = pd.DataFrame(rows)

    bucket_edges = [-1.0, 0.0, 0.02, 0.05, 0.10, 1.0]
    labels = ["<0%", "0-2%", "2-5%", "5-10%", "10%+"]
    df["bucket"] = pd.cut(df["edge"], bins=bucket_edges, labels=labels)

    return df.groupby("bucket", observed=True).agg(
        n_bets=("won", "size"),
        win_pct=("won", "mean"),
        roi=("profit", "mean"),
        total_profit=("profit", "sum"),
    )


@dataclass
class WalkForwardResult:
    season: int
    model_name: str
    brier: float
    log_loss: float
    n_matches: int


def run_walk_forward(start_train_end: int = 2016, end_season: int = 2025, min_train_seasons: int = 4, exclude_seasons: tuple[int, ...] = ()) -> tuple[list[WalkForwardResult], pd.DataFrame]:
    matches = load_matches()
    matches = matches[matches["season"] <= end_season]
    if exclude_seasons:
        matches = matches[~matches["season"].isin(exclude_seasons)]
    market = load_market(venue="bookmaker_avg", snapshot="closing")
    market_pre = load_market(venue="bookmaker_avg", snapshot="pre_close")
    market = market if not market.empty else market_pre
    market_indexed = market.set_index("match_id")

    results: list[WalkForwardResult] = []
    all_edge_rows = []
    season_cache: dict[int, dict] = {}  # season -> {dixon_coles_probs, market_probs, outcomes, odds}

    seasons = sorted(matches["season"].unique())
    seasons = [s for s in seasons if s >= start_train_end]

    for test_season in seasons:
        train = matches[matches["season"] < test_season]
        test = matches[matches["season"] == test_season]
        if train["season"].nunique() < min_train_seasons or test.empty:
            continue

        # only evaluate matches that have a market odds row (needed for edge/ROI comparison)
        test = test[test["match_id"].isin(market_indexed.index)]
        if test.empty:
            continue

        for name, model in [("poisson", PoissonModel()), ("dixon_coles", DixonColesModel(xi=0.003))]:
            model.fit(train)

            probs, outcomes, odds_rows = [], [], []
            for _, row in test.iterrows():
                try:
                    pred = model.match_probabilities(row["home_team"], row["away_team"])
                except KeyError:
                    continue  # team unseen in training window (e.g. newly promoted) -> skip, don't guess
                probs.append([pred["home_win"], pred["draw"], pred["away_win"]])
                outcomes.append(RESULT_TO_IDX[row["result"]])
                mkt = market_indexed.loc[row["match_id"]]
                odds_rows.append([mkt["odds_home"], mkt["odds_draw"], mkt["odds_away"]])

            if not probs:
                continue

            probs = np.array(probs)
            outcomes = np.array(outcomes)
            odds_arr = np.array(odds_rows)
            market_probs = np.array([
                list(implied_probabilities(*row, method="shin").values()) for row in odds_arr
            ])

            results.append(WalkForwardResult(
                season=test_season, model_name=name,
                brier=brier_score(probs, outcomes),
                log_loss=log_loss(probs, outcomes),
                n_matches=len(outcomes),
            ))

            edges = edge_bucket_table(probs, market_probs, odds_arr, outcomes)
            edges["season"] = test_season
            edges["model"] = name
            all_edge_rows.append(edges)

            if name == "dixon_coles":
                season_cache[test_season] = {
                    "model_probs": probs, "market_probs": market_probs,
                    "outcomes": outcomes, "odds": odds_arr,
                }

        # market-prior blend: weight w fit on the PREVIOUS season only (nested
        # validation -- never the test season, never a future season), then
        # applied out-of-sample to this season. Needs >=2 prior cached seasons
        # (one to search a stable w on, effectively) so skip the first one.
        prior_seasons = sorted(s for s in season_cache if s < test_season)
        if test_season in season_cache and prior_seasons:
            tune_season = prior_seasons[-1]
            tune = season_cache[tune_season]
            best_w, _ = fit_weight(tune["model_probs"], tune["market_probs"], tune["outcomes"])

            cur = season_cache[test_season]
            blended_probs = blend(cur["model_probs"], cur["market_probs"], best_w)

            results.append(WalkForwardResult(
                season=test_season, model_name=f"market_prior_blend(w={best_w:.2f})",
                brier=brier_score(blended_probs, cur["outcomes"]),
                log_loss=log_loss(blended_probs, cur["outcomes"]),
                n_matches=len(cur["outcomes"]),
            ))
            edges = edge_bucket_table(blended_probs, cur["market_probs"], cur["odds"], cur["outcomes"])
            edges["season"] = test_season
            edges["model"] = "market_prior_blend"
            all_edge_rows.append(edges)

        # market benchmark itself (Brier/log-loss of just using de-vigged odds)
        odds_rows_mkt, outcomes_mkt = [], []
        for _, row in test.iterrows():
            mkt = market_indexed.loc[row["match_id"]]
            odds_rows_mkt.append([mkt["odds_home"], mkt["odds_draw"], mkt["odds_away"]])
            outcomes_mkt.append(RESULT_TO_IDX[row["result"]])
        market_probs_only = np.array([
            list(implied_probabilities(*r, method="shin").values()) for r in odds_rows_mkt
        ])
        results.append(WalkForwardResult(
            season=test_season, model_name="market_devigged",
            brier=brier_score(market_probs_only, np.array(outcomes_mkt)),
            log_loss=log_loss(market_probs_only, np.array(outcomes_mkt)),
            n_matches=len(outcomes_mkt),
        ))

    edge_df = pd.concat(all_edge_rows) if all_edge_rows else pd.DataFrame()
    return results, edge_df


def summarise(results: list[WalkForwardResult]) -> pd.DataFrame:
    df = pd.DataFrame([r.__dict__ for r in results])
    weighted = df.groupby("model_name").apply(
        lambda g: pd.Series({
            "n_matches": g["n_matches"].sum(),
            "brier": np.average(g["brier"], weights=g["n_matches"]),
            "log_loss": np.average(g["log_loss"], weights=g["n_matches"]),
        }),
        include_groups=False,
    )
    return weighted


def pooled_edge_table(edge_df: pd.DataFrame, model_name: str) -> pd.DataFrame:
    sub = edge_df[edge_df["model"] == model_name]
    pooled = sub.groupby("bucket", observed=True).agg(
        n_bets=("n_bets", "sum"), total_profit=("total_profit", "sum")
    )
    pooled["roi_net_commission"] = pooled["total_profit"] / pooled["n_bets"]
    return pooled


if __name__ == "__main__":
    import sys

    covid_seasons = (2019, 2020)  # 2019-20 (behind closed doors from Mar 2020) and 2020-21 (fully behind closed doors)

    for label, exclude in [("full 2012-2025", ()), ("excluding COVID seasons 2019-20/2020-21", covid_seasons)]:
        print(f"\n{'='*70}\nRUN: {label}\n{'='*70}")
        results, edge_df = run_walk_forward(exclude_seasons=exclude)

        print("\n--- Weighted summary ---")
        print(summarise(results).to_string())

        if not edge_df.empty:
            for model_name in ["poisson", "dixon_coles", "market_prior_blend"]:
                if model_name in edge_df["model"].unique():
                    print(f"\n--- Edge-bucket table, net of 5% commission ({model_name}) ---")
                    print(pooled_edge_table(edge_df, model_name).to_string())

    if "--full" in sys.argv:
        results, edge_df = run_walk_forward()
        print("\n=== Per-season results (full run) ===")
        print(pd.DataFrame([r.__dict__ for r in results]).to_string(index=False))
