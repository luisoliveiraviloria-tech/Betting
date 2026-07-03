#!/usr/bin/env python3
"""
Grind scanner: find +EV bets across the configured leagues by comparing every
bookmaker's price to Pinnacle's de-vigged fair line (the only edge source that
survived our backtests). Prints stake suggestions from the current bankroll.

h2h fair = 3-way de-vig of Pinnacle H/D/A. totals fair = 2-way de-vig per line.
EV = odds * fair_prob - 1. Only odds within [MIN_ODDS, MAX_ODDS], EV >= MIN_EV.

Usage:
  python3 scanner.py                # scan all configured leagues (~20 credits)
  python3 scanner.py --league soccer_epl
  python3 scanner.py --all-books    # include books you can't bet on
"""
import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "predictor"))
from env_config import require  # noqa: E402

from config import (LEAGUES, MIN_ODDS, MAX_ODDS, MIN_EV, REGIONS, MARKETS,
                    PREFERRED_BOOKS, KELLY_FRACTION, MAX_STAKE_PCT)
from ledger import current_bankroll  # noqa: E402

API = "https://api.the-odds-api.com/v4"
SHARP = "pinnacle"


def get(url):
    with urllib.request.urlopen(url, timeout=25) as r:
        return json.load(r), r.headers.get("x-requests-remaining")


def devig(prices):
    raws = [1.0 / p for p in prices]
    t = sum(raws)
    return [x / t for x in raws]


def pinnacle_fair(event):
    """{('h2h', outcome_name): p} and {('totals', point, side): p}."""
    fair = {}
    for bk in event.get("bookmakers", []):
        if bk["key"] != SHARP:
            continue
        for m in bk.get("markets", []):
            if m["key"] == "h2h" and len(m["outcomes"]) == 3:
                ps = devig([o["price"] for o in m["outcomes"]])
                for o, p in zip(m["outcomes"], ps):
                    fair[("h2h", o["name"])] = p
            elif m["key"] == "totals":
                byline = {}
                for o in m["outcomes"]:
                    byline.setdefault(o["point"], {})[o["name"]] = o["price"]
                for pt, d in byline.items():
                    if "Over" in d and "Under" in d:
                        po, pu = devig([d["Over"], d["Under"]])
                        fair[("totals", pt, "Over")] = po
                        fair[("totals", pt, "Under")] = pu
    return fair


def scan_event(event, league, betfair_only=True):
    fair = pinnacle_fair(event)
    if not fair:
        return []
    out = []
    for bk in event.get("bookmakers", []):
        if bk["key"] == SHARP:
            continue
        if betfair_only and bk["key"] not in PREFERRED_BOOKS:
            continue
        for m in bk.get("markets", []):
            for o in m["outcomes"]:
                if m["key"] == "h2h":
                    key, sel = ("h2h", o["name"]), o["name"]
                elif m["key"] == "totals":
                    key, sel = ("totals", o.get("point"), o["name"]), f"{o['name']} {o.get('point')}"
                else:
                    continue
                if key not in fair or not (MIN_ODDS <= o["price"] <= MAX_ODDS):
                    continue
                p = fair[key]
                ev = o["price"] * p - 1
                if ev >= MIN_EV:
                    out.append({
                        "league": league, "match": f"{event['home_team']} vs {event['away_team']}",
                        "commence": event["commence_time"], "market": m["key"],
                        "sel": sel, "book": bk["title"], "odds": o["price"],
                        "fair_prob": p, "ev": ev,
                    })
    return out


def kelly_stake(bankroll, odds, fair_prob):
    b = odds - 1
    f = (fair_prob * b - (1 - fair_prob)) / b
    f = max(0.0, f) * KELLY_FRACTION
    return round(min(f, MAX_STAKE_PCT) * bankroll, 2)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", default=None, help="single sport key")
    ap.add_argument("--all-books", action="store_true")
    args = ap.parse_args()

    key = require("ODDS_API_KEY")
    bankroll = current_bankroll()
    leagues = {args.league: LEAGUES.get(args.league, args.league)} if args.league else LEAGUES

    found, remaining = [], "?"
    for sport, name in leagues.items():
        url = (f"{API}/sports/{sport}/odds/?apiKey={key}&regions={REGIONS}"
               f"&markets={MARKETS}&oddsFormat=decimal")
        try:
            events, remaining = get(url)
        except Exception as e:
            print(f"  {name}: fetch failed ({e})")
            continue
        for ev in events:
            found.extend(scan_event(ev, name, betfair_only=not args.all_books))

    found.sort(key=lambda c: c["ev"], reverse=True)
    print(f"Bankroll £{bankroll:.2f} | {len(found)} value bets "
          f"(EV≥{MIN_EV*100:.0f}%, odds {MIN_ODDS}-{MAX_ODDS}"
          f"{', Betfair only' if not args.all_books else ''}) | API credits left: {remaining}\n")
    for c in found:
        stake = kelly_stake(bankroll, c["odds"], c["fair_prob"])
        print(f"[{c['commence'][5:16]}] {c['league']:16s} {c['match']:38s} "
              f"{c['market']:6s} {c['sel']:18s} [{c['book']}] odds {c['odds']:5.2f} "
              f"fair {1/c['fair_prob']:5.2f} EV {c['ev']*100:+5.1f}%  stake £{stake:.2f}")
    if not found:
        print("Nothing clears the bar today. Not betting IS the edge — wait.")


if __name__ == "__main__":
    main()
