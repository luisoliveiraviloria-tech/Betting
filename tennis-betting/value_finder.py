#!/usr/bin/env python3
"""
Scan every currently active ATP/WTA tournament's match-winner (h2h) odds,
compare our Elo model's win probability against de-vigged bookmaker odds,
and rank every (match, bookmaker, selection) by edge = model_prob - market_prob.

Tennis has no draw, so this is the entire market - no totals/spreads scan
needed for a first pass (could add games/sets totals later).

Usage:
  python3 value_finder.py
  python3 value_finder.py --top 5
  python3 value_finder.py --min-odds 1.3 --max-odds 4.0
"""
import argparse
import datetime

from elo_predict import build_elo, build_name_index, resolve_player, blended_rating
from fetch_odds import list_tennis_sports, fetch_odds, surface_for_sport, tour_for_sport


def devig_two_way(price_a, price_b):
    raw_a, raw_b = 1.0 / price_a, 1.0 / price_b
    total = raw_a + raw_b
    return raw_a / total, raw_b / total


def scan_tournament(sport, regions, include_live=False):
    tour = tour_for_sport(sport["key"])
    if tour is None:
        return []
    surface = surface_for_sport(sport["key"])
    ratings = build_elo(tour)
    index = build_name_index(ratings)

    events = fetch_odds(sport["key"], regions=regions, markets="h2h")
    now = datetime.datetime.now(datetime.timezone.utc)
    candidates = []
    for ev in events:
        # the-odds-api serves live in-play prices for matches that have
        # started, not just pre-match prices. Our Elo model is pre-match
        # only - comparing it to in-play odds produces meaningless "edge"
        # (e.g. a player who's basically already lost still gets her
        # pre-match win probability, vs. odds reflecting the real score).
        commence = datetime.datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if not include_live and commence <= now:
            continue
        try:
            name_a = resolve_player(ev["home_team"], ratings, index)
            name_b = resolve_player(ev["away_team"], ratings, index)
        except ValueError:
            continue
        ra = blended_rating(ratings[name_a], surface)
        rb = blended_rating(ratings[name_b], surface)
        p_a = 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))
        model_prob = {name_a: p_a, name_b: 1 - p_a}

        for bk in ev.get("bookmakers", []):
            for market in bk.get("markets", []):
                if market["key"] != "h2h":
                    continue
                outcomes = {o["name"]: o["price"] for o in market["outcomes"]}
                if len(outcomes) != 2 or name_a not in outcomes or name_b not in outcomes:
                    continue
                p_mkt_a, p_mkt_b = devig_two_way(outcomes[name_a], outcomes[name_b])
                market_prob = {name_a: p_mkt_a, name_b: p_mkt_b}
                for name in (name_a, name_b):
                    price = outcomes[name]
                    candidates.append({
                        "tournament": sport["title"], "tour": tour, "surface": surface or "?",
                        "match": f"{name_a} vs {name_b}", "selection": name,
                        "bookmaker": bk["title"], "odds": price,
                        "model_prob": model_prob[name], "market_prob": market_prob[name],
                        "edge": model_prob[name] - market_prob[name],
                        "ev": model_prob[name] * price - 1,
                        "commence_time": ev["commence_time"],
                    })
    return candidates


def scan_all(regions="uk,eu", include_live=False):
    sports = list_tennis_sports()
    candidates = []
    for sport in sports:
        candidates.extend(scan_tournament(sport, regions, include_live=include_live))
    return candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regions", default="uk,eu")
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--min-odds", type=float, default=1.3)
    parser.add_argument("--max-odds", type=float, default=4.0)
    parser.add_argument("--include-live", action="store_true",
                         help="also scan matches already in progress (odds are in-play, not pre-match - edge vs. our pre-match model is meaningless)")
    args = parser.parse_args()

    candidates = scan_all(regions=args.regions, include_live=args.include_live)
    candidates = [c for c in candidates if args.min_odds <= c["odds"] <= args.max_odds]
    candidates.sort(key=lambda c: c["edge"], reverse=True)
    top = candidates[: args.top]

    print(f"{len(candidates)} candidate bets in odds range [{args.min_odds}, {args.max_odds}].\n")
    for c in top:
        print(
            f"[{c['tour'].upper()} {c['surface']:5s}] {c['match']:45s} -> {c['selection']:25s} "
            f"[{c['bookmaker']}] odds {c['odds']:.2f}  "
            f"model {c['model_prob']*100:5.1f}%  market {c['market_prob']*100:5.1f}%  "
            f"edge {c['edge']*100:+5.1f}pp  EV {c['ev']*100:+5.1f}%"
        )


if __name__ == "__main__":
    main()
