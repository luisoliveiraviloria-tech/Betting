"""
Downloads EPL historical match + odds data from football-data.co.uk and
stores it in a local SQLite database (quant/epl.db).

Source: https://www.football-data.co.uk/englandm.php (free, no key, EPL
data back to 1993; Betfair Exchange odds columns present from ~2012/13
onward).

Column meaning per football-data.co.uk/notes.txt:
  - AvgH/D/A, AvgCH/D/A   : bookmaker-average odds (pre-close / closing)
  - BFEH/D/A, BFECH/D/A   : Betfair Exchange odds (pre-close / closing)
Both pairs are used: BFE* where available (the actual venue in scope for
this project), falling back to Avg* for older seasons that predate the
Betfair Exchange columns.
"""
import sqlite3
from pathlib import Path

import pandas as pd
import requests

DB_PATH = Path(__file__).parent / "epl.db"
BASE_URL = "https://www.football-data.co.uk/mmz4281/{code}/E0.csv"
START_SEASON = 2005  # first season with the standardised column layout

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    season INTEGER NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_goals INTEGER NOT NULL,
    away_goals INTEGER NOT NULL,
    result TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS markets (
    match_id TEXT NOT NULL,
    venue TEXT NOT NULL,           -- 'betfair_exchange' or 'bookmaker_avg'
    snapshot TEXT NOT NULL,        -- 'pre_close' or 'closing'
    odds_home REAL,
    odds_draw REAL,
    odds_away REAL,
    PRIMARY KEY (match_id, venue, snapshot),
    FOREIGN KEY (match_id) REFERENCES matches(match_id)
);
"""


def _season_code(start_year: int) -> str:
    return f"{str(start_year)[-2:]}{str(start_year + 1)[-2:]}"


def download_season(start_year: int) -> pd.DataFrame | None:
    url = BASE_URL.format(code=_season_code(start_year))
    resp = requests.get(url, timeout=30, allow_redirects=True)
    if resp.status_code != 200 or not resp.content:
        return None
    from io import StringIO
    try:
        df = pd.read_csv(StringIO(resp.content.decode("utf-8-sig")), on_bad_lines="skip")
    except UnicodeDecodeError:
        df = pd.read_csv(StringIO(resp.content.decode("latin1")), on_bad_lines="skip")
    df["Season"] = start_year
    return df


def build_database(end_year: int, db_path: Path = DB_PATH, start_year: int = START_SEASON) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    match_rows = []
    market_rows = []

    for year in range(start_year, end_year + 1):
        df = download_season(year)
        if df is None or df.empty:
            print(f"[{year}] no data (season not started / not reachable)")
            continue

        n_matches = 0
        for _, row in df.iterrows():
            if pd.isna(row.get("HomeTeam")) or pd.isna(row.get("FTHG")):
                continue
            match_id = f"E0-{year}-{row['HomeTeam']}-{row['AwayTeam']}-{row['Date']}"
            match_rows.append((
                match_id, str(row["Date"]), year, row["HomeTeam"], row["AwayTeam"],
                int(row["FTHG"]), int(row["FTAG"]), row["FTR"],
            ))
            n_matches += 1

            for venue, snap, cols in [
                ("betfair_exchange", "pre_close", ("BFEH", "BFED", "BFEA")),
                ("betfair_exchange", "closing", ("BFECH", "BFECD", "BFECA")),
                ("bookmaker_avg", "pre_close", ("AvgH", "AvgD", "AvgA")),
                ("bookmaker_avg", "closing", ("AvgCH", "AvgCD", "AvgCA")),
            ]:
                if all(c in row.index for c in cols) and not any(pd.isna(row[c]) for c in cols):
                    market_rows.append((match_id, venue, snap, float(row[cols[0]]), float(row[cols[1]]), float(row[cols[2]])))

        print(f"[{year}] {n_matches} matches")

    conn.executemany(
        "INSERT OR REPLACE INTO matches VALUES (?,?,?,?,?,?,?,?)", match_rows
    )
    conn.executemany(
        "INSERT OR REPLACE INTO markets VALUES (?,?,?,?,?,?)", market_rows
    )
    conn.commit()

    n_m = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    n_bf = conn.execute("SELECT COUNT(*) FROM markets WHERE venue='betfair_exchange'").fetchone()[0]
    conn.close()
    print(f"\nDatabase built: {n_m} matches, {n_bf} Betfair Exchange market rows -> {db_path}")


def load_matches(db_path: Path = DB_PATH) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT * FROM matches ORDER BY date", conn, parse_dates=["date"]
    )
    conn.close()
    return df


def load_market(venue: str, snapshot: str, db_path: Path = DB_PATH) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT * FROM markets WHERE venue=? AND snapshot=?", conn, params=(venue, snapshot)
    )
    conn.close()
    return df


if __name__ == "__main__":
    import argparse, datetime

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end-year", type=int, default=datetime.date.today().year)
    parser.add_argument("--start-year", type=int, default=START_SEASON)
    args = parser.parse_args()
    build_database(end_year=args.end_year, start_year=args.start_year)
