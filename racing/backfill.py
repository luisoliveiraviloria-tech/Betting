"""
Extend the form archive with results from The Racing API.

The Kaggle archive ends 2026-06-03. `rp_rpr_last` — the horse's Racing Post
rating **last time out** — carries 100% of the measured edge (RECON.md s.14), so
every day the archive is stale is a day's worth of runners whose strongest feature
has gone missing. Backfilling results is not housekeeping; it is what keeps the
model's edge alive.

    python -m racing.backfill --from 2026-06-04 --to 2026-09-21   # gap fill (Standard plan)
    python -m racing.backfill --today                             # daily top-up (Free plan)

Rows are written into the same `runs` table `clean.py` produces, through the same
`clean_runs()` parser, and races already present are replaced rather than
duplicated. Re-run `python -m racing.features` afterwards so the feature table
picks them up.

The report at the end is the point of the module: **RPR coverage on the rows just
added**. If that number is low the archive is being extended with rows that cannot
support the edge feature, and the strategy needs re-validating against whatever
rating the feed does supply before any live betting.
"""
import argparse
import sqlite3
from datetime import date, timedelta

import pandas as pd

from racing import sources
from racing.clean import FORM_DB

CHUNK_DAYS = 7  # /v1/results is paged; a week at a time keeps each walk small


def existing_span(db=FORM_DB) -> tuple[str, str, int]:
    con = sqlite3.connect(db, timeout=180)
    row = con.execute("select min(date), max(date), count(*) from runs").fetchone()
    con.close()
    return row


def upsert_runs(runs: pd.DataFrame, db=FORM_DB) -> tuple[int, int]:
    """Replace any race already stored, then append. Returns (deleted, inserted)."""
    if runs.empty:
        return 0, 0
    runs = runs.copy()
    con = sqlite3.connect(db, timeout=180)
    cur = con.cursor()
    keys = sorted(set(runs.race_key))
    deleted = 0
    for i in range(0, len(keys), 500):
        batch = keys[i:i + 500]
        q = ",".join("?" * len(batch))
        cur.execute(f"delete from runs where race_key in ({q})", batch)
        deleted += cur.rowcount
    con.commit()
    cols = [r[1] for r in con.execute("pragma table_info(runs)").fetchall()]
    missing = [c for c in cols if c not in runs.columns]
    for c in missing:
        runs[c] = None
    runs[cols].to_sql("runs", con, if_exists="append", index=False, chunksize=20_000)
    con.commit()
    con.close()
    return deleted, len(runs)


def fetch_range(api, start: str, end: str) -> list[dict]:
    """Walk /v1/results a week at a time (Standard plan; 12 months of history)."""
    out, cur = [], pd.Timestamp(start)
    last = pd.Timestamp(end)
    while cur <= last:
        stop = min(cur + timedelta(days=CHUNK_DAYS - 1), last)
        got = api.results(cur.strftime("%Y-%m-%d"), stop.strftime("%Y-%m-%d"))
        print(f"    {cur.date()} -> {stop.date()}: {len(got)} races")
        out.extend(got)
        cur = stop + timedelta(days=1)
    return out


def report(runs: pd.DataFrame) -> None:
    n = len(runs)
    if not n:
        print("  nothing to report — no rows added")
        return
    print(f"\n  added {n:,} runners over {runs.race_key.nunique():,} races "
          f"({runs.date.min().date()} -> {runs.date.max().date()})")
    for col, label in (("rpr", "RPR   (rp_rpr_last — CARRIES THE EDGE)"),
                       ("or_", "OR    (official mark)"),
                       ("ts", "TS    (topspeed)"),
                       ("pos_num", "result (finishing position)"),
                       ("sp_dec", "SP")):
        if col in runs:
            print(f"    {label:<42} {runs[col].notna().mean():6.1%} populated")
    cov = runs["rpr"].notna().mean() if "rpr" in runs else 0.0
    print()
    if cov >= 0.85:
        print("  RPR is intact on these rows. The daily loop can keep the edge feature alive.")
    elif cov >= 0.5:
        print("  RPR is PARTIAL. Expect the edge feature to be missing for a share of runners; "
              "use `racing.live --require-edge-feature` so those are not bet blind.")
    else:
        print("  RPR IS ABSENT. Do not bet live on this archive: the feature carrying 100% of "
              "the validated edge cannot be built. Re-validate against the feed's own rating "
              "(performance_rating) on a walk-forward before risking money.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", help="YYYY-MM-DD (defaults to the day after the archive ends)")
    ap.add_argument("--to", dest="end", default=None, help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--today", action="store_true", help="just today's results (free endpoint)")
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    args = ap.parse_args()

    from racing.racingapi import RacingAPI
    api = RacingAPI()

    if not FORM_DB.exists():
        raise SystemExit(f"no form database at {FORM_DB} — run racing.clean first")
    lo, hi, n = existing_span()
    print(f"  archive: {n:,} runs, {str(lo)[:10]} -> {str(hi)[:10]}")

    if args.today:
        raw_results = api.results_today(free=True)
    else:
        if not args.start and hi is None:
            raise SystemExit("the archive is empty and no --from was given")
        start = args.start or (pd.Timestamp(str(hi)[:10]) + timedelta(days=1)).strftime("%Y-%m-%d")
        end = args.end or (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        if pd.Timestamp(start) > pd.Timestamp(end):
            print("  archive is already current — nothing to do")
            return
        print(f"  fetching results {start} -> {end}")
        raw_results = fetch_range(api, start, end)

    runs = sources.to_runs(sources.results_to_raw(raw_results))
    report(runs)
    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return
    deleted, inserted = upsert_runs(runs)
    print(f"\n  wrote {inserted:,} rows (replaced {deleted:,} already stored) -> {FORM_DB}")
    print("  now run:  python -m racing.features")


if __name__ == "__main__":
    main()
