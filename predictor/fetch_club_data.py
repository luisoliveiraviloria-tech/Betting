#!/usr/bin/env python3
"""
Fetch club-league data to backtest our (national-team) Elo->Poisson->Dixon-Coles
model on club football. Two free sources, joined by club name:

  - football-data.co.uk: per-season CSVs with results AND closing odds
    (we keep market-average AvgH/D/A and Pinnacle CLOSING PSCH/D/A).
  - clubelo.com: per-club Elo history with From/To date ranges, giving the
    point-in-time rating needed for a no-lookahead backtest.

Writes:
  data/club/results.csv        flat results+odds for all leagues/seasons
  data/club/elo/<Club>.csv     raw clubelo history per club
  data/club/name_map.json      football-data name -> clubelo name (audit)

Usage:
  python3 fetch_club_data.py                 # default leagues + seasons
  python3 fetch_club_data.py --seasons 2223 2324 2425
"""
import argparse
import csv
import json
import os
import re
import time
import urllib.parse
import urllib.request

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "club")
ELO_DIR = os.path.join(DATA_DIR, "elo")

# football-data.co.uk division codes -> (our label, clubelo country)
LEAGUES = {
    "E0": ("Premier League", "ENG"),
    "SP1": ("La Liga", "ESP"),
    "I1": ("Serie A", "ITA"),
    "D1": ("Bundesliga", "GER"),
    "F1": ("Ligue 1", "FRA"),
}
DEFAULT_SEASONS = ["1819", "1920", "2021", "2122", "2223", "2324", "2425"]

KEEP = ["Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
        "AvgH", "AvgD", "AvgA", "PSCH", "PSCD", "PSCA",
        "B365H", "B365D", "B365A"]

# football-data spelling -> clubelo spelling, only where normalization fails.
# Keys must be in norm() form (lowercase, no apostrophes/dots, single spaces).
ALIAS = {
    # England
    "nottm forest": "Forest", "sheffield united": "Sheffield Utd",
    "west brom": "West Brom", "wolverhampton": "Wolves", "man utd": "Man United",
    # Spain
    "ath madrid": "Atletico", "ath bilbao": "Bilbao", "espanol": "Espanyol",
    "vallecano": "Rayo Vallecano", "sociedad": "Sociedad", "celta": "Celta",
    "la coruna": "La Coruna", "sp gijon": "Gijon",
    # Italy
    "milan": "Milan", "inter": "Inter", "verona": "Verona", "spal": "SPAL",
    # Germany
    "bayern munich": "Bayern", "dortmund": "Dortmund", "mgladbach": "Gladbach",
    "ein frankfurt": "Frankfurt", "werder bremen": "Werder",
    "fc koln": "Koln", "leverkusen": "Leverkusen", "hertha": "Hertha",
    "stuttgart": "Stuttgart", "schalke 04": "Schalke", "mainz": "Mainz",
    "hamburg": "Hamburg", "fortuna dusseldorf": "Dusseldorf",
    "holstein kiel": "Holstein", "greuther furth": "Furth",
    # France
    "paris sg": "Paris SG", "st etienne": "Saint-Etienne", "etienne": "Saint-Etienne",
    "marseille": "Marseille", "nimes": "Nimes", "clermont": "Clermont",
}


def norm(name):
    s = name.lower().strip()
    s = s.replace(".", "").replace("'", "").replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def fetch_football_data(seasons):
    os.makedirs(DATA_DIR, exist_ok=True)
    rows = []
    for season in seasons:
        for div in LEAGUES:
            url = f"https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    raw = resp.read().decode("latin-1")
            except Exception as e:
                print(f"  warn: {div} {season} failed ({e})")
                continue
            reader = csv.DictReader(raw.splitlines())
            n = 0
            for r in reader:
                if not r.get("HomeTeam") or not r.get("FTR"):
                    continue
                rec = {k: (r.get(k) or "") for k in KEEP}
                rec["Season"] = season
                rows.append(rec)
                n += 1
            print(f"  {div} {season}: {n} rows")
    return rows


def build_name_map(rows):
    """Map every football-data team name to a clubelo name (alias or identity)."""
    names = set()
    for r in rows:
        names.add(r["HomeTeam"])
        names.add(r["AwayTeam"])
    name_map = {}
    for n in sorted(names):
        key = norm(n)
        if key in ALIAS:
            name_map[n] = ALIAS[key]
        else:
            # clubelo names are mostly the same token; default to identity and
            # let the fetch step flag any that 404 so we can add an alias.
            name_map[n] = n
    return name_map


def fetch_clubelo(name_map):
    os.makedirs(ELO_DIR, exist_ok=True)
    clubelo_names = sorted(set(name_map.values()))
    ok, missing = [], []
    for cname in clubelo_names:
        safe = cname.replace(" ", "_").replace("/", "_")
        path = os.path.join(ELO_DIR, f"{safe}.csv")
        if os.path.exists(path) and os.path.getsize(path) > 100:
            ok.append(cname)
            continue
        url = "http://api.clubelo.com/" + urllib.parse.quote(cname.replace(" ", ""))
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
            if raw.count("\n") < 2:
                missing.append(cname)
                continue
            with open(path, "w", encoding="utf-8") as f:
                f.write(raw)
            ok.append(cname)
        except Exception as e:
            missing.append(cname)
        time.sleep(0.2)  # be polite to the free API
    return ok, missing


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seasons", nargs="+", default=DEFAULT_SEASONS)
    args = ap.parse_args()

    print("Fetching football-data.co.uk results+odds...")
    rows = fetch_football_data(args.seasons)
    results_path = os.path.join(DATA_DIR, "results.csv")
    with open(results_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Season"] + KEEP)
        w.writeheader()
        w.writerows(rows)
    print(f"-> {len(rows)} match rows to {results_path}\n")

    name_map = build_name_map(rows)
    print(f"Fetching clubelo histories for {len(set(name_map.values()))} clubs...")
    ok, missing = fetch_clubelo(name_map)
    with open(os.path.join(DATA_DIR, "name_map.json"), "w", encoding="utf-8") as f:
        json.dump(name_map, f, indent=2, ensure_ascii=False)
    print(f"-> clubelo OK: {len(ok)}  | unresolved (add to ALIAS): {len(missing)}")
    if missing:
        print("   missing:", ", ".join(missing))


if __name__ == "__main__":
    main()
