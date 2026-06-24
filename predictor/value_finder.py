#!/usr/bin/env python3
"""
Scan today's World Cup fixtures across the 1X2 and Over/Under markets,
compare our Elo/Poisson model's probabilities against de-vigged bookmaker
odds, and rank every (match, market, selection, bookmaker) combination by
edge = model_prob - market_implied_prob.

This is a value-betting scanner, not a tipster service: a positive edge
usually means the model is wrong, not that the market is. Treat the
output as a starting point for your own judgement, not a certainty.

Usage:
  python3 value_finder.py                  # today's UTC fixtures, regions=uk,eu
  python3 value_finder.py --date 2026-06-25
  python3 value_finder.py --top 5
"""
import argparse
import datetime
import math

from elo_predict import poisson, predict
from fetch_fixtures import fetch_fixtures
from fetch_odds import fetch_odds
from live_report import match_fixture_to_odds_event, NEUTRAL_HOME_ADV, is_host_nation_match

ONLY_HALF_LINES = True  # skip integer (push-possible) totals lines for clean 2-way devig


def poisson_cdf(lam, k):
    return sum(poisson(lam, i) for i in range(k + 1))


def totals_model_probs(lambda_home, lambda_away, point):
    lam_total = lambda_home + lambda_away
    if point != int(point) + 0.5:
        return None
    under_k = int(math.floor(point))
    p_under = poisson_cdf(lam_total, under_k)
    p_over = 1.0 - p_under
    return {"Over": p_over, "Under": p_under}


def devig_two_way(price_a, price_b):
    raw_a, raw_b = 1.0 / price_a, 1.0 / price_b
    total = raw_a + raw_b
    return raw_a / total, raw_b / total


def devig_three_way(odds):
    raw = {k: 1.0 / v for k, v in odds.items()}
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}


def scan_event(fixture, event, result):
    """Yield candidate bet dicts for one fixture's odds event."""
    lambda_home, lambda_away = result["lambda_home"], result["lambda_away"]
    model_1x2 = {"home": result["p_home"], "draw": result["p_draw"], "away": result["p_away"]}

    for bk in event.get("bookmakers", []):
        for market in bk.get("markets", []):
            if market["key"] == "h2h":
                outcomes = {o["name"]: o["price"] for o in market["outcomes"]}
                if len(outcomes) != 3:
                    continue
                market_probs = devig_three_way(outcomes)
                for name, price in outcomes.items():
                    if name == event["home_team"]:
                        slot, label = "home", fixture["home"]
                    elif name == event["away_team"]:
                        slot, label = "away", fixture["away"]
                    else:
                        slot, label = "draw", "Draw"
                    model_p = model_1x2[slot]
                    market_p = market_probs[name]
                    yield {
                        "market": "1X2", "selection": label, "bookmaker": bk["title"],
                        "odds": price, "model_prob": model_p, "market_prob": market_p,
                        "edge": model_p - market_p, "ev": model_p * price - 1,
                    }

            elif market["key"] == "totals":
                by_point = {}
                for o in market["outcomes"]:
                    by_point.setdefault(o["point"], {})[o["name"]] = o["price"]
                for point, prices in by_point.items():
                    if "Over" not in prices or "Under" not in prices:
                        continue
                    if ONLY_HALF_LINES and point != int(point) + 0.5:
                        continue
                    model_probs = totals_model_probs(lambda_home, lambda_away, point)
                    if model_probs is None:
                        continue
                    p_over_mkt, p_under_mkt = devig_two_way(prices["Over"], prices["Under"])
                    market_probs = {"Over": p_over_mkt, "Under": p_under_mkt}
                    for name in ("Over", "Under"):
                        price = prices[name]
                        model_p = model_probs[name]
                        market_p = market_probs[name]
                        yield {
                            "market": f"O/U {point}", "selection": name, "bookmaker": bk["title"],
                            "odds": price, "model_prob": model_p, "market_prob": market_p,
                            "edge": model_p - market_p, "ev": model_p * price - 1,
                        }


def scan_day(date_str, regions="uk,eu"):
    fixtures = fetch_fixtures(status="SCHEDULED")
    if not fixtures:
        fixtures = fetch_fixtures()
    todays = [fx for fx in fixtures if fx["utc_date"].startswith(date_str)]

    events = fetch_odds(regions=regions, markets="h2h,totals")

    candidates = []
    skipped_host_matches = []
    for fx in todays:
        if is_host_nation_match(fx["home"], fx["away"]):
            skipped_host_matches.append(f"{fx['home']} vs {fx['away']}")
            continue
        try:
            result = predict(fx["home"], fx["away"], home_advantage=NEUTRAL_HOME_ADV)
        except ValueError:
            continue
        ev = match_fixture_to_odds_event(fx, events)
        if ev is None:
            continue
        for cand in scan_event(fx, ev, result):
            cand["fixture"] = f"{fx['home']} vs {fx['away']}"
            cand["utc_date"] = fx["utc_date"]
            candidates.append(cand)
    return candidates, skipped_host_matches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD UTC, default today")
    parser.add_argument("--regions", default="uk,eu")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--min-odds", type=float, default=1.3)
    parser.add_argument("--max-odds", type=float, default=4.0)
    args = parser.parse_args()

    date_str = args.date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    candidates, skipped = scan_day(date_str, regions=args.regions)

    if skipped:
        print("Skipped (host-nation fixture, neutral-venue assumption unreliable - verify venue manually):")
        for s in skipped:
            print(f"  - {s}")
        print()

    candidates = [c for c in candidates if args.min_odds <= c["odds"] <= args.max_odds]
    candidates.sort(key=lambda c: c["edge"], reverse=True)

    print(f"Fixtures scanned for {date_str} UTC. {len(candidates)} candidate bets in odds range "
          f"[{args.min_odds}, {args.max_odds}].\n")
    for c in candidates[: args.top]:
        print(
            f"{c['fixture']:35s} {c['market']:9s} {c['selection']:22s} "
            f"[{c['bookmaker']}] odds {c['odds']:.2f}  "
            f"model {c['model_prob']*100:5.1f}%  market {c['market_prob']*100:5.1f}%  "
            f"edge {c['edge']*100:+5.1f}pp  EV {c['ev']*100:+5.1f}%"
        )


if __name__ == "__main__":
    main()
