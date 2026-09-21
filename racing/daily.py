"""
Daily bet card: score every runner on a given day, price the value, propose bets.

    python -m racing.train_final              # fit and save the production model
    python -m racing.daily --date 2026-06-02  # bet card for that day

For each runner it reports the model's probability, the fair odds that implies,
the price actually available, the expected value after commission, the lowest
price still worth taking, a recommended stake, and WHY -- form, jockey/trainer,
course and conditions, and the runner's standing against this particular field.

Prices default to the recorded Betfair BSP for the date (so past days can be
replayed exactly). A live day needs a price source: pass --prices with a CSV of
`horse,price` and the card is rebuilt against those.

"NO BET" is a valid and expected output. The gates are there to be failed.
"""
import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from racing import ev as evmod
from racing.clean import FORM_DB
from racing.ev import Gates
from racing.explain import explain_runners, format_case
from racing.model import MODEL_DIR, sigmoid_norm_by_race


def load_model() -> tuple:
    import lightgbm as lgb
    path = MODEL_DIR / "production.txt"
    if not path.exists():
        raise SystemExit("No production model. Run:  python -m racing.train_final")
    meta = json.loads((MODEL_DIR / "production_meta.json").read_text(encoding="utf-8"))
    return lgb.Booster(model_file=str(path)), meta


def load_day(date: str, prices: Path = None) -> pd.DataFrame:
    con = sqlite3.connect(FORM_DB, timeout=180)
    df = pd.read_sql("""
        select f.*, m.bsp, m.bsp_prob_norm, m.ppwap, m.morningwap, m.pptradedvol, m.book_full
        from features f left join market m using (race_key, horse_key)
        where date(f.date) = ?""", con, params=[date], parse_dates=["date", "race_dt"])
    con.close()
    if prices:
        p = pd.read_csv(prices)
        key = {c.lower(): c for c in p.columns}
        p = p.rename(columns={key.get("horse", "horse"): "horse", key.get("price", "price"): "price"})
        p["_k"] = p.horse.str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
        df["_k"] = df.horse.str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
        df = df.merge(p[["_k", "price"]], on="_k", how="left")
        df["bsp"] = df.price.fillna(df.bsp)
        df = df.drop(columns=["_k", "price"])
    return df


def score_day(df: pd.DataFrame, booster, meta: dict) -> pd.DataFrame:
    cols = meta["features"]
    d = df.copy()
    p = d.bsp_prob_norm
    if p.isna().all():
        raise SystemExit("No market prices for this date -- the blend model needs a price.")
    pc = p.clip(1e-6, 1 - 1e-6)
    d["mkt_logit"] = np.log(pc / (1 - pc))
    d["mkt_rank"] = d.groupby("race_key").bsp_prob_norm.rank(ascending=False)
    for c in cols:
        if c not in d:
            d[c] = np.nan
    X = d[cols]
    # the booster was trained with the market log-odds as init_score, and
    # predict() excludes it -- add it back, then normalise the race to sum to 1.
    correction = booster.predict(X, raw_score=True)
    raw = correction + d.mkt_logit.values
    d["p_model"] = sigmoid_norm_by_race(raw, d.race_key.values)
    d["model_correction"] = correction
    expl = explain_runners(booster, X)
    for c in expl.columns:
        d[f"why_{c}"] = expl[c].values
    return d


def build_card(d: pd.DataFrame, gates: Gates) -> pd.DataFrame:
    scored = evmod.evaluate(d, prob_col="p_model", price_col="bsp", gates=gates)
    return evmod.cap_race_exposure(scored, gates)


def print_card(card: pd.DataFrame, bankroll: float, gates: Gates, top_n: int = 0) -> None:
    day = pd.to_datetime(card.date.iloc[0]).date() if len(card) else "?"
    q = card[card.qualifies].sort_values("ev", ascending=False)
    print("=" * 78)
    print(f"  BET CARD  {day}   bankroll £{bankroll:.2f}")
    print(f"  races {card.race_key.nunique()}   runners {len(card)}   "
          f"qualifying {len(q)}")
    print(f"  gates: EV>={gates.min_ev:.0%}  edge>={gates.min_edge:.0%}  "
          f"liquidity>=£{gates.min_liquidity:.0f}  Kelly x{gates.kelly_fraction}")
    print("=" * 78)

    if q.empty:
        print("\n  NO BET")
        print(f"  Nothing cleared the gates. Most common reason: "
              f"{card.fail_reason.replace('', np.nan).dropna().mode().iat[0] if card.fail_reason.ne('').any() else 'n/a'}")
        near = card.nlargest(5, "ev")[["horse", "course_base", "bsp", "p_model", "ev", "fail_reason"]]
        print("\n  closest to qualifying:")
        for _, r in near.iterrows():
            print(f"    {r.horse[:26]:<26} {r.course_base[:12]:<12} @{r.bsp:>7.2f}  "
                  f"model {r.p_model:>6.1%}  EV {r.ev:>+6.1%}  failed: {r.fail_reason}")
        return

    if top_n:
        q = q.head(top_n)
    for _, r in q.iterrows():
        stake = bankroll * r.stake_frac
        t = pd.to_datetime(r.race_dt).strftime("%H:%M")
        print(f"\n  {'-' * 74}")
        print(f"  {t} {r.course_base}  {r.dist_f:.1f}f {r.type}  (field {int(r.field)})")
        print(f"  >> {r.horse}")
        print(f"     price {r.bsp:>7.2f}   model {r.p_model:>6.1%}   fair odds {r.fair_odds:>6.2f}   "
              f"market {1 / r.bsp:>6.1%}")
        print(f"     EV {r.ev:>+6.1%} after {evmod.COMMISSION:.0%} commission   "
              f"edge {r.edge:>+5.1%}   BET DOWN TO {r.min_odds:.2f}")
        print(f"     stake £{stake:,.2f}  ({r.stake_frac:.2%} of bankroll, 1/4 Kelly)")
        cats = r[[c for c in card.columns if c.startswith("why_") and c != "why_top_drivers"]]
        cats.index = [c[4:] for c in cats.index]
        print(f"     case: {format_case(cats, r.why_top_drivers)}")
    tot = (q.stake_frac * bankroll).sum()
    print(f"\n  {'-' * 74}")
    print(f"  TOTAL staked £{tot:,.2f} ({tot / bankroll:.1%} of bankroll) across {len(q)} bets")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--bankroll", type=float, default=20.0)
    ap.add_argument("--min-ev", type=float, default=None)
    ap.add_argument("--min-edge", type=float, default=None)
    ap.add_argument("--min-liquidity", type=float, default=None)
    ap.add_argument("--kelly", type=float, default=None)
    ap.add_argument("--prices", type=Path, default=None, help="CSV with horse,price for a live day")
    ap.add_argument("--csv", type=Path, default=None, help="also write the full card here")
    args = ap.parse_args()

    booster, meta = load_model()
    g = Gates(**{**meta.get("gates", {}),
                 **{k: v for k, v in (("min_ev", args.min_ev), ("min_edge", args.min_edge),
                                      ("min_liquidity", args.min_liquidity),
                                      ("kelly_fraction", args.kelly)) if v is not None}})
    df = load_day(args.date, args.prices)
    if df.empty:
        raise SystemExit(f"No racing data for {args.date}")
    d = score_day(df, booster, meta)
    card = build_card(d, g)
    print_card(card, args.bankroll, g)
    if args.csv:
        cols = ["date", "race_dt", "course_base", "horse", "bsp", "p_model", "fair_odds", "ev",
                "edge", "min_odds", "stake_frac", "qualifies", "fail_reason"]
        card[cols].to_csv(args.csv, index=False)
        print(f"\nwrote {args.csv}")


if __name__ == "__main__":
    main()
