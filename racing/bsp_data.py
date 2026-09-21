"""
Phase 1: Betfair free daily BSP CSVs (UK + IRE horse racing) -> SQLite.

Source: https://promo.betfair.com/betfairsp/prices/dwbfprices{uk|ire}{win|place}DDMMYYYY.csv
No login. Betfair notes the data is (c) / database-right protected; the user
has accepted personal-research use of it (2026-09-21).

Behaviour confirmed live 2026-09-21:
  - 8 parallel threads -> HTTP 429 after ~64 requests. Fetch sequentially.
  - a 302 whose Location is the SAME URL is a transient edge glitch, not "no
    racing": the identical file returned 200 seconds later, and a place file
    had rows while the win file 302'd. Retried with backoff; only recorded as
    "none" after MAX_SELF_REDIRECTS failures. A day can still legitimately have
    no file (e.g. abandoned meeting), so `--audit` cross-checks win vs place.
  - Python 3.13+ verifies with X509_STRICT and rejects Betfair's CA chain
    ("Basic Constraints of CA cert not marked critical"); requests/certifi
    also fails. We clear ONLY the VERIFY_X509_STRICT flag -- chain and hostname
    verification stay on. Do not disable verification outright.
  - header casing differs between files (EVENT_ID vs event_id) -> normalised.
  - `bsp` is blank for non-runners, 1000 when nobody was matched at SP.

Resumable: every (date, region, market) outcome goes in `fetch_log`; reruns
skip anything already resolved ("ok" or "none"). Walks newest -> oldest so the
most useful data lands first. Ctrl-C safe.

    python -m racing.bsp_data --start 2016-01-01            # win markets
    python -m racing.bsp_data --start 2024-01-01 --markets win place
"""
import argparse
import csv
import datetime as dt
import io
import sqlite3
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "racing.db"
URL = "https://promo.betfair.com/betfairsp/prices/dwbfprices{region}{market}{d:%d%m%Y}.csv"
REGIONS = ("uk", "ire")
MAX_SELF_REDIRECTS = 5
COLUMNS = [
    "event_id", "menu_hint", "event_name", "event_dt", "selection_id", "selection_name",
    "win_lose", "bsp", "ppwap", "morningwap", "ppmax", "ppmin", "ipmax", "ipmin",
    "morningtradedvol", "pptradedvol", "iptradedvol",
]
NUMERIC = set(COLUMNS[6:])

SCHEMA = """
CREATE TABLE IF NOT EXISTS bsp_runner (
    file_date TEXT NOT NULL, region TEXT NOT NULL, market TEXT NOT NULL,
    event_id TEXT, menu_hint TEXT, event_name TEXT, event_dt TEXT,
    selection_id TEXT, selection_name TEXT,
    win_lose REAL, bsp REAL, ppwap REAL, morningwap REAL, ppmax REAL, ppmin REAL,
    ipmax REAL, ipmin REAL, morningtradedvol REAL, pptradedvol REAL, iptradedvol REAL,
    PRIMARY KEY (file_date, region, market, event_id, selection_id)
);
CREATE INDEX IF NOT EXISTS ix_bsp_event ON bsp_runner (market, event_id);
CREATE TABLE IF NOT EXISTS fetch_log (
    file_date TEXT NOT NULL, region TEXT NOT NULL, market TEXT NOT NULL,
    status TEXT NOT NULL, n_rows INTEGER, fetched_at TEXT,
    PRIMARY KEY (file_date, region, market)
);
"""


def connect_db(path: Path = None, read_only: bool = False) -> sqlite3.Connection:
    """WAL + a long busy timeout so an analysis query reading this DB cannot kill a
    multi-hour download. In the default rollback-journal mode a reader holds a SHARED
    lock that blocks the writer's commit -- that is exactly what happened on 2026-09-21
    (the downloader died with "database is locked" at file 5,219 of ~7,830 while a
    pandas read_sql was running). WAL lets readers and the single writer coexist.
    """
    path = path or DB_PATH
    if read_only:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=180)
    else:
        con = sqlite3.connect(path, timeout=180)
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=180000")
    return con


def _num(v: str):
    v = (v or "").strip()
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def parse_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]
    if "event_id" not in reader.fieldnames or "bsp" not in reader.fieldnames:
        raise ValueError(f"unexpected header: {reader.fieldnames[:5]}")
    rows = []
    for r in reader:
        rows.append({c: (_num(r.get(c)) if c in NUMERIC else (r.get(c) or "").strip() or None)
                     for c in COLUMNS})
    return rows


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None  # surface 3xx as HTTPError instead of following it


_CTX = ssl.create_default_context()
_CTX.verify_flags &= ~ssl.VERIFY_X509_STRICT
_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=_CTX))


def fetch_one(region: str, market: str, d: dt.date):
    """Returns (status, rows). status: ok | none | transient | throttled:<retry-after> | error:<detail>."""
    req = urllib.request.Request(URL.format(region=region, market=market, d=d),
                                 headers={"User-Agent": "Mozilla/5.0"})
    try:
        with _OPENER.open(req, timeout=30) as resp:
            text = resp.read().decode("utf-8-sig")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return f"throttled:{e.headers.get('Retry-After', '')}", []
        if e.code in (301, 302) and e.headers.get("Location", "") == req.full_url:
            return "transient", []
        if e.code in (301, 302, 404):
            return "none", []
        return f"error:http{e.code}", []
    except (urllib.error.URLError, TimeoutError) as e:
        return f"error:{e}", []
    try:
        return "ok", parse_csv(text)
    except ValueError as e:
        return f"error:{e}", []


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2016-01-01", help="oldest date (YYYY-MM-DD)")
    ap.add_argument("--end", default=None, help="newest date, default yesterday")
    ap.add_argument("--markets", nargs="+", default=["win"], choices=["win", "place"])
    ap.add_argument("--audit", action="store_true",
                    help="list (date, region) where win/place disagree on presence, then exit")
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    args = ap.parse_args()

    if args.audit:
        con = connect_db()
        rows = con.execute("""
            SELECT file_date, region, GROUP_CONCAT(market || ':' || status || ':' || n_rows, '  ')
            FROM fetch_log GROUP BY file_date, region
            HAVING COUNT(DISTINCT market) > 1 AND COUNT(DISTINCT status) > 1
            ORDER BY file_date""").fetchall()
        for r in rows:
            print(*r)
        print(f"{len(rows)} (date, region) pairs disagree between win and place")
        return

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today() - dt.timedelta(days=1)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = connect_db()
    con.executescript(SCHEMA)
    done = {tuple(r) for r in con.execute(
        "SELECT file_date, region, market FROM fetch_log WHERE status IN ('ok','none')")}

    backoff = 30
    n_new = n_rows = 0
    day = end
    try:
        while day >= start:
            for market in args.markets:
                for region in REGIONS:
                    key = (day.isoformat(), region, market)
                    if key in done:
                        continue
                    tries = 0
                    while True:
                        status, rows = fetch_one(region, market, day)
                        if status == "transient":
                            tries += 1
                            if tries >= MAX_SELF_REDIRECTS:
                                status = "none"
                                break
                            time.sleep(5 * tries)
                            continue
                        if status.startswith("throttled"):
                            wait = int(status.split(":")[1] or 0) or backoff
                            print(f"429 at {day} {region} {market}; sleeping {wait}s", flush=True)
                            time.sleep(wait)
                            backoff = min(backoff * 2, 900)
                            continue
                        backoff = 30
                        break
                    if status == "ok":
                        con.executemany(
                            "INSERT OR REPLACE INTO bsp_runner (file_date, region, market, "
                            + ", ".join(COLUMNS) + ") VALUES (?, ?, ?, " + ", ".join("?" * len(COLUMNS)) + ")",
                            [(key[0], region, market, *(r[c] for c in COLUMNS)) for r in rows],
                        )
                        n_rows += len(rows)
                    if status in ("ok", "none") or status.startswith("error"):
                        con.execute("INSERT OR REPLACE INTO fetch_log VALUES (?,?,?,?,?,?)",
                                    (*key, status, len(rows), dt.datetime.now().isoformat(timespec="seconds")))
                    con.commit()
                    n_new += 1
                    if n_new % 50 == 0:
                        print(f"{day}: {n_new} files fetched, {n_rows} runner rows", flush=True)
                    time.sleep(args.delay)
            day -= dt.timedelta(days=1)
    except KeyboardInterrupt:
        print("interrupted; progress saved, rerun to resume")
    finally:
        con.commit()
        con.close()
    print(f"done: {n_new} files fetched this run, {n_rows} runner rows")


if __name__ == "__main__":
    main()
