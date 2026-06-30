#!/usr/bin/env python3
"""Scan races for +EV value bets — GB, Ireland and overseas.

Uses the Betfair Exchange as the sharp reference (de-vigged win market = true
probabilities), then flags:
  - BACK value: a soft bookmaker priced above true probability.
  - LAY value:  an exchange lay price implying LESS than true probability
    (e.g. an overbet favourite or longshot), net of commission.

Runs on the bundled sample now; switch to live with --source betfair once
BETFAIR_APP_KEY / BETFAIR_SESSION_TOKEN are set.

Example:
    python -m racing.scan --source sample --regions all --min-ev 0.02
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

from racing import value
from racing.feed import Race, load_sample
from racing.regions import COUNTRY_NAMES, resolve_countries


@dataclass
class Opportunity:
    race: str
    country: str
    runner: str
    side: str
    venue: str
    odds: float
    true_prob: float
    ev: float
    stake: float        # stake (back) or liability (lay)


def find_opportunities(race: Race, *, commission: float, min_ev: float,
                       bankroll: float, kelly_fraction: float) -> list[Opportunity]:
    # True probabilities from the de-vigged sharp (exchange) win market.
    sharp_odds = [r.sharp_back for r in race.runners]
    true_probs = value.devig_market(sharp_odds)

    found: list[Opportunity] = []
    for runner, p in zip(race.runners, true_probs):
        # BACK value at soft books (no commission on bookmaker wins).
        for off in runner.offers:
            if off.side != "back":
                continue
            v = value.back_value(p, off.odds, commission=0.0)
            if v.ev > min_ev and v.positive:
                stake = value.stake_from_kelly(v, bankroll, kelly_fraction)
                if stake > 0:
                    found.append(Opportunity(
                        race.name, race.country, runner.name, "BACK",
                        off.venue, off.odds, p, v.ev, stake))

        # LAY value on offered lay prices (commission applies).
        lay_offers = [o for o in runner.offers if o.side == "lay"]
        for off in lay_offers:
            v = value.lay_value(p, off.odds, commission=commission)
            if v.ev > min_ev and v.positive:
                liability = value.stake_from_kelly(v, bankroll, kelly_fraction)
                if liability > 0:
                    found.append(Opportunity(
                        race.name, race.country, runner.name, "LAY",
                        off.venue, off.odds, p, v.ev, liability))

    return found


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", choices=["sample", "betfair"], default="sample")
    p.add_argument("--sample-file", default=os.path.join(
        os.path.dirname(__file__), "..", "data", "sample_races.json"))
    p.add_argument("--regions", nargs="*", default=["all"],
                   help="uk ireland overseas all, or ISO codes (GB IE US AU ...).")
    p.add_argument("--commission", type=float, default=0.02,
                   help="Exchange commission for lay bets (default 0.02 = 2%%).")
    p.add_argument("--min-ev", type=float, default=0.0,
                   help="Minimum EV per £1 to flag (e.g. 0.02 = 2%%).")
    p.add_argument("--bankroll", type=float, default=1000.0)
    p.add_argument("--kelly-fraction", type=float, default=0.25)
    args = p.parse_args()

    countries = set(resolve_countries(args.regions))

    if args.source == "sample":
        races = load_sample(os.path.abspath(args.sample_file))
    else:
        from racing.feed import BetfairFeed
        try:
            races = BetfairFeed().fetch_races(regions=args.regions)
        except RuntimeError as e:
            raise SystemExit(f"Cannot use live Betfair feed:\n  {e}")

    races = [r for r in races if r.country in countries]
    region_label = ", ".join(sorted(COUNTRY_NAMES.get(c, c) for c in countries))
    print(f"Scanning {len(races)} races across: {region_label}")
    print(f"Sharp reference: Betfair Exchange | commission {args.commission:.0%} | "
          f"min EV {args.min_ev:.0%} | {args.kelly_fraction:g}x Kelly | "
          f"bankroll £{args.bankroll:,.0f}\n")

    all_ops: list[Opportunity] = []
    for race in races:
        ops = find_opportunities(
            race, commission=args.commission, min_ev=args.min_ev,
            bankroll=args.bankroll, kelly_fraction=args.kelly_fraction)
        all_ops.extend(ops)

    if not all_ops:
        print("No +EV opportunities at these settings.")
        return

    all_ops.sort(key=lambda o: o.ev, reverse=True)
    print(f"{'EV':>7}  {'SIDE':<4} {'RUNNER':<18} {'VENUE':<10} {'ODDS':>6} "
          f"{'TRUE%':>6} {'STAKE':>9}  RACE")
    print("-" * 88)
    for o in all_ops:
        cc = COUNTRY_NAMES.get(o.country, o.country)
        kind = "stake" if o.side == "BACK" else "liab."
        print(f"{o.ev:>+6.1%}  {o.side:<4} {o.runner:<18} {o.venue:<10} "
              f"{o.odds:>6.2f} {o.true_prob:>5.1%} £{o.stake:>6.2f}({kind})  "
              f"{o.race} [{cc}]")

    staked = sum(o.stake for o in all_ops)
    print(f"\n{len(all_ops)} opportunities | total committed £{staked:,.2f} "
          f"({staked / args.bankroll:.1%} of bankroll)")
    print("Note: sample prices. Add Betfair credentials and --source betfair "
          "for live GB/IRE/overseas markets.")


if __name__ == "__main__":
    main()
