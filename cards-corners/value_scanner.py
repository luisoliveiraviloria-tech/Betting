#!/usr/bin/env python3
"""
Soft-vs-sharp value scanner for cards (and corners) over/under lines.

This is the edge that actually showed up live: not out-predicting the market,
but finding a SOFT book paying more than the SHARP consensus. We treat
Pinnacle's de-vigged two-way price as "fair", then for every other bookmaker
and line compute EV = soft_odds * pinnacle_fair_prob - 1, and flag the gaps.

It needs no model — Pinnacle is the truth-proxy — so it sidesteps our
unvalidated club model entirely and works on any sport/competition, including
tonight's World Cup national-team games.

Markets: alternate_totals_cards (default) or alternate_totals_corners.

FEED LIMITATION (important): the-odds-api only carries SHARP books for these
markets (Pinnacle and Betfair *Exchange*) - not soft fixed-odds sportsbooks
(Betfair Sportsbook, bet365, etc.), which is exactly where the value lives. So
the auto-scan rarely finds gaps (sharp books agree with each other). Use
--check to test a soft price you've seen in an app against Pinnacle's fair line:

  python3 value_scanner.py --check "Croatia" --line 3.5 --side Over --price 3.2

Usage:
  python3 value_scanner.py                       # auto-scan (sharp books only)
  python3 value_scanner.py --market corners --min-ev 0.05
  python3 value_scanner.py --check Croatia --line 3.5 --side Over --price 3.2
"""
import argparse
import json
import os
import sys
import urllib.request

# reuse the predictor's ODDS_API_KEY loader
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "predictor"))
from env_config import require  # noqa: E402

API = "https://api.the-odds-api.com/v4"
MARKET = {"cards": "alternate_totals_cards", "corners": "alternate_totals_corners"}
SHARP = "pinnacle"


def get(url):
    with urllib.request.urlopen(url, timeout=25) as resp:
        return json.load(resp)


def devig(over, under):
    ro, ru = 1.0 / over, 1.0 / under
    t = ro + ru
    return ro / t, ru / t


def pinnacle_fair(event, market_key):
    """line -> {'Over': p, 'Under': p} from Pinnacle's two-way de-vig."""
    fair = {}
    for bk in event.get("bookmakers", []):
        if bk["key"] != SHARP:
            continue
        for m in bk.get("markets", []):
            if m["key"] != market_key:
                continue
            byline = {}
            for o in m["outcomes"]:
                byline.setdefault(o["point"], {})[o["name"]] = o["price"]
            for pt, d in byline.items():
                if "Over" in d and "Under" in d:
                    po, pu = devig(d["Over"], d["Under"])
                    fair[pt] = {"Over": po, "Under": pu}
    return fair


def scan_event(event, market_key, min_ev, min_odds, max_odds):
    fair = pinnacle_fair(event, market_key)
    if not fair:
        return []
    out = []
    for bk in event.get("bookmakers", []):
        if bk["key"] == SHARP:
            continue
        for m in bk.get("markets", []):
            if m["key"] != market_key:
                continue
            for o in m["outcomes"]:
                pt, side, price = o["point"], o["name"], o["price"]
                if pt not in fair or side not in fair[pt]:
                    continue
                if not (min_odds <= price <= max_odds):
                    continue
                ev = price * fair[pt][side] - 1
                if ev >= min_ev:
                    out.append({
                        "match": f"{event['home_team']} vs {event['away_team']}",
                        "sel": f"{side} {pt}", "book": bk["title"],
                        "odds": price, "fair_prob": fair[pt][side],
                        "fair_odds": 1 / fair[pt][side], "ev": ev,
                        "commence": event["commence_time"],
                    })
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sport", default="soccer_fifa_world_cup")
    ap.add_argument("--market", choices=["cards", "corners"], default="cards")
    ap.add_argument("--regions", default="uk,eu")
    ap.add_argument("--min-ev", type=float, default=0.04)
    ap.add_argument("--min-odds", type=float, default=1.3)
    ap.add_argument("--max-odds", type=float, default=8.0)
    ap.add_argument("--check", default=None,
                    help="substring of a team name; check a soft price vs Pinnacle fair")
    ap.add_argument("--line", type=float, help="line for --check, e.g. 3.5")
    ap.add_argument("--side", choices=["Over", "Under"], help="side for --check")
    ap.add_argument("--price", type=float, help="soft-book price for --check")
    args = ap.parse_args()

    key = require("ODDS_API_KEY")
    mk = MARKET[args.market]
    events = get(f"{API}/sports/{args.sport}/events/?apiKey={key}")

    if args.check:
        ev = next((e for e in events if args.check.lower() in
                   (e["home_team"] + " " + e["away_team"]).lower()), None)
        if not ev:
            print(f"No event matching {args.check!r}")
            return
        url = (f"{API}/sports/{args.sport}/events/{ev['id']}/odds/?apiKey={key}"
               f"&regions={args.regions}&markets={mk}&oddsFormat=decimal")
        fair = pinnacle_fair(get(url), mk)
        if args.line not in fair:
            print(f"Pinnacle has no {args.market} line {args.line} for this match. "
                  f"Lines: {sorted(fair)}")
            return
        fp = fair[args.line][args.side]
        ev_pct = args.price * fp - 1
        print(f"{ev['home_team']} vs {ev['away_team']} | {args.market} "
              f"{args.side} {args.line}")
        print(f"  Pinnacle fair: {fp*100:.1f}%  (fair odds {1/fp:.2f})")
        print(f"  your price {args.price:.2f}  ->  EV {ev_pct*100:+.1f}%  "
              f"{'VALUE' if ev_pct > 0 else 'no value'}")
        return
    print(f"Scanning {len(events)} {args.sport} events for {args.market} value "
          f"(soft book vs Pinnacle fair, min EV {args.min_ev*100:.0f}%)...\n")

    found = []
    for ev in events:
        url = (f"{API}/sports/{args.sport}/events/{ev['id']}/odds/?apiKey={key}"
               f"&regions={args.regions}&markets={mk}&oddsFormat=decimal")
        try:
            full = get(url)
        except Exception:
            continue
        found.extend(scan_event(full, mk, args.min_ev, args.min_odds, args.max_odds))

    found.sort(key=lambda c: c["ev"], reverse=True)
    if not found:
        print("No +EV soft-vs-sharp gaps found.")
        return
    for c in found:
        print(f"[{c['commence'][5:16]}] {c['match']:32s} {c['sel']:11s} "
              f"{c['book']:18s} odds {c['odds']:5.2f}  fair {c['fair_odds']:5.2f} "
              f"({c['fair_prob']*100:4.1f}%)  EV {c['ev']*100:+5.1f}%")


if __name__ == "__main__":
    main()
