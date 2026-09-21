"""
Join Betfair BSP prices (racing.db, from bsp_data.py) to the form features
(form.db, from features.py) and write a `market` table.

Join contract
-------------
The BSP file published on date D contains races run on D-1 (confirmed live:
the file_date - race_date lag is exactly 1 for every row checked). The race
date/time in `event_dt` is therefore the truth; `file_date` is never used as a
race date.

Runners are matched on (race date, normalised horse name). Horse names carry a
country suffix in the form data ("Definitly Red (IRE)") but not on Betfair
("Definitly Red"), so the suffix is stripped and both sides are reduced to
lowercase alphanumerics. Stripping the suffix can in principle collide two
different horses of the same base name, so every ambiguous match is resolved by
course, then by race-time proximity, and anything still ambiguous is DROPPED
rather than guessed (counted in the report).

Prices
------
  bsp        the settled starting price (what a bet "at SP" is matched at)
  ppwap      pre-play weighted average price
  morningwap weighted average price that morning
`bsp >= 1000` means nobody backed at SP -- a placeholder, not a price, so it is
nulled. `bsp_prob` is the raw implied probability (1/bsp); `bsp_prob_norm`
normalises the race book to sum to 1, and is only set where our joined runners
cover the whole field (otherwise normalising against a partial book is wrong).
"""
import argparse
import re
import sqlite3

import numpy as np
import pandas as pd

from racing.bsp_data import DB_PATH as BSP_DB
from racing.clean import FORM_DB

# Betfair's menu_hint course names are not stable over time. Up to ~2023 they carry a
# country prefix and are often abbreviated ("GB / Kemp 3rd Jun", "IRE / Dund"); from 2024
# they are plain ("Kempton 3rd Jun"). Resolution is: strip the prefix, apply the explicit
# alias map, then fall back to a UNIQUE prefix match against the known course list.
COURSE_ALIASES = {
    "chelmsfordcity": "Chelmsford", "chelmc": "Chelmsford", "royalascot": "Ascot",
    "epsm": "Epsom", "extr": "Exeter", "mrktr": "Market Rasen", "sthl": "Southwell",
    "gowp": "Gowran Park", "ballinarobe": "Ballinrobe",  # source typo
}
_COUNTRY_PREFIX = re.compile(r"^(GB|IRE|INT)\s*/\s*", re.I)
_MENU_DATE = re.compile(r"\s+\d{1,2}(st|nd|rd|th)\s+\w+$", re.I)
_SUFFIX = re.compile(r"\s*\([A-Z]{2,3}\)\s*$")
_NONALNUM = re.compile(r"[^a-z0-9]")


def _course_lookup() -> dict[str, str]:
    from racing.clean import GB_COURSES, IRE_COURSES
    return {_NONALNUM.sub("", c.lower()): c for c in sorted(GB_COURSES | IRE_COURSES)}


def resolve_course(raw: str) -> str | None:
    """Betfair menu_hint course text -> our course_base, or None if ambiguous/unknown."""
    known = _course_lookup()
    c = _COUNTRY_PREFIX.sub("", str(raw)).strip()
    n = _NONALNUM.sub("", c.lower())
    if n in COURSE_ALIASES:
        return COURSE_ALIASES[n]
    if n in known:
        return known[n]
    hits = {v for k, v in known.items() if k.startswith(n) or n.startswith(k)}
    return hits.pop() if len(hits) == 1 else None


def norm_name(s: pd.Series) -> pd.Series:
    """'Definitly Red (IRE)' -> 'definitlyred'."""
    return (s.astype("string").str.replace(_SUFFIX, "", regex=True)
             .str.lower().str.replace(_NONALNUM, "", regex=True))


def load_bsp(market: str = "win") -> pd.DataFrame:
    """BSP runner rows, read-only (the downloader may be writing concurrently)."""
    from racing.bsp_data import connect_db
    con = connect_db(BSP_DB, read_only=True)  # WAL: does not block a concurrent download
    b = pd.read_sql(
        "select file_date, region, menu_hint, event_id, event_dt, selection_id, selection_name,"
        " win_lose, bsp, ppwap, morningwap, ppmin, ppmax, morningtradedvol, pptradedvol, iptradedvol"
        " from bsp_runner where market = ?", con, params=[market])
    con.close()
    b["race_dt_bf"] = pd.to_datetime(b.event_dt, format="%d-%m-%Y %H:%M", errors="coerce")
    b = b[b.race_dt_bf.notna()].copy()
    b["date"] = b.race_dt_bf.dt.normalize()
    raw_course = b.menu_hint.astype("string").str.replace(_MENU_DATE, "", regex=True).str.strip()
    b["course_bf"] = raw_course.map({v: resolve_course(v) for v in raw_course.dropna().unique()})
    b["name_key"] = norm_name(b.selection_name)
    b.loc[b.bsp >= 1000, "bsp"] = np.nan        # 1000 = no SP backers, not a price
    b.loc[b.bsp <= 1.0, "bsp"] = np.nan
    return b


def join_market(feats: pd.DataFrame, bsp: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    f = feats.copy()
    f["name_key"] = norm_name(f.horse)
    f["date"] = pd.to_datetime(f.date).dt.normalize()

    m = f[["race_key", "horse_key", "date", "race_dt", "course_base", "horse", "name_key", "win", "field"]].merge(
        bsp, on=["date", "name_key"], how="inner", suffixes=("", "_bf"))

    # resolve rows where one (date, name) matched more than one candidate
    m["dup"] = m.duplicated(["race_key", "horse_key"], keep=False) | m.duplicated(["event_id", "selection_id"], keep=False)
    clean, ambig = m[~m.dup].copy(), m[m.dup].copy()
    resolved = pd.DataFrame()
    if len(ambig):
        ambig["course_ok"] = ambig.course_base == ambig.course_bf
        ambig["dt_gap"] = (ambig.race_dt - ambig.race_dt_bf).abs().dt.total_seconds().abs()
        ambig = ambig.sort_values(["course_ok", "dt_gap"], ascending=[False, True])
        best = ambig[ambig.course_ok].drop_duplicates(["race_key", "horse_key"], keep="first")
        resolved = best.drop_duplicates(["event_id", "selection_id"], keep="first")
    out = pd.concat([clean, resolved], ignore_index=True) if len(resolved) else clean

    stats = {
        "form_runners": len(f), "bsp_runners": len(bsp), "matched": len(out),
        "ambiguous_dropped": int(len(ambig) - len(resolved)),
        "course_mismatch": int((out.course_base != out.course_bf).sum()),
    }
    both = out[out.bsp.notna()]
    stats["winner_agreement"] = float(((both.win == 1) == (both.win_lose == 1)).mean()) if len(both) else float("nan")
    return out, stats


def add_probs(m: pd.DataFrame) -> pd.DataFrame:
    m = m.copy()
    m["bsp_prob"] = 1.0 / m.bsp
    m["ppwap_prob"] = 1.0 / m.ppwap.where(m.ppwap > 1.0)
    m["morning_prob"] = 1.0 / m.morningwap.where(m.morningwap > 1.0)
    g = m.groupby("race_key")
    n_priced = g.bsp_prob.transform("count")
    m["book"] = g.bsp_prob.transform("sum")
    # only trust a normalised book when we priced the entire field
    full = n_priced == m.field
    m["bsp_prob_norm"] = (m.bsp_prob / m.book).where(full)
    m["book_full"] = full.astype(int)
    return m


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", default="win", choices=["win", "place"])
    args = ap.parse_args()

    con = sqlite3.connect(FORM_DB, timeout=180)
    feats = pd.read_sql("select race_key, horse_key, horse, date, race_dt, course_base, win, field from features",
                        con, parse_dates=["date", "race_dt"])
    bsp = load_bsp(args.market)
    print(f"form runners: {len(feats):,} | bsp runners: {len(bsp):,} "
          f"({bsp.date.min().date()} -> {bsp.date.max().date()})")

    m, stats = join_market(feats, bsp)
    m = add_probs(m)
    for k, v in stats.items():
        print(f"  {k}: {v:,.4f}" if isinstance(v, float) else f"  {k}: {v:,}")

    # coverage measured only over the overlapping date range
    lo, hi = bsp.date.min(), bsp.date.max()
    in_range = feats[(feats.date >= lo) & (feats.date <= hi)]
    print(f"  coverage in BSP date range: {len(m):,} / {len(in_range):,} = {len(m)/max(len(in_range),1):.1%}")
    print(f"  with a usable BSP: {m.bsp.notna().sum():,} | full-book races: {m.book_full.sum():,}")

    keep = ["race_key", "horse_key", "date", "event_id", "selection_id", "course_bf", "region", "win_lose",
            "bsp", "ppwap", "morningwap", "ppmin", "ppmax", "morningtradedvol", "pptradedvol", "iptradedvol",
            "bsp_prob", "ppwap_prob", "morning_prob", "bsp_prob_norm", "book", "book_full"]
    m[keep].to_sql("market", con, if_exists="replace", index=False, chunksize=50_000)
    con.execute("create index if not exists ix_market_key on market (race_key, horse_key)")
    con.commit()
    con.close()
    print(f"wrote market: {len(m):,} rows -> {FORM_DB}")


if __name__ == "__main__":
    main()
