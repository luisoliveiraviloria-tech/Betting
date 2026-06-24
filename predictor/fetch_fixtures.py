#!/usr/bin/env python3
"""
Fetch live FIFA World Cup fixtures from football-data.org (v4 API).

Requires FOOTBALL_DATA_API_KEY in predictor/.env (see .env.example).
Free tier covers fixtures/scores/standings, not odds.

Usage:
  python3 fetch_fixtures.py                  # all scheduled matches
  python3 fetch_fixtures.py --matchday 3
"""
import argparse
import json
import urllib.request

from env_config import require

API_URL = "https://api.football-data.org/v4/competitions/WC/matches"


def fetch_fixtures(matchday=None, status=None):
    api_key = require("FOOTBALL_DATA_API_KEY")
    params = {}
    if matchday is not None:
        params["matchday"] = str(matchday)
    if status:
        params["status"] = status
    url = API_URL
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())

    req = urllib.request.Request(url, headers={"X-Auth-Token": api_key})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.load(resp)

    fixtures = []
    for m in data.get("matches", []):
        fixtures.append({
            "utc_date": m["utcDate"],
            "status": m["status"],
            "group": m.get("group"),
            "home": m["homeTeam"]["name"],
            "away": m["awayTeam"]["name"],
            "home_score": m["score"]["fullTime"]["home"],
            "away_score": m["score"]["fullTime"]["away"],
        })
    return fixtures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matchday", type=int, default=None)
    parser.add_argument("--status", default=None, help="e.g. SCHEDULED, TIMED, FINISHED")
    args = parser.parse_args()

    fixtures = fetch_fixtures(matchday=args.matchday, status=args.status)
    for fx in fixtures:
        score = f"{fx['home_score']}-{fx['away_score']}" if fx["status"] == "FINISHED" else ""
        print(f"{fx['utc_date']}  {fx['group'] or '':10s} {fx['home']} vs {fx['away']}  {fx['status']} {score}")


if __name__ == "__main__":
    main()
