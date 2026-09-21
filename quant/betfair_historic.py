"""
Client for Betfair's Historic Data service (historicdata.betfair.com) —
pre-recorded streaming market-data files (bz2-compressed, one file per
market), separate from the live API-NG. Free tier ("Basic Plan") covers a
rolling window of recent data across most sports/countries; deeper history
requires a paid plan purchased via the Historic Data web UI.

This is the Stage-2 priority named in quant/README.md: order-book/price
history the football-data.co.uk closing-line odds cannot provide, needed
to study price movement and microstructure rather than just the closing
price.

Endpoints (developer.betfair.com "Historical Data" docs):
  GET  /GetCollectionOptions   -- valid filter values (sports, plans, market types...)
  GET  /GetMyData              -- plans this account is subscribed to
  POST /DownloadListOfFiles    -- file paths matching a filter
  GET  /DownloadFile           -- streams one bz2 file, ?filePath=<path>

All calls take the same auth headers as API-NG: X-Application (app key)
and X-Authentication (session token from betfair_auth.login_*).

NOT YET VERIFIED LIVE: Betfair's edge blocks the login step from this
sandbox's IP (see betfair_auth.py), so these calls are un-exercised here.
Run `python -m quant.betfair_historic --list` from a permitted IP first
and check the response shape against BETFAIR_API_NOTES.md before trusting
a bulk download.
"""
import argparse
import os
from pathlib import Path

import requests

from quant.betfair_auth import BetfairSession, login_interactive

BASE_URL = "https://historicdata.betfair.com/api"
DATA_DIR = Path(__file__).parent / "data" / "betfair_historic"

# EPL matches are Soccer / GB (Great Britain) competition-filtered; Betfair's
# collection filter uses eventName / marketTypesCollection rather than a
# competition ID, so we filter client-side on eventName after listing.
DEFAULT_SPORT = "Soccer"
DEFAULT_PLAN = "Basic Plan"
DEFAULT_MARKET_TYPES = ["MATCH_ODDS"]
DEFAULT_COUNTRIES = ["GB"]


def get_collection_options(session: BetfairSession, sport: str = DEFAULT_SPORT) -> dict:
    resp = requests.get(
        f"{BASE_URL}/GetCollectionOptions",
        headers=session.headers, params={"sport": sport}, timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_my_data(session: BetfairSession) -> dict:
    resp = requests.get(f"{BASE_URL}/GetMyData", headers=session.headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def list_files(session: BetfairSession, from_day: int, from_month: int, from_year: int,
                to_day: int, to_month: int, to_year: int,
                sport: str = DEFAULT_SPORT, plan: str = DEFAULT_PLAN,
                market_types: list[str] = None, countries: list[str] = None) -> list[str]:
    body = {
        "sport": sport,
        "plan": plan,
        "fromDay": from_day, "fromMonth": from_month, "fromYear": from_year,
        "toDay": to_day, "toMonth": to_month, "toYear": to_year,
        "marketTypesCollection": market_types or DEFAULT_MARKET_TYPES,
        "countriesCollection": countries or DEFAULT_COUNTRIES,
        "fileTypeCollection": ["M"],  # market data (vs. "E" event/result files)
    }
    resp = requests.post(
        f"{BASE_URL}/DownloadListOfFiles", headers=session.headers, json=body, timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def download_file(session: BetfairSession, file_path: str, dest_dir: Path = DATA_DIR) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / file_path.replace("/", "_")
    resp = requests.get(
        f"{BASE_URL}/DownloadFile", headers=session.headers,
        params={"filePath": file_path}, stream=True, timeout=120,
    )
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
    return dest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", help="DD-MM-YYYY", required=False)
    parser.add_argument("--to-date", help="DD-MM-YYYY", required=False)
    parser.add_argument("--list", action="store_true", help="list matching files, don't download")
    parser.add_argument("--options", action="store_true", help="print GetCollectionOptions and exit")
    parser.add_argument("--my-data", action="store_true", help="print GetMyData (subscribed plans) and exit")
    args = parser.parse_args()

    session = login_interactive()
    print("Logged in.")

    if args.options:
        import json
        print(json.dumps(get_collection_options(session), indent=2))
        raise SystemExit

    if args.my_data:
        import json
        print(json.dumps(get_my_data(session), indent=2))
        raise SystemExit

    if not args.from_date or not args.to_date:
        parser.error("--from-date/--to-date required unless --options or --my-data")

    fd, fm, fy = (int(x) for x in args.from_date.split("-"))
    td, tm, ty = (int(x) for x in args.to_date.split("-"))
    files = list_files(session, fd, fm, fy, td, tm, ty)
    print(f"{len(files)} files matched.")

    if args.list:
        for f in files[:50]:
            print(f)
        raise SystemExit

    for f in files:
        dest = download_file(session, f)
        print(f"downloaded -> {dest}")
