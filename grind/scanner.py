#!/usr/bin/env python3
"""
Grind scanner: find +EV bets by comparing bookmaker prices to Pinnacle's
de-vigged fair line (the only edge source that survived our backtests).

Markets covered:
  base scan  : h2h (1X2), totals (goals O/U), spreads (Asian handicap)
  --deep     : + btts, cards O/U, corners O/U (per-event calls, costlier)
  --check    : price any market you see in the Betfair app against fair

Feed limitation: the-odds-api carries Betfair *Exchange* h2h only; for AH /
totals / BTTS / cards / corners the soft Betfair Sportsbook price isn't in the
feed — use --check with the price on your screen. Pinnacle fair is available
for every market above, so the comparison always works.

Usage:
  python3 scanner.py                          # 5 leagues, base markets (~30 cr)
  python3 scanner.py --deep --hours 36        # + btts/cards/corners for near games
  python3 scanner.py --check "Mexico" --market totals --line 2.5 --side Over --price 2.10
  python3 scanner.py --check "Mexico" --market spreads --side "England -0.5" --price 1.95
  python3 scanner.py --check "Mexico" --market btts --side Yes --price 1.80
"""
import argparse
import datetime
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "predictor"))
from env_config import require  # noqa: E402

from config import (LEAGUES, MIN_ODDS, MAX_ODDS, MIN_EV, REGIONS,
                    PREFERRED_BOOKS, KELLY_FRACTION, MAX_STAKE_PCT)
from ledger import current_bankroll  # noqa: E402

API = "https://api.the-odds-api.com/v4"
SHARP = "pinnacle"
BASE_MARKETS = "h2h,totals,spreads"
DEEP_MARKETS = "btts,alternate_totals_cards,alternate_totals_corners"
MARKET_LABEL = {"h2h": "1X2", "totals": "GoalsOU", "spreads": "AH",
                "btts": "BTTS", "alternate_totals_cards": "CardsOU",
                "alternate_totals_corners": "CornersOU"}


def get(url):
    with urllib.request.urlopen(url, timeout=25) as r:
        return json.load(r), r.headers.get("x-requests-remaining")


def devig(prices):
    raws = [1.0 / p for p in prices]
    t = sum(raws)
    return [x / t for x in raws]


def pinnacle_fair(event):
    """{(market, sel_key): fair_prob}. sel_key encodes side+line so any book's
    identical outcome maps onto it."""
    fair = {}
    for bk in event.get("bookmakers", []):
        if bk["key"] != SHARP:
            continue
        for m in bk.get("markets", []):
            mk, outs = m["key"], m["outcomes"]
            if mk == "h2h" and len(outs) == 3:
                for o, p in zip(outs, devig([o["price"] for o in outs])):
                    fair[(mk, o["name"])] = p
            elif mk in ("totals", "alternate_totals_cards",
                        "alternate_totals_corners"):
                byline = {}
                for o in outs:
                    byline.setdefault(o["point"], {})[o["name"]] = o["price"]
                for pt, d in byline.items():
                    if "Over" in d and "Under" in d:
                        po, pu = devig([d["Over"], d["Under"]])
                        fair[(mk, f"Over {pt}")] = po
                        fair[(mk, f"Under {pt}")] = pu
            elif mk == "spreads":
                # pair team A @ point p with team B @ -p
                byabs = {}
                for o in outs:
                    byabs.setdefault(abs(o["point"]), []).append(o)
                for pair in byabs.values():
                    if len(pair) == 2:
                        pa, pb = devig([pair[0]["price"], pair[1]["price"]])
                        fair[(mk, f"{pair[0]['name']} {pair[0]['point']:+g}")] = pa
                        fair[(mk, f"{pair[1]['name']} {pair[1]['point']:+g}")] = pb
            elif mk == "btts":
                if len(outs) == 2:
                    py, pn = devig([outs[0]["price"], outs[1]["price"]])
                    fair[(mk, outs[0]["name"])] = py
                    fair[(mk, outs[1]["name"])] = pn
    return fair


def sel_key(mk, o):
    if mk == "h2h" or mk == "btts":
        return o["name"]
    if mk == "spreads":
        return f"{o['name']} {o['point']:+g}"
    return f"{o['name']} {o['point']}"


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
            if m["key"] not in MARKET_LABEL:   # e.g. betfair h2h_lay
                continue
            for o in m["outcomes"]:
                k = (m["key"], sel_key(m["key"], o))
                if k not in fair or not (MIN_ODDS <= o["price"] <= MAX_ODDS):
                    continue
                ev = o["price"] * fair[k] - 1
                if ev >= MIN_EV:
                    out.append({"league": league,
                                "match": f"{event['home_team']} vs {event['away_team']}",
                                "commence": event["commence_time"],
                                "market": MARKET_LABEL.get(m["key"], m["key"]),
                                "sel": k[1], "book": bk["title"],
                                "odds": o["price"], "fair_prob": fair[k], "ev": ev})
    return out


def kelly_stake(bankroll, odds, fair_prob):
    b = odds - 1
    f = max(0.0, (fair_prob * b - (1 - fair_prob)) / b) * KELLY_FRACTION
    return round(min(f, MAX_STAKE_PCT) * bankroll, 2)


def find_event(key, needle, sports):
    for sport in sports:
        evs, _ = get(f"{API}/sports/{sport}/events/?apiKey={key}")
        for e in evs:
            if needle.lower() in (e["home_team"] + " " + e["away_team"]).lower():
                return sport, e
    return None, None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", default=None)
    ap.add_argument("--all-books", action="store_true")
    ap.add_argument("--deep", action="store_true", help="add btts/cards/corners")
    ap.add_argument("--hours", type=float, default=48, help="--deep window")
    ap.add_argument("--check", default=None, help="team substring")
    ap.add_argument("--market", default="totals",
                    help="h2h|totals|spreads|btts|cards|corners (for --check)")
    ap.add_argument("--line", type=float, default=None)
    ap.add_argument("--side", default=None, help='e.g. Over / Yes / "England -0.5"')
    ap.add_argument("--price", type=float, default=None)
    args = ap.parse_args()

    key = require("ODDS_API_KEY")
    sports = [args.league] if args.league else list(LEAGUES)

    if args.check:
        mk = {"cards": "alternate_totals_cards", "corners": "alternate_totals_corners"}.get(args.market, args.market)
        sport, ev = find_event(key, args.check, sports)
        if not ev:
            print(f"No upcoming event matching {args.check!r}")
            return
        full, rem = get(f"{API}/sports/{sport}/events/{ev['id']}/odds/?apiKey={key}"
                        f"&regions={REGIONS}&markets={mk}&oddsFormat=decimal")
        fair = pinnacle_fair(full)
        sel = args.side if args.line is None else f"{args.side} {args.line}"
        k = (mk, sel)
        if k not in fair:
            avail = sorted(s for m, s in fair if m == mk)
            print(f"No Pinnacle fair for {sel!r}. Available: {avail}")
            return
        evpct = args.price * fair[k] - 1
        bankroll = current_bankroll()
        stake = kelly_stake(bankroll, args.price, fair[k])
        print(f"{ev['home_team']} vs {ev['away_team']} | {MARKET_LABEL.get(mk, mk)} {sel}")
        print(f"  fair {fair[k]*100:.1f}% ({1/fair[k]:.2f}) | your {args.price:.2f} "
              f"-> EV {evpct*100:+.1f}%  {'VALUE - stake £'+format(stake,'.2f') if evpct >= MIN_EV else 'no bet'}")
        return

    bankroll = current_bankroll()
    found, remaining = [], "?"
    now = datetime.datetime.now(datetime.timezone.utc)
    for sport in sports:
        name = LEAGUES.get(sport, sport)
        try:
            events, remaining = get(f"{API}/sports/{sport}/odds/?apiKey={key}"
                                    f"&regions={REGIONS}&markets={BASE_MARKETS}&oddsFormat=decimal")
        except Exception as e:
            print(f"  {name}: fetch failed ({e})")
            continue
        for ev in events:
            found.extend(scan_event(ev, name, betfair_only=not args.all_books))
        if args.deep:
            for ev in events:
                t = datetime.datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
                if (t - now).total_seconds() > args.hours * 3600:
                    continue
                try:
                    full, remaining = get(f"{API}/sports/{sport}/events/{ev['id']}/odds/"
                                          f"?apiKey={key}&regions={REGIONS}&markets={DEEP_MARKETS}&oddsFormat=decimal")
                    found.extend(scan_event(full, name, betfair_only=not args.all_books))
                except Exception:
                    continue

    found.sort(key=lambda c: c["ev"], reverse=True)
    print(f"Bankroll £{bankroll:.2f} | {len(found)} value bets (EV≥{MIN_EV*100:.0f}%, "
          f"odds {MIN_ODDS}-{MAX_ODDS}{', Betfair-only' if not args.all_books else ''}) "
          f"| credits left: {remaining}\n")
    for c in found:
        stake = kelly_stake(bankroll, c["odds"], c["fair_prob"])
        print(f"[{c['commence'][5:16]}] {c['league']:14s} {c['match']:36s} "
              f"{c['market']:9s} {c['sel']:20s} [{c['book']}] {c['odds']:5.2f} "
              f"fair {1/c['fair_prob']:5.2f} EV {c['ev']*100:+5.1f}%  £{stake:.2f}")
    if not found:
        print("Nothing clears the bar. Not betting IS the edge - wait.")


if __name__ == "__main__":
    main()
