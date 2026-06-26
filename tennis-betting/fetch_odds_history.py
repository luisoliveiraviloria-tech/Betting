#!/usr/bin/env python3
"""
Download historical ATP/WTA results WITH bookmaker closing odds from
tennis-data.co.uk, and flatten into data/{atp,wta}_odds.csv.

The Sackmann match files (fetch_data.sh) have results but no prices, so they
can't tell us whether our value signal actually makes money. tennis-data.co.uk
ships per-year spreadsheets that include results AND odds (Pinnacle PSW/PSL,
market average AvgW/AvgL) in the same row - exactly what a value-bet backtest
needs, with no cross-dataset name matching.

Requires openpyxl (only this offline tool needs it; the live model stays
stdlib-only):  pip install openpyxl

Usage:
  python3 fetch_odds_history.py                 # 2015-2026, both tours
  python3 fetch_odds_history.py --from 2010
"""
import argparse
import csv
import io
import os
import urllib.request

import openpyxl

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
# ATP files live at /{year}/{year}.xlsx, WTA at /{year}w/{year}.xlsx
URL = {
    "atp": "http://www.tennis-data.co.uk/{year}/{year}.xlsx",
    "wta": "http://www.tennis-data.co.uk/{year}w/{year}.xlsx",
}
# columns we keep, by header name (tennis-data schema is stable)
KEEP = ["Date", "Surface", "Winner", "Loser", "PSW", "PSL", "AvgW", "AvgL", "Comment"]


def fetch_year(tour, year):
    url = URL[tour].format(year=year)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            raw = resp.read()
    except Exception as e:
        print(f"  warning: {tour} {year} fetch failed ({e}), skipping")
        return []
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    header = list(next(it))
    idx = {name: header.index(name) for name in KEEP if name in header}
    out = []
    for row in it:
        if not row or row[idx.get("Winner", 0)] is None:
            continue
        rec = {}
        for name in KEEP:
            rec[name] = row[idx[name]] if name in idx else None
        date = rec["Date"]
        if hasattr(date, "date"):
            rec["Date"] = date.date().isoformat()
        elif date is None:
            continue
        out.append(rec)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="year_from", type=int, default=2015)
    parser.add_argument("--to", dest="year_to", type=int, default=2026)
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    for tour in ("atp", "wta"):
        rows = []
        for year in range(args.year_from, args.year_to + 1):
            yr = fetch_year(tour, year)
            print(f"{tour} {year}: {len(yr)} rows")
            rows.extend(yr)
        path = os.path.join(DATA_DIR, f"{tour}_odds.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=KEEP)
            w.writeheader()
            w.writerows(rows)
        print(f"-> wrote {len(rows)} rows to {path}\n")


if __name__ == "__main__":
    main()
