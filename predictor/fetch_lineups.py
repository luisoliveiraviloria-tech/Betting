#!/usr/bin/env python3
"""
Fetch World Cup starting lineups and injury/unavailability news from
API-Football (api-sports.io v3) — something football-data.org's free tier
doesn't expose at all, so the Elo model has no way to know a key striker is
out until now.

Free tier: 100 requests/day, no card required. Sign up at
https://dashboard.api-football.com/register, copy the key from your
dashboard, and put it in predictor/.env as API_FOOTBALL_KEY.

Lineups are normally only published ~1 hour before kickoff, so this is a
final pre-match sanity check, not something to run across a whole
matchday's fixtures in advance. Injury reports are available earlier.

Usage:
  python3 fetch_lineups.py --home Brazil --away Argentina --date 2026-06-25
"""
import argparse
import difflib
import json
import urllib.request

from env_config import require

API_URL = "https://v3.football.api-sports.io"
WORLD_CUP_LEAGUE_ID = 1
SEASON = 2026


def _get(path, params):
    api_key = require("API_FOOTBALL_KEY")
    url = f"{API_URL}{path}?" + "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(url, headers={"x-apisports-key": api_key})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def _loosely_matches(target, candidate):
    return difflib.SequenceMatcher(None, target.lower(), candidate.lower()).ratio() >= 0.5


def find_fixture_id(home_name, away_name, date_str):
    """API-Football fixture id for a World Cup match on a given UTC date.
    Team names are matched loosely since API-Football's spellings don't
    always match eloratings.net's (e.g. "USA" vs "United States")."""
    data = _get("/fixtures", {"date": date_str, "league": WORLD_CUP_LEAGUE_ID, "season": SEASON})
    for m in data.get("response", []):
        api_home = m["teams"]["home"]["name"]
        api_away = m["teams"]["away"]["name"]
        if _loosely_matches(home_name, api_home) and _loosely_matches(away_name, api_away):
            return m["fixture"]["id"]
    return None


def fetch_lineups(fixture_id):
    """[{team, formation, startXI: [names], substitutes: [names]}, ...].
    Empty list if lineups aren't published yet."""
    data = _get("/fixtures/lineups", {"fixture": fixture_id})
    lineups = []
    for entry in data.get("response", []):
        lineups.append({
            "team": entry["team"]["name"],
            "formation": entry.get("formation"),
            "startXI": [p["player"]["name"] for p in entry.get("startXI", [])],
            "substitutes": [p["player"]["name"] for p in entry.get("substitutes", [])],
        })
    return lineups


def fetch_injuries(fixture_id):
    """[{team, player, type, reason}, ...] for this specific fixture."""
    data = _get("/injuries", {"fixture": fixture_id})
    injuries = []
    for entry in data.get("response", []):
        injuries.append({
            "team": entry["team"]["name"],
            "player": entry["player"]["name"],
            "type": entry["player"].get("type"),
            "reason": entry["player"].get("reason"),
        })
    return injuries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True)
    parser.add_argument("--away", required=True)
    parser.add_argument("--date", required=True, help="YYYY-MM-DD UTC")
    args = parser.parse_args()

    fixture_id = find_fixture_id(args.home, args.away, args.date)
    if fixture_id is None:
        raise SystemExit(f"No API-Football fixture found for {args.home} vs {args.away} on {args.date}")

    injuries = fetch_injuries(fixture_id)
    if injuries:
        print("Injuries / unavailable:")
        for inj in injuries:
            print(f"  [{inj['team']}] {inj['player']} - {inj['type']} ({inj['reason']})")
    else:
        print("No injuries reported.")
    print()

    lineups = fetch_lineups(fixture_id)
    if not lineups:
        print("Lineups not published yet (usually ~1h before kickoff).")
        return
    for lu in lineups:
        print(f"{lu['team']} ({lu['formation']}):")
        for name in lu["startXI"]:
            print(f"  {name}")
        print()


if __name__ == "__main__":
    main()
