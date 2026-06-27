#!/usr/bin/env python3
"""
Download match data for the cards/corners models from football-data.co.uk.

Unlike the 1X2 work, we keep the secondary-stat columns: corners (HC/AC),
cards (HY/AY/HR/AR), fouls (HF/AF), shots (HS/AS/HST/AST) and crucially the
REFEREE, which is the strongest public predictor of cards. football-data does
NOT publish historical cards/corners betting odds, so this file is for building
and CALIBRATING the predictive model; ROI validation needs an odds source we
collect separately (see README).

Writes data/matches.csv (all leagues/seasons, one row per match).

Usage:
  python3 fetch_data.py
  python3 fetch_data.py --from 1617 --to 2425
"""
import argparse
import csv
import os
import urllib.request

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# football-data division code -> readable league name
LEAGUES = {
    "E0": "Premier League", "E1": "Championship", "SP1": "La Liga",
    "I1": "Serie A", "D1": "Bundesliga", "F1": "Ligue 1",
}
# season codes football-data uses: 1617 = 2016/17 ... 2425 = 2024/25
DEFAULT_SEASONS = ["1617", "1718", "1819", "1920", "2021",
                   "2122", "2223", "2324", "2425"]

KEEP = ["Div", "Date", "HomeTeam", "AwayTeam", "Referee",
        "FTHG", "FTAG", "HS", "AS", "HST", "AST",
        "HF", "AF", "HC", "AC", "HY", "AY", "HR", "AR"]


def fetch(seasons):
    os.makedirs(DATA_DIR, exist_ok=True)
    rows = []
    for season in seasons:
        for div in LEAGUES:
            url = f"https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    raw_bytes = resp.read()
                if raw_bytes.startswith(b"\xef\xbb\xbf"):  # strip UTF-8 BOM
                    raw_bytes = raw_bytes[3:]              # else 'Div' key gets mangled
                raw = raw_bytes.decode("latin-1")
            except Exception as e:
                print(f"  warn: {div} {season} failed ({e})")
                continue
            n = 0
            for r in csv.DictReader(raw.splitlines()):
                # need corners AND cards present to be useful
                if not r.get("HomeTeam") or r.get("HC") in (None, "") or r.get("HY") in (None, ""):
                    continue
                rec = {k: (r.get(k) or "") for k in KEEP}
                rec["Season"] = season
                rows.append(rec)
                n += 1
            print(f"  {div} {season}: {n} rows")
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="frm", default=None)
    ap.add_argument("--to", dest="to", default=None)
    args = ap.parse_args()

    seasons = DEFAULT_SEASONS
    if args.frm or args.to:
        all_codes = ["%02d%02d" % (y % 100, (y + 1) % 100) for y in range(2000, 2026)]
        lo = args.frm or all_codes[0]
        hi = args.to or all_codes[-1]
        seasons = [c for c in all_codes if lo <= c <= hi]

    rows = fetch(seasons)
    path = os.path.join(DATA_DIR, "matches.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Season"] + KEEP)
        w.writeheader()
        w.writerows(rows)
    print(f"\n-> {len(rows)} matches written to {path}")


if __name__ == "__main__":
    main()
