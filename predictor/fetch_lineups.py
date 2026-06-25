#!/usr/bin/env python3
"""
Fetch World Cup starting lineups and injury/unavailability news from
API-Football (api-sports.io v3) — something football-data.org's free tier
doesn't expose at all, so the Elo model has no way to know a key striker is
out until now.

Free tier: 100 requests/day, no card required. Sign up at
https://dashboard.api-football.com/register, copy the key from your
dashboard, and put it in predictor/.env as API_FOOTBALL_KEY.

Two real free-tier restrictions, found by direct testing (not documented
on the pricing page):
  - The `date` filter on /fixtures only accepts a rolling window of
    roughly yesterday/today/tomorrow ("Free plans do not have access to
    this date, try from <today-1> to <today+1>"). Fine for this project's
    actual use case (checking a match you're about to bet on today), but
    means you can't use this script to browse future or past matchdays.
  - Passing &season= explicitly (or &league=&season= together, or &next=)
    is rejected for the free plan outside 2022-2024, even though the exact
    same current-season fixtures come back fine from a plain &date= query.
    So find_fixture_id() below deliberately never sends &season=.

Lineups are normally only published ~1 hour before kickoff, so this is a
final pre-match sanity check, not something to run across a whole
matchday's fixtures in advance. Injury reports are available earlier.

Usage:
  python3 fetch_lineups.py --home Brazil --away Argentina --date 2026-06-25
"""
import argparse
import json
import urllib.request

from elo_predict import load_team_codes, load_elo_ratings, resolve_team
from env_config import require

API_URL = "https://v3.football.api-sports.io"
WORLD_CUP_LEAGUE_ID = 1

# API-Football spellings that don't match any alias already in
# data/elo_teams.tsv (found by diffing the actual WC26 team list API
# Football returns against elo_predict.resolve_team).
API_FOOTBALL_EXTRA_ALIASES = {
    "türkiye": "TR",
}


def _get(path, params):
    api_key = require("API_FOOTBALL_KEY")
    url = f"{API_URL}{path}?" + "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(url, headers={"x-apisports-key": api_key})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def find_fixture_id(home_name, away_name, date_str):
    """API-Football fixture id for a World Cup match on a given UTC date.

    Resolves both the caller's names and API-Football's own team names to
    eloratings.net's 2-letter codes and compares codes — the same exact-match
    pattern live_report.py's match_fixture_to_odds_event() uses for the-odds-api
    — rather than fuzzy string similarity, which is actively dangerous here
    (e.g. "Iran"/"Iraq" and "Korea Republic"/"Korea DPR" score >0.6 similar
    despite being different teams; mixing those up would attribute one
    team's injury news to its opponent)."""
    alias_to_code = load_team_codes()
    alias_to_code.update(API_FOOTBALL_EXTRA_ALIASES)
    ratings = load_elo_ratings()
    try:
        home_code = resolve_team(home_name, alias_to_code, ratings)
        away_code = resolve_team(away_name, alias_to_code, ratings)
    except ValueError:
        return None

    data = _get("/fixtures", {"date": date_str})
    for m in data.get("response", []):
        if m["league"]["id"] != WORLD_CUP_LEAGUE_ID:
            continue
        try:
            api_home_code = resolve_team(m["teams"]["home"]["name"], alias_to_code, ratings)
            api_away_code = resolve_team(m["teams"]["away"]["name"], alias_to_code, ratings)
        except ValueError:
            continue
        if api_home_code == home_code and api_away_code == away_code:
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
