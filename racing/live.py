"""
Live bet card: today's declarations + today's Exchange prices -> bets.

`daily.py` replays a past day by reading pre-computed features out of the
database. It cannot price today, because today has no rows. This module closes
that gap — it is the piece README s.7.4 names as the only thing standing between
the validated model and daily use.

    python -m racing.live --source racingapi --prices betfair --bankroll 100
    python -m racing.live --card cards/2026-09-22.json --prices prices.csv

The pipeline, and where each step can silently go wrong:

  1. FETCH the card (racing.sources), parsed by the SAME `clean_runs` as training.
  2. RESOLVE each declared runner to its history. `clean.horse_key` is
     `horse|sire|dam`, so any difference in how a feed spells a sire turns a
     proven horse into a first-timer with no ratings and no form. This is the
     single most dangerous failure in the whole pipeline because it is silent:
     the model still returns a probability, just an uninformed one. We therefore
     match deliberately, then REPORT the match rate and refuse to bet runners
     whose ratings history is missing.
  3. BUILD features over history + today, so every as-of feature is computed by
     the same code that produced the training set.
  4. PRICE from the Exchange (or a CSV), normalising the book — the model's prior
     is a normalised probability, and a raw 1/price book sums to ~1.02-1.30
     pre-off, which would manufacture an edge on every runner in the race.
  5. SCORE and gate exactly as `daily.py` does.

Two honest caveats that the card prints rather than hides:
  * the strategy was validated at BSP, and a pre-off back price is a worse-
    informed price (RECON.md s.12). Prefer Betfair's "Take SP" where available.
  * the free/delayed key does not return `totalMatched`, so the liquidity gate
    falls back to visible back-side depth.
"""
import argparse
import json
import sqlite3
import sys
from datetime import date as _date
from pathlib import Path

import numpy as np
import pandas as pd

from racing import sources
from racing.clean import FORM_DB
from racing.daily import build_card, load_model, print_card, score_day
from racing.ev import Gates
from racing.features import build_features
from racing.market import norm_name, resolve_course

# the `runs` columns build_features() reads (the full table exhausts memory)
HIST_COLS = ("race_key, race_id, date, race_dt, course_base, region, surface, type, is_handicap, "
             "class_num, dist_f, going_ord, ran, num, draw, horse, horse_key, age, sex, wgt_lb, hg, "
             "jockey, trainer, prize, or_, rpr, ts, pos_num, finished, win, place3, sp_dec")
# a runner with no rating history has a NaN in the feature that carries the edge
EDGE_FEATURE = "rp_rpr_minus_or"


# ---------------------------------------------------------------- history ---
def load_history(db: Path = FORM_DB, from_year: int = None) -> pd.DataFrame:
    """The training `runs` table, typed and downcast.

    `from_year` trims the history to save memory. Use it with care: the
    jockey/trainer all-time a/e features are cumulative over everything in the
    frame, so trimming changes them, and a model trained on full history then
    sees shifted inputs. Leave it unset unless the machine forces your hand.
    """
    con = sqlite3.connect(db, timeout=180)
    where = f"where date(date) >= '{from_year}-01-01'" if from_year else ""
    runs = pd.read_sql(f"select {HIST_COLS} from runs {where}", con,
                       parse_dates=["date", "race_dt"])
    con.close()
    for c in runs.select_dtypes("float64").columns:
        runs[c] = runs[c].astype("float32")
    return runs


# ------------------------------------------------------------- identities ---
def _birth_year(df: pd.DataFrame) -> pd.Series:
    return df["date"].dt.year - df["age"]


def build_identity_index(hist: pd.DataFrame) -> pd.DataFrame:
    """One row per historical horse: name key, sire/dam keys, implied birth year,
    last run date and run count — everything needed to pick the right horse when
    two share a name."""
    h = hist[["horse_key", "horse", "date", "age"]].copy()
    parts = h.horse_key.str.split("|", n=2, expand=True)
    h["sire_key"] = norm_name(parts[1]) if parts.shape[1] > 1 else ""
    h["dam_key"] = norm_name(parts[2]) if parts.shape[1] > 2 else ""
    h["name_key"] = norm_name(h.horse)
    h["birth_year"] = _birth_year(h)
    g = h.groupby("horse_key", sort=False)
    idx = g.agg(name_key=("name_key", "first"), sire_key=("sire_key", "first"),
                dam_key=("dam_key", "first"), birth_year=("birth_year", "median"),
                last_run=("date", "max"), runs=("date", "size")).reset_index()
    return idx


def resolve_identities(card: pd.DataFrame, idx: pd.DataFrame,
                       card_date: pd.Timestamp = None) -> pd.DataFrame:
    """Map each declared runner onto a historical `horse_key`.

    Order of preference, most to least certain:
      1. the composite key already matches exactly (feed spells sire/dam as the
         archive does)
      2. name + sire + dam all match after normalisation
      3. name + sire match
      4. name is unique in the archive
      5. name is ambiguous -> pick the candidate whose implied birth year matches
         the runner's declared age, tie-broken by most recent run
    Anything left over keeps its own key and runs as a first-timer, which is
    correct for an actual debutant and flagged for everyone else.

    Adds: `resolved_key`, `match` (how it matched), `hist_runs`.
    """
    c = card.copy()
    parts = c.horse_key.str.split("|", n=2, expand=True)
    c["name_key"] = norm_name(c.horse)
    c["sire_key"] = norm_name(parts[1]) if parts.shape[1] > 1 else ""
    c["dam_key"] = norm_name(parts[2]) if parts.shape[1] > 2 else ""
    year = (card_date or c.date.max()).year
    c["birth_year"] = year - c["age"]

    runs_by_key = dict(zip(idx.horse_key, idx.runs))
    # only the names actually declared today — grouping the whole archive builds
    # a quarter of a million one-row frames for nothing
    wanted = set(c.name_key)
    sub = idx[idx.name_key.isin(wanted)]
    by_name: dict[str, pd.DataFrame] = {k: v for k, v in sub.groupby("name_key", sort=False)}

    res, how, nruns = [], [], []
    for r in c.itertuples():
        if r.horse_key in runs_by_key:
            res.append(r.horse_key)
            how.append("exact")
            nruns.append(int(runs_by_key[r.horse_key]))
            continue
        cand = by_name.get(r.name_key)
        if cand is None or cand.empty:
            res.append(r.horse_key)
            how.append("no_history")
            nruns.append(0)
            continue
        pick, label = None, None
        if r.sire_key and r.dam_key:
            m = cand[(cand.sire_key == r.sire_key) & (cand.dam_key == r.dam_key)]
            if len(m) == 1:
                pick, label = m.iloc[0], "name+sire+dam"
        if pick is None and r.sire_key:
            m = cand[cand.sire_key == r.sire_key]
            if len(m) == 1:
                pick, label = m.iloc[0], "name+sire"
        if pick is None and len(cand) == 1:
            pick, label = cand.iloc[0], "name_unique"
        if pick is None:
            m = cand.copy()
            if not np.isnan(r.birth_year):
                m["age_gap"] = (m.birth_year - r.birth_year).abs()
                m = m.sort_values(["age_gap", "last_run"], ascending=[True, False])
            else:
                m = m.sort_values("last_run", ascending=False)
            pick, label = m.iloc[0], "name_ambiguous"
        res.append(pick.horse_key)
        how.append(label)
        nruns.append(int(pick.runs))

    c["resolved_key"] = res
    c["match"] = how
    c["hist_runs"] = nruns
    return c


def identity_report(resolved: pd.DataFrame) -> str:
    counts = resolved["match"].value_counts()
    n = len(resolved)
    lines = [f"  identity: {n} declared runners"]
    for k, v in counts.items():
        lines.append(f"    {k:<16} {v:>4}  ({v / n:.0%})")
    no_hist = (resolved.hist_runs == 0).sum()
    lines.append(f"    with no prior runs in the archive: {no_hist} ({no_hist / n:.0%})")
    return "\n".join(lines)


# ---------------------------------------------------------------- features ---
def as_of_index(hist: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    """Identity index built from runs strictly BEFORE `day`.

    A live archive never holds the future, but building the index as-of anyway
    keeps the ambiguity tie-break ("most recent run wins") and the `hist_runs`
    diagnostic honest under replay, and makes the as-of guarantee uniform across
    every part of the pipeline rather than true only by accident.
    """
    return build_identity_index(hist[hist.date < day] if day is not None else hist)


def live_features(card_runs: pd.DataFrame, hist: pd.DataFrame,
                  card_date: pd.Timestamp = None,
                  resolved: pd.DataFrame = None) -> pd.DataFrame:
    """Append today's card to the history and build features; return today's rows.

    The as-of contract is unchanged: every feature for a card runner is built from
    strictly earlier rows, and today's rows carry win=place3=0 so the
    cumsum-minus-self arithmetic excludes them from their own history.
    """
    day_ts = pd.Timestamp(card_date).normalize() if card_date is not None \
        else card_runs.date.max().normalize()
    if resolved is None:
        resolved = resolve_identities(card_runs, as_of_index(hist, day_ts), day_ts)
    today = card_runs.copy()
    today["horse_key"] = resolved["resolved_key"].values
    for c in ("win", "place3", "finished"):
        today[c] = 0
    for c in ("pos_num", "rpr", "ts", "sp_dec"):
        today[c] = np.nan

    for c in [c for c in hist.columns if c not in today.columns]:
        today[c] = np.nan
    both = pd.concat([hist, today[hist.columns]], ignore_index=True)
    feats = build_features(both)
    out = feats[feats.date.dt.normalize() == day_ts].copy()
    # carry the diagnostics through so the card can report them
    diag = resolved[["race_key", "horse", "match", "hist_runs"]].copy()
    diag["horse_key"] = resolved["resolved_key"].values
    out = out.merge(diag.drop_duplicates(["race_key", "horse_key"]),
                    on=["race_key", "horse_key"], how="left", suffixes=("", "_d"))
    return out.drop(columns=[c for c in out.columns if c.endswith("_d")])


# ------------------------------------------------------------------ prices ---
def attach_prices_csv(feats: pd.DataFrame, path: Path) -> pd.DataFrame:
    """Merge a `horse,price[,liquidity_gbp,course]` CSV onto the card."""
    p = pd.read_csv(path)
    p.columns = [c.strip().lower() for c in p.columns]
    if "horse" not in p or "price" not in p:
        raise SystemExit(f"{path}: need at least `horse` and `price` columns")
    p["name_key"] = norm_name(p.horse)
    d = feats.copy()
    d["name_key"] = norm_name(d.horse)
    keep = ["name_key", "price"] + [c for c in ("liquidity_gbp",) if c in p]
    dup = p.name_key.duplicated().sum()
    if dup:
        print(f"  warning: {dup} duplicate horse names in {path.name}; keeping the first of each")
        p = p.drop_duplicates("name_key")
    d = d.merge(p[keep], on="name_key", how="left")
    return _finalise_prices(d, d["price"], d.get("liquidity_gbp"), "csv")


def attach_prices_betfair(feats: pd.DataFrame, date: str, cert: str = None,
                          key: str = None) -> pd.DataFrame:
    """Fetch live Exchange prices and match them to the card by course + name."""
    from racing import exchange
    s = exchange.login(cert, key)
    px = exchange.live_prices(s, date)
    if px.empty:
        raise SystemExit(f"no UK/IRE WIN markets on the Exchange for {date}")
    px["course_base"] = px.venue.map(lambda v: resolve_course(v) if isinstance(v, str) else None)
    unresolved = px.course_base.isna().sum()
    if unresolved:
        print(f"  warning: {unresolved} Exchange runners at courses we could not name "
              f"({sorted(set(px.loc[px.course_base.isna(), 'venue'].dropna()))[:5]})")
    d = feats.copy()
    d["name_key"] = norm_name(d.horse)
    m = px[["course_base", "name_key", "price", "liquidity_gbp", "liquidity_source"]].dropna(subset=["name_key"])
    m = m.drop_duplicates(["course_base", "name_key"])
    d = d.merge(m, on=["course_base", "name_key"], how="left")
    if d.price.notna().any() and (d.liquidity_source == "back_depth").any():
        print("  note: liquidity is visible back-side depth, not traded volume "
              "(the delayed key withholds totalMatched)")
    return _finalise_prices(d, d["price"], d["liquidity_gbp"], "betfair")


def _finalise_prices(d: pd.DataFrame, price: pd.Series, liquidity, basis: str) -> pd.DataFrame:
    """Set the columns the scorer expects: `bsp`, `bsp_prob_norm`, `pptradedvol`.

    The names are historical (the backtest priced at BSP). The normalisation is
    the part that matters: `bsp_prob_norm` is the model's prior and must be a
    proper within-race probability, not a raw 1/price.
    """
    from racing.exchange import normalise_book
    d = d.copy()
    d["bsp"] = price.values if hasattr(price, "values") else price
    d["price_basis"] = basis
    d["bsp_prob_norm"] = normalise_book(d, price_col="bsp", by="race_key")
    d["book"] = (1.0 / d.bsp).groupby(d.race_key).transform("sum")
    d["pptradedvol"] = liquidity.values if liquidity is not None and hasattr(liquidity, "values") \
        else (np.nan if liquidity is None else liquidity)
    return d


# -------------------------------------------------------------------- card ---
def prize_scale_report(feats: pd.DataFrame, hist: pd.DataFrame) -> str:
    """Is the card's `race_prize` on the same scale as the training data?

    In the archive `prize` is the money each runner WON, so `race_prize` (the sum
    over a race) is the fund actually distributed. A racecard advertises ONE
    race-level figure, which `sources.racecard_to_raw` splits across the field —
    but if a feed advertises only the winner's share, the live `race_prize` lands
    at a fraction of the training scale and the model reads every race as cheaper
    than it is. Nothing crashes; the feature just quietly means something else.
    This prints the ratio so the shift is visible on the first live card.
    """
    if "race_prize" not in feats or feats.race_prize.isna().all():
        return "  race_prize: absent from the card (feature will be missing)"
    h = hist.groupby("race_key").agg(p=("prize", "sum"), cls=("class_num", "first"))
    live = feats.groupby("race_key").agg(p=("race_prize", "first"), cls=("class_num", "first"))
    ratios = []
    for cls, g in live.groupby("cls"):
        ref = h.loc[h.cls == cls, "p"]
        if len(ref) >= 20 and ref.median() > 0:
            ratios.append(g.p.median() / ref.median())
    if not ratios:
        return "  race_prize: no comparable archive races to check the scale against"
    r = float(np.median(ratios))
    verdict = "ok" if 0.5 <= r <= 2.0 else "OUT OF SCALE — check what the feed means by `prize`"
    return f"  race_prize: {r:.2f}x the archive median for the same class ({verdict})"


def prepare(card_raw: pd.DataFrame, hist: pd.DataFrame, card_date=None,
            verbose: bool = True) -> pd.DataFrame:
    card_runs = sources.to_runs(card_raw)
    if card_runs.empty:
        raise SystemExit("the card produced no UK/IRE runners after cleaning — "
                         "check course names and the region whitelists in clean.py")
    day = (pd.Timestamp(card_date) if card_date else card_runs.date.max()).normalize()
    resolved = resolve_identities(card_runs, as_of_index(hist, day), day)
    if verbose:
        print(f"\n  card: {card_runs.race_key.nunique()} races, {len(card_runs)} runners, "
              f"{card_runs.date.min().date()}")
        print(identity_report(resolved))
    feats = live_features(card_runs, hist, day, resolved)
    if verbose:
        cov = feats[EDGE_FEATURE].notna().mean() if EDGE_FEATURE in feats else 0.0
        print(f"  {EDGE_FEATURE}: {cov:.0%} of runners have it "
              f"({'ok' if cov >= 0.7 else 'LOW — the edge feature is missing for most of the card'})")
        print(prize_scale_report(feats, hist))
    return feats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_argument_group("card source")
    src.add_argument("--source", choices=["racingapi", "file"], default="file")
    src.add_argument("--card", type=Path, help="racecard JSON or CSV (with --source file)")
    src.add_argument("--day", default="today", help="today | tomorrow (with --source racingapi)")
    src.add_argument("--plan", default="free", choices=["free", "basic", "standard", "pro"])
    px = ap.add_argument_group("prices")
    px.add_argument("--prices", default="betfair",
                    help="'betfair' for live Exchange prices, or a path to a horse,price CSV")
    px.add_argument("--cert", help="Betfair client cert (.crt) for the bot login")
    px.add_argument("--key", help="Betfair client cert key (.key)")
    ap.add_argument("--bankroll", type=float, default=100.0)
    ap.add_argument("--history-from", type=int, default=None,
                    help="trim history to this year to save memory (changes jockey/trainer aggregates)")
    ap.add_argument("--min-ev", type=float, default=None)
    ap.add_argument("--max-price", type=float, default=None)
    ap.add_argument("--min-liquidity", type=float, default=None)
    ap.add_argument("--require-edge-feature", action=argparse.BooleanOptionalAction, default=True,
                    help=f"refuse to bet runners with no {EDGE_FEATURE}. On by default: that "
                         f"feature carries 100%% of the measured edge, so a runner missing it is "
                         f"being bet on no measured edge at all. --no-require-edge-feature to allow.")
    ap.add_argument("--csv", type=Path, help="also write the full card here")
    ap.add_argument("--dump-card", type=Path, help="write the fetched racecard JSON here")
    args = ap.parse_args()

    # ---- the card
    if args.source == "racingapi":
        from racing.racingapi import RacingAPI
        races = RacingAPI().racecards(day=args.day, plan=args.plan)
        if args.dump_card:
            args.dump_card.parent.mkdir(parents=True, exist_ok=True)
            args.dump_card.write_text(json.dumps(races, indent=1), encoding="utf-8")
        card_raw = sources.racecard_to_raw(races)
    else:
        if not args.card:
            ap.error("--source file needs --card")
        card_raw = sources.card_from_file(args.card)
    if card_raw.empty:
        raise SystemExit("the feed returned no runners")

    booster, meta = load_model()
    if not FORM_DB.exists():
        raise SystemExit(f"no form database at {FORM_DB} — run racing.clean first")
    hist = load_history(from_year=args.history_from)
    print(f"  history: {len(hist):,} runs to {hist.date.max().date()}")
    stale = (pd.Timestamp(_date.today()) - hist.date.max()).days
    if stale > 7:
        print(f"  WARNING: the form archive is {stale} days stale. Every horse that has run "
              f"since is missing its latest RPR, which is the feature carrying the edge.\n"
              f"           Backfill with:  python -m racing.backfill "
              f"--from {(hist.date.max() + pd.Timedelta(days=1)).date()}")

    feats = prepare(card_raw, hist)

    # ---- prices
    day = pd.Timestamp(feats.date.max()).strftime("%Y-%m-%d")
    if str(args.prices).lower() == "betfair":
        d = attach_prices_betfair(feats, day, args.cert, args.key)
    else:
        d = attach_prices_csv(feats, Path(args.prices))
    have = d.bsp.notna().mean()
    print(f"  prices: {have:.0%} of runners priced; "
          f"{d.bsp_prob_norm.notna().sum()} in fully-priced races")
    if d.bsp_prob_norm.notna().sum() == 0:
        raise SystemExit("no race has a complete price book — cannot normalise, cannot score.")

    if args.require_edge_feature and EDGE_FEATURE in d:
        before = len(d)
        d = d[d[EDGE_FEATURE].notna()]
        n = before - len(d)
        print(f"  --require-edge-feature dropped {n} runner{'' if n == 1 else 's'} "
              f"with no rating history")

    d = d[d.bsp_prob_norm.notna()].copy()
    g = Gates(**{**meta.get("gates", {}),
                 **{k: v for k, v in (("min_ev", args.min_ev), ("max_price", args.max_price),
                                      ("min_liquidity", args.min_liquidity)) if v is not None}})
    scored = score_day(d, booster, meta)
    card = build_card(scored, g)
    print_card(card, args.bankroll, g)
    print(f"\n  priced from: {d.price_basis.iloc[0]} (the strategy was validated at BSP — "
          f"prefer 'Take SP' where the market offers it)")
    if args.csv:
        card.to_csv(args.csv, index=False)
        print(f"  wrote {args.csv}")


if __name__ == "__main__":
    sys.exit(main())
