#!/usr/bin/env python3
"""
Fetch live World Cup fixtures (football-data.org) and live bookmaker odds
(the-odds-api.com), run the Elo model on each fixture, and print model vs
market side by side.

Usage:
  python3 live_report.py
  python3 live_report.py --matchday 3
  python3 live_report.py --bookmaker betfair_ex_uk
"""
import argparse

from elo_predict import load_team_codes, load_elo_ratings, resolve_team, predict
from fetch_fixtures import fetch_fixtures
from fetch_odds import fetch_odds, best_h2h_odds

NEUTRAL_HOME_ADV = 0.0  # World Cup matches are played at neutral venues

# WC 2026 is co-hosted by the US, Canada and Mexico. Group-stage matches
# involving a host nation are very often actually played in that nation's
# own stadium (real crowd advantage), not a neutral venue — but the free
# football-data.org tier doesn't return a venue field, so we can't confirm
# this automatically. Flag these fixtures instead of silently assuming
# neutral; verify the actual venue before trusting the model's probabilities.
HOST_NATIONS = {"united states", "canada", "mexico"}


def is_host_nation_match(home_name, away_name):
    return home_name.strip().lower() in HOST_NATIONS or away_name.strip().lower() in HOST_NATIONS


def implied_probs(odds):
    """Convert decimal 1X2 odds to probabilities, removing the bookmaker margin."""
    if not all(odds.values()):
        return None
    raw = {k: 1.0 / v for k, v in odds.items()}
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}


def match_fixture_to_odds_event(fixture, events):
    alias_to_code = load_team_codes()
    ratings = load_elo_ratings()
    try:
        home_code = resolve_team(fixture["home"], alias_to_code, ratings)
        away_code = resolve_team(fixture["away"], alias_to_code, ratings)
    except ValueError:
        return None
    for ev in events:
        try:
            ev_home = resolve_team(ev["home_team"], alias_to_code, ratings)
            ev_away = resolve_team(ev["away_team"], alias_to_code, ratings)
        except ValueError:
            continue
        if ev_home == home_code and ev_away == away_code:
            return ev
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matchday", type=int, default=None)
    parser.add_argument("--bookmaker", default=None, help="e.g. betfair_ex_uk")
    parser.add_argument("--regions", default="uk")
    parser.add_argument(
        "--check-injuries", action="store_true",
        help="Look up API-Football injury reports per fixture (requires "
        "API_FOOTBALL_KEY in .env; see fetch_lineups.py). One request per "
        "fixture shown, so use with --matchday to keep it small.",
    )
    args = parser.parse_args()

    # football-data.org's status query param doesn't match its own response
    # status string ("TIMED") — only "SCHEDULED" actually works as a filter
    # value, for both matchday and non-matchday queries (confirmed by direct
    # testing). Keep the unfiltered fallback for the end-of-tournament edge
    # case where there are no upcoming matches left at all, but still drop
    # FINISHED ones so a leftover finished match in the requested matchday
    # doesn't get treated as upcoming.
    fixtures = fetch_fixtures(matchday=args.matchday, status="SCHEDULED")
    if not fixtures:
        fixtures = [fx for fx in fetch_fixtures(matchday=args.matchday) if fx["status"] != "FINISHED"]
    events = fetch_odds(regions=args.regions)

    for fx in fixtures:
        flag = "  [HOST-NATION MATCH - verify venue, neutral assumption may be wrong]" if is_host_nation_match(fx["home"], fx["away"]) else ""
        print(f"{fx['home']} vs {fx['away']}  ({fx['group']}, {fx['utc_date']}){flag}")
        try:
            result = predict(fx["home"], fx["away"], home_advantage=NEUTRAL_HOME_ADV)
        except ValueError as e:
            print(f"  model: {e}")
            print()
            continue

        print(
            f"  model:  Home {result['p_home']*100:5.1f}%  "
            f"Draw {result['p_draw']*100:5.1f}%  Away {result['p_away']*100:5.1f}%"
        )

        ev = match_fixture_to_odds_event(fx, events)
        if ev is None:
            print("  market: no odds found")
            print()
            continue

        odds, book = best_h2h_odds(ev, bookmaker_key=args.bookmaker)
        probs = implied_probs(odds)
        label = book or "best of region"
        if probs is None:
            print(f"  market: [{label}] odds incomplete (H {odds['home']} D {odds['draw']} A {odds['away']})")
        else:
            print(
                f"  market: [{label}] Home {probs['home']*100:5.1f}%  "
                f"Draw {probs['draw']*100:5.1f}%  Away {probs['away']*100:5.1f}%  "
                f"(odds H {odds['home']:.2f} D {odds['draw']:.2f} A {odds['away']:.2f})"
            )

        if args.check_injuries:
            from fetch_lineups import find_fixture_id, fetch_injuries
            try:
                fixture_id = find_fixture_id(fx["home"], fx["away"], fx["utc_date"][:10])
                injuries = fetch_injuries(fixture_id) if fixture_id is not None else []
            except (RuntimeError, OSError) as e:
                injuries = []
                print(f"  injuries: lookup failed ({e})")
            for inj in injuries:
                print(f"  [!] {inj['team']}: {inj['player']} - {inj['type']} ({inj['reason']})")

        print()


if __name__ == "__main__":
    main()
