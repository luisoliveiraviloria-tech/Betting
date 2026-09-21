"""
Bet simulation on out-of-sample predictions -- the test that decides whether this
system is allowed to bet at all.

Honesty rules baked in
----------------------
1. Predictions come from model.py's walk-forward: every prediction was made by a
   model that never saw that year.
2. Thresholds are chosen on a SELECTION period and then applied unchanged to a
   later VALIDATION period. A strategy tuned and scored on the same years proves
   nothing.
3. Bets settle at BSP, a price you can actually get (you can place "at SP" in
   advance), so the simulation is achievable rather than theoretical.
4. Commission is charged per winning bet at 5%.
5. Betfair's £2 minimum stake is enforced. On a £20 bank that makes every bet
   >=10% of the roll, which is a ruin problem no edge can fix -- so ruin
   probability is reported explicitly rather than hidden behind a nice ROI.

Configurations are ranked by COMPOUND LOG GROWTH, not raw ROI. Raw ROI favours a
handful of longshot winners; log growth is what actually compounds a bankroll and
it punishes the variance that busts small banks.

A negative result here is a real result. It means: do not bet.
"""
import argparse
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from racing import ev as evmod
from racing.ev import Gates

MIN_STAKE = 2.0


def flat_roi(bets: pd.DataFrame, commission: float = evmod.COMMISSION) -> tuple[float, float]:
    """Flat-stake ROI and its standard error -- the cleanest measure of edge."""
    if not len(bets):
        return float("nan"), float("nan")
    r = np.where(bets.win == 1, (bets.bsp - 1.0) * (1.0 - commission), -1.0)
    return float(r.mean()), float(r.std(ddof=1) / np.sqrt(len(r)))


def log_growth(bets: pd.DataFrame, commission: float = evmod.COMMISSION) -> float:
    """Expected log growth per bet at the bet's own Kelly stake fraction."""
    if not len(bets):
        return float("nan")
    f = np.clip(bets.stake_frac.values, 0, 0.5)
    r = np.where(bets.win == 1, (bets.bsp.values - 1.0) * (1.0 - commission), -1.0)
    return float(np.mean(np.log1p(np.clip(f * r, -0.999, None))))


def ruin_probability(bets: pd.DataFrame, bankroll: float, stake: float, n_sims: int = 2000,
                     horizon: int = 400, commission: float = evmod.COMMISSION,
                     seed: int = 0) -> float:
    """Monte Carlo chance of being wiped out, resampling the real bet outcomes."""
    if not len(bets):
        return float("nan")
    rng = np.random.default_rng(seed)
    r = np.where(bets.win == 1, (bets.bsp.values - 1.0) * (1.0 - commission), -1.0)
    n = min(len(r), horizon)
    draws = rng.choice(r, size=(n_sims, n), replace=True) * stake
    paths = bankroll + np.cumsum(draws, axis=1)
    return float((paths.min(axis=1) < stake).mean())


def simulate(bets: pd.DataFrame, bankroll: float = 20.0, staking: str = "kelly",
             flat_stake: float = 2.0, commission: float = evmod.COMMISSION,
             min_stake: float = MIN_STAKE) -> dict:
    """Walk bets in time order, compounding the bankroll."""
    b = bets.sort_values(["date", "race_key"], kind="mergesort")
    bank = peak = bankroll
    staked_l, pnl_l, skipped = [], [], 0
    max_dd, streak, worst_streak = 0.0, 0, 0

    for row in b.itertuples():
        stake = bank * row.stake_frac if staking == "kelly" else flat_stake
        stake = float(np.floor(stake * 100) / 100)
        if stake < min_stake or stake > bank:
            skipped += 1
            continue
        if row.win == 1:
            pnl, streak = stake * (row.bsp - 1.0) * (1.0 - commission), 0
        else:
            pnl = -stake
            streak += 1
            worst_streak = max(worst_streak, streak)
        bank += pnl
        peak = max(peak, bank)
        max_dd = max(max_dd, (peak - bank) / peak if peak > 0 else 0.0)
        staked_l.append(stake)
        pnl_l.append(pnl)
        if bank < min_stake:
            break

    placed = len(staked_l)
    total_staked = float(np.sum(staked_l)) if placed else 0.0
    total_pnl = float(np.sum(pnl_l)) if placed else 0.0
    return {
        "bets_qualified": len(b), "bets_placed": placed, "skipped_min_stake": skipped,
        "strike": float(sum(1 for p in pnl_l if p > 0) / placed) if placed else float("nan"),
        "total_staked": total_staked, "pnl": total_pnl,
        "roi": total_pnl / total_staked if total_staked else float("nan"),
        "final_bankroll": bank, "max_drawdown": max_dd,
        "longest_losing_streak": worst_streak, "busted": bank < min_stake,
    }


def build_bets(oos: pd.DataFrame, gates: Gates, prob_col: str = "p_blend",
               unc_table: pd.DataFrame = None) -> pd.DataFrame:
    unc = evmod.attach_uncertainty(oos, prob_col, unc_table) if unc_table is not None else None
    d = evmod.evaluate(oos, prob_col=prob_col, gates=gates, uncertainty=unc)
    return evmod.cap_race_exposure(d[d.qualifies].copy(), gates)


def sweep(oos: pd.DataFrame, prob_col: str, min_evs, min_edges, max_prices,
          unc_table=None, min_bets: int = 400) -> pd.DataFrame:
    rows = []
    for mev, medge, mx in itertools.product(min_evs, min_edges, max_prices):
        g = Gates(min_ev=mev, min_edge=medge, max_price=mx, min_liquidity=0.0)
        bets = build_bets(oos, g, prob_col, unc_table)
        if len(bets) < min_bets:
            continue
        roi, se = flat_roi(bets)
        rows.append({"min_ev": mev, "min_edge": medge, "max_price": mx, "n": len(bets),
                     "strike": bets.win.mean(), "mean_bsp": bets.bsp.mean(),
                     "flat_roi": roi, "roi_se": se, "t": roi / se if se else np.nan,
                     "growth": log_growth(bets)})
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("growth", ascending=False).reset_index(drop=True)


def describe_bets(bets: pd.DataFrame, label: str) -> dict:
    roi, se = flat_roi(bets)
    model, market, actual = bets.p_blend.mean(), bets.p_market.mean(), bets.win.mean()
    print(f"  {label}: n={len(bets):,}  strike {actual:.3f}  mean BSP {bets.bsp.mean():.2f}")
    print(f"    flat ROI {roi:+.2%} +/- {1.96 * se:.2%} (95% CI)   t={roi / se if se else np.nan:.2f}")
    print(f"    model says {model:.3f} | market says {market:.3f} | ACTUAL {actual:.3f}"
          f"  -> market under-prices these by {actual - market:+.3f}")
    return {"n": int(len(bets)), "roi": roi, "roi_se": se, "strike": float(actual),
            "model_prob": float(model), "market_prob": float(market)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--oos", default=str(Path(__file__).parent / "data" / "oos_predictions.parquet"))
    ap.add_argument("--prob-col", default="p_blend")
    ap.add_argument("--split-year", type=int, default=2024)
    args = ap.parse_args()

    oos = pd.read_parquet(args.oos)
    oos["date"] = pd.to_datetime(oos.date)
    sel, val = oos[oos.year < args.split_year], oos[oos.year >= args.split_year]
    print(f"out-of-sample rows {len(oos):,}")
    print(f"selection  {sorted(sel.year.unique())}  ({len(sel):,} rows)")
    print(f"validation {sorted(val.year.unique())}  ({len(val):,} rows)")

    unc = evmod.calibration_uncertainty(sel, args.prob_col)

    print("\n--- 1. Threshold search on the SELECTION years only ---")
    sw = sweep(sel, args.prob_col,
               min_evs=[0.0, 0.02, 0.05, 0.10, 0.15],
               min_edges=[0.0, 0.01, 0.02, 0.03, 0.05],
               max_prices=[3.0, 4.0, 6.0, 10.0, 1000.0], unc_table=unc)
    if sw.empty:
        print("  nothing produced enough bets -> NO STRATEGY")
        return
    print(f"  {'min_ev':>7} {'min_edge':>9} {'max_px':>7} {'n':>7} {'strike':>7} {'meanBSP':>8} "
          f"{'flat ROI':>10} {'t':>6} {'growth/bet':>11}")
    for _, r in sw.head(10).iterrows():
        print(f"  {r.min_ev:>7.2f} {r.min_edge:>9.2f} {r.max_price:>7.0f} {r.n:>7,.0f} "
              f"{r.strike:>7.3f} {r.mean_bsp:>8.2f} {r.flat_roi:>+9.2%} {r.t:>6.2f} {r.growth:>+11.5f}")
    best = sw.iloc[0]
    print("\n  chosen by COMPOUND GROWTH (raw ROI favours longshot flukes that bust a bank):")
    print(f"    min_ev={best.min_ev:.2f}  min_edge={best.min_edge:.2f}  max_price={best.max_price:.0f}")
    print(f"  best of {len(sw)} combinations -> upward-biased. Validation below is the real test.")

    g = Gates(min_ev=float(best.min_ev), min_edge=float(best.min_edge),
              max_price=float(best.max_price), min_liquidity=0.0)

    print("\n--- 2. Those exact thresholds on the UNSEEN validation years ---")
    sb = build_bets(sel, g, args.prob_col, unc)
    vb = build_bets(val, g, args.prob_col, unc)
    describe_bets(sb, "selection (in-sample, for reference)")
    print()
    vstats = describe_bets(vb, "VALIDATION (out-of-sample)")
    roi, se = flat_roi(vb)
    verdict = ("POSITIVE EDGE (95% CI excludes zero)" if roi - 1.96 * se > 0
               else "positive but not significant" if roi > 0 else "NEGATIVE -- do not bet")
    print(f"\n  VERDICT: {verdict}")

    print("\n  by validation year:")
    for y, grp in vb.groupby("year"):
        r, s = flat_roi(grp)
        print(f"    {y}: n={len(grp):>5,}  strike {grp.win.mean():.3f}  "
              f"ROI {r:>+7.2%} +/-{1.96 * s:.2%}")

    print("\n--- 3. Will a real bankroll survive it? ---")
    print(f"  Betfair's minimum stake is £{MIN_STAKE:.0f}. On a £20 bank that is >=10% per bet,")
    print("  so ruin is a STAKE-SIZE problem that no edge can fix.")
    print(f"\n  {'bank':>8} {'stake':>7} {'stake%':>7} {'P(ruin)':>9} {'final':>12} {'maxDD':>7} {'worst run':>10}")
    bank_rows = []
    for bank in (20.0, 50.0, 100.0, 250.0, 500.0, 1000.0):
        stake = max(MIN_STAKE, bank * 0.02)
        pr = ruin_probability(vb, bank, stake)
        sim = simulate(vb, bankroll=bank, staking="flat", flat_stake=stake)
        bank_rows.append({"bankroll": bank, "stake": stake, "p_ruin": pr,
                          "final": sim["final_bankroll"], "busted": sim["busted"]})
        print(f"  £{bank:>7,.0f} £{stake:>6.2f} {stake / bank:>6.1%} {pr:>9.1%} "
              f"£{sim['final_bankroll']:>11,.2f} {sim['max_drawdown']:>6.1%} "
              f"{sim['longest_losing_streak']:>10}{'  BUST' if sim['busted'] else ''}")

    print("\n  compounding 1/4-Kelly (the intended long-run mode):")
    for bank in (100.0, 500.0, 2000.0):
        sim = simulate(vb, bankroll=bank, staking="kelly")
        if sim["bets_placed"]:
            print(f"    £{bank:>6,.0f} -> £{sim['final_bankroll']:>11,.2f}  "
                  f"placed {sim['bets_placed']:,}/{sim['bets_qualified']:,}  "
                  f"ROI {sim['roi']:+.2%}  maxDD {sim['max_drawdown']:.1%}"
                  f"{'  BUST' if sim['busted'] else ''}")
        else:
            print(f"    £{bank:>6,.0f} -> no bet clears the £{MIN_STAKE:.0f} minimum at 1/4 Kelly")

    out = Path(__file__).parent / "models"
    out.mkdir(parents=True, exist_ok=True)
    (out / "backtest_summary.json").write_text(json.dumps({
        "prob_col": args.prob_col, "split_year": int(args.split_year),
        "gates": {"min_ev": float(best.min_ev), "min_edge": float(best.min_edge),
                  "max_price": float(best.max_price)},
        "selection": {"n": int(best.n), "roi": float(best.flat_roi), "t": float(best.t)},
        "validation": vstats, "verdict": verdict, "bankroll": bank_rows,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {out / 'backtest_summary.json'}")


if __name__ == "__main__":
    main()
