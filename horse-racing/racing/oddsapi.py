#!/usr/bin/env python3
"""Live value scanner via The Odds API — find Betfair prices that beat a sharp ref.

The Odds API (the-odds-api.com) has no horse racing, but it covers many soccer
competitions plus other sports, and crucially includes both a sharp reference
(Pinnacle) and Betfair (Sportsbook + Exchange). This scanner:

  1. Pulls h2h odds for a sport.
  2. De-vigs PINNACLE into true probabilities (the sharp anchor).
  3. Flags any BETFAIR price (SB or EX) that pays above true, net of exchange
     commission, and sizes it with fractional Kelly.

Key is read from --api-key or the ODDS_API_KEY env var (never hardcode it).

Examples:
    export ODDS_API_KEY=...   # keep this private
    python -m racing.oddsapi --sport soccer_fifa_world_cup
    python -m racing.oddsapi --sport soccer_epl --commission 0.05 --min-ev 0.02
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
import urllib.request
import urllib.parse
import json

from racing import value

API_ROOT = "https://api.the-odds-api.com/v4"
SHARP_DEFAULT = "pinnacle"
BETFAIR_BOOKS = {            # key -> (label, exchange_commission)
    "betfair_sb_uk": ("Betfair SB", 0.0),
    "betfair_ex_uk": ("Betfair EX", None),    # None -> use --commission
    "betfair_ex_eu": ("Betfair EX", None),
}


def fetch_odds(sport: str, regions: str, api_key: str, markets: str = "h2h") -> list:
    q = urllib.parse.urlencode({
        "apiKey": api_key, "regions": regions,
        "markets": markets, "oddsFormat": "decimal",
    })
    with urllib.request.urlopen(f"{API_ROOT}/sports/{sport}/odds/?{q}", timeout=20) as r:
        return json.loads(r.read())


def _h2h(event: dict, book_key: str) -> dict | None:
    for b in event.get("bookmakers", []):
        if b["key"] == book_key:
            for m in b["markets"]:
                if m["key"] == "h2h":
                    return {o["name"]: o["price"] for o in m["outcomes"]}
    return None


def scan(events: list, *, sharp: str, commission: float, window_h: float,
         min_ev: float, bankroll: float, kelly_fraction: float) -> list[dict]:
    now = datetime.datetime.now(datetime.timezone.utc)
    out, seen = [], set()
    for e in events:
        t = datetime.datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00"))
        hrs = (t - now).total_seconds() / 3600
        if hrs < 0 or hrs > window_h:
            continue
        pin = _h2h(e, sharp)
        if not pin or len(pin) < 2:
            continue
        names = list(pin)
        true = dict(zip(names, value.devig_market([pin[n] for n in names])))
        for bkey, (label, comm) in BETFAIR_BOOKS.items():
            bf = _h2h(e, bkey)
            if not bf:
                continue
            c = commission if comm is None else comm
            for n in names:
                if n not in bf:
                    continue
                v = value.back_value(true[n], bf[n], commission=c)
                if v.ev <= min_ev or not v.positive:
                    continue
                k = (e["id"], n, label)
                if k in seen:
                    continue
                seen.add(k)
                out.append({
                    "ev": v.ev, "selection": n, "book": label, "odds": bf[n],
                    "pinnacle": pin[n], "true": true[n],
                    "stake": value.stake_from_kelly(v, bankroll, kelly_fraction),
                    "match": f"{e['home_team']} v {e['away_team']}",
                    "ko": t.strftime("%a %H:%MZ"),
                })
    out.sort(key=lambda o: o["ev"], reverse=True)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sport", required=True, help="Odds API sport key, e.g. soccer_fifa_world_cup.")
    p.add_argument("--regions", default="uk,eu", help="Regions to pull (need uk for Betfair, eu for Pinnacle).")
    p.add_argument("--sharp", default=SHARP_DEFAULT, help="Sharp reference bookmaker key.")
    p.add_argument("--commission", type=float, default=0.05, help="Betfair Exchange commission (default 0.05 = standard 5%%).")
    p.add_argument("--window-h", type=float, default=24.0, help="Only games kicking off within this many hours.")
    p.add_argument("--min-ev", type=float, default=0.02, help="Minimum EV to flag (default 0.02 = 2%%).")
    p.add_argument("--bankroll", type=float, default=10.0)
    p.add_argument("--kelly-fraction", type=float, default=0.25)
    p.add_argument("--api-key", default=os.environ.get("ODDS_API_KEY"))
    args = p.parse_args()

    if not args.api_key:
        raise SystemExit("Set --api-key or the ODDS_API_KEY env var (keep it private).")

    events = fetch_odds(args.sport, args.regions, args.api_key)
    if isinstance(events, dict):
        raise SystemExit(f"Odds API error: {events}")

    ops = scan(events, sharp=args.sharp, commission=args.commission,
               window_h=args.window_h, min_ev=args.min_ev,
               bankroll=args.bankroll, kelly_fraction=args.kelly_fraction)

    print(f"{args.sport}: {len(events)} events | sharp={args.sharp} | "
          f"EX commission {args.commission:.0%} | min EV {args.min_ev:.0%} | next {args.window_h:g}h")
    if not ops:
        print("No +EV Betfair bets — markets efficient at these settings.")
        return
    print(f"\n{'EV':>6}  {'SELECTION':<16}{'BOOK':<12}{'ODDS':>5}{'PINN':>6}{'TRUE%':>7}{'£STAKE':>8}  MATCH")
    print("-" * 92)
    for o in ops:
        print(f"{o['ev']:>+5.1%}  {o['selection'][:16]:<16}{o['book']:<12}"
              f"{o['odds']:>5.2f}{o['pinnacle']:>6.2f}{o['true']:>6.1%}£{o['stake']:>6.2f}  "
              f"{o['match']} [{o['ko']}]")


if __name__ == "__main__":
    main()
