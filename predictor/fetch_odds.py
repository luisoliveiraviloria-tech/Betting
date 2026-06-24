#!/usr/bin/env python3
"""
Fetch live FIFA World Cup h2h (1X2) bookmaker odds from the-odds-api.com.

Requires ODDS_API_KEY in predictor/.env (see .env.example).

Usage:
  python3 fetch_odds.py                       # all bookmakers, uk region
  python3 fetch_odds.py --bookmaker betfair_ex_uk
"""
import argparse
import json
import urllib.request

from env_config import require

SPORT_KEY = "soccer_fifa_world_cup"
API_URL = f"https://api.the-odds-api.com/v4/sports/{SPORT_KEY}/odds/"


def fetch_odds(regions="uk", markets="h2h", odds_format="decimal"):
    api_key = require("ODDS_API_KEY")
    url = (
        f"{API_URL}?apiKey={api_key}&regions={regions}"
        f"&markets={markets}&oddsFormat={odds_format}"
    )
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.load(resp)


def best_h2h_odds(event, bookmaker_key=None):
    """Return {home, draw, away} best-available decimal odds (or a specific
    bookmaker's odds if bookmaker_key is given) for an event's h2h market."""
    home_team, away_team = event["home_team"], event["away_team"]
    best = {"home": 0.0, "draw": 0.0, "away": 0.0}
    chosen_book = None
    for bk in event.get("bookmakers", []):
        if bookmaker_key and bk["key"] != bookmaker_key:
            continue
        for market in bk.get("markets", []):
            if market["key"] != "h2h":
                continue
            for outcome in market["outcomes"]:
                price = outcome["price"]
                if outcome["name"] == home_team:
                    slot = "home"
                elif outcome["name"] == away_team:
                    slot = "away"
                else:
                    slot = "draw"
                if price > best[slot]:
                    best[slot] = price
                    if slot == "home":
                        chosen_book = bk["title"]
        if bookmaker_key:
            break
    return best, chosen_book


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regions", default="uk")
    parser.add_argument("--bookmaker", default=None, help="e.g. betfair_ex_uk")
    args = parser.parse_args()

    events = fetch_odds(regions=args.regions)
    for ev in events:
        odds, book = best_h2h_odds(ev, bookmaker_key=args.bookmaker)
        label = book or "best of region"
        print(
            f"{ev['commence_time']}  {ev['home_team']} vs {ev['away_team']}  "
            f"[{label}] H {odds['home']:.2f}  D {odds['draw']:.2f}  A {odds['away']:.2f}"
        )


if __name__ == "__main__":
    main()
