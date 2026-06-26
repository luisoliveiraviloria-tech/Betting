#!/usr/bin/env python3
"""
Fetch live tennis odds from the-odds-api.com. Tennis has no fixtures API
of its own here - the-odds-api's events themselves ARE the fixture list
(player names + commence_time), so there's no separate fetch_fixtures.py
like the football project has.

Tournament sport keys are dynamic (only the currently-running ones are
"active") - list_tennis_sports() discovers them per call instead of
hardcoding a list.

Usage:
  python3 fetch_odds.py                 # list active tennis tournaments
  python3 fetch_odds.py --sport tennis_wta_bad_homburg_open
"""
import argparse
import json
import urllib.request

from env_config import require

API_BASE = "https://api.the-odds-api.com/v4"

# the-odds-api doesn't tag events with court surface. Tournament surface is
# fixed and known in advance, so map sport_key substrings to surface rather
# than querying the model with surface=None for every grass/clay event.
SURFACE_HINTS = {
    "wimbledon": "Grass", "queens": "Grass", "halle": "Grass",
    "eastbourne": "Grass", "mallorca": "Grass", "newport": "Grass",
    "s_hertogenbosch": "Grass", "hertogenbosch": "Grass", "bad_homburg": "Grass",
    "french_open": "Clay", "roland_garros": "Clay", "madrid": "Clay",
    "rome": "Clay", "monte_carlo": "Clay", "barcelona": "Clay",
    "hamburg": "Clay", "umag": "Clay", "bastad": "Clay", "gstaad": "Clay",
    "australian_open": "Hard", "us_open": "Hard", "indian_wells": "Hard",
    "miami": "Hard", "cincinnati": "Hard", "canada": "Hard", "toronto": "Hard",
    "montreal": "Hard", "shanghai": "Hard", "paris_masters": "Hard",
}


def surface_for_sport(sport_key):
    key = sport_key.lower()
    for hint, surface in SURFACE_HINTS.items():
        if hint in key:
            return surface
    return None


def tour_for_sport(sport_key):
    if sport_key.startswith("tennis_atp"):
        return "atp"
    if sport_key.startswith("tennis_wta"):
        return "wta"
    return None


def list_tennis_sports():
    api_key = require("ODDS_API_KEY")
    url = f"{API_BASE}/sports/?apiKey={api_key}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.load(resp)
    return [s for s in data if s["key"].startswith("tennis_") and s["active"]]


def fetch_odds(sport_key, regions="uk,eu", markets="h2h"):
    api_key = require("ODDS_API_KEY")
    url = f"{API_BASE}/sports/{sport_key}/odds/?apiKey={api_key}&regions={regions}&markets={markets}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", default=None, help="e.g. tennis_wta_bad_homburg_open")
    parser.add_argument("--regions", default="uk,eu")
    args = parser.parse_args()

    if args.sport:
        events = fetch_odds(args.sport, regions=args.regions)
        for ev in events:
            print(f"{ev['commence_time']}  {ev['home_team']} vs {ev['away_team']}  "
                  f"({len(ev.get('bookmakers', []))} bookmakers)")
        return

    sports = list_tennis_sports()
    print(f"{len(sports)} active tennis tournaments:")
    for s in sports:
        surface = surface_for_sport(s["key"]) or "unknown"
        print(f"  {s['key']:35s} {s['title']:30s} surface={surface}")


if __name__ == "__main__":
    main()
