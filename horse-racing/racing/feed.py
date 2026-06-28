"""Race data feed — sample (offline) now, Betfair Exchange (live) when ready.

A Race carries each runner's SHARP exchange prices (the truth) plus any number
of soft-bookmaker BACK offers and exchange LAY offers to hunt for value in.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class Offer:
    venue: str           # e.g. "Bet365", "Betfair" (exchange)
    side: str            # "back" or "lay"
    odds: float          # decimal odds available


@dataclass
class Runner:
    name: str
    sharp_back: float                       # exchange best back (for true-prob de-vig)
    sharp_lay: float | None = None          # exchange best lay
    offers: list[Offer] = field(default_factory=list)


@dataclass
class Race:
    market_id: str
    country: str         # ISO code, e.g. "GB"
    course: str
    off_time: str        # ISO time or "HH:MM"
    runners: list[Runner]

    @property
    def name(self) -> str:
        return f"{self.course} {self.off_time}"


# --------------------------------------------------------------------------- #
# Offline sample feed
# --------------------------------------------------------------------------- #
def load_sample(path: str) -> list[Race]:
    """Load races from a JSON file (see data/sample_races.json)."""
    with open(path) as f:
        raw = json.load(f)
    races = []
    for r in raw["races"]:
        runners = [
            Runner(
                name=rn["name"],
                sharp_back=rn["sharp_back"],
                sharp_lay=rn.get("sharp_lay"),
                offers=[Offer(**o) for o in rn.get("offers", [])],
            )
            for rn in r["runners"]
        ]
        races.append(Race(
            market_id=r["market_id"], country=r["country"],
            course=r["course"], off_time=r["off_time"], runners=runners,
        ))
    return races


# --------------------------------------------------------------------------- #
# Live Betfair Exchange feed (requires credentials)
# --------------------------------------------------------------------------- #
class BetfairFeed:
    """Live feed via the Betfair Exchange API.

    Requires env vars BETFAIR_APP_KEY and BETFAIR_SESSION_TOKEN (the installed
    `betfair` skill can mint these). Network access to api.betfair.com must be
    allowed. This is the seam where live odds enter the system; the maths in
    value.py is identical whether the prices are sample or live.
    """

    CATALOGUE_URL = "https://api.betfair.com/exchange/betting/rest/v1.0/listMarketCatalogue/"
    BOOK_URL = "https://api.betfair.com/exchange/betting/rest/v1.0/listMarketBook/"

    def __init__(self, app_key: str | None = None, session_token: str | None = None):
        self.app_key = app_key or os.environ.get("BETFAIR_APP_KEY")
        self.session_token = session_token or os.environ.get("BETFAIR_SESSION_TOKEN")
        if not self.app_key or not self.session_token:
            raise RuntimeError(
                "Live Betfair feed needs BETFAIR_APP_KEY and BETFAIR_SESSION_TOKEN. "
                "Use the installed `betfair` skill to obtain them, then export both. "
                "Until then run the scanner with --source sample."
            )

    def _headers(self) -> dict:
        return {
            "X-Application": self.app_key,
            "X-Authentication": self.session_token,
            "Content-Type": "application/json",
        }

    def fetch_races(self, regions: list[str], max_results: int = 50) -> list[Race]:
        """Pull WIN markets for the regions and build Race objects from prices.

        Implemented against the documented Betfair REST shape. Kept thin on
        purpose: it returns the SHARP exchange prices; soft-book offers come
        from your own bookmaker odds source and are merged in by the caller.
        """
        import requests  # local import: only needed for the live path
        from .regions import betfair_market_filter

        catalogue_req = {
            "filter": betfair_market_filter(regions),
            "maxResults": str(max_results),
            "marketProjection": ["EVENT", "MARKET_START_TIME", "RUNNER_DESCRIPTION"],
            "sort": "FIRST_TO_START",
        }
        cat = requests.post(self.CATALOGUE_URL, headers=self._headers(),
                            json=catalogue_req, timeout=15).json()

        races: list[Race] = []
        for market in cat:
            ids = [r["selectionId"] for r in market.get("runners", [])]
            names = {r["selectionId"]: r.get("runnerName", str(r["selectionId"]))
                     for r in market.get("runners", [])}
            book_req = {
                "marketIds": [market["marketId"]],
                "priceProjection": {"priceData": ["EX_BEST_OFFERS"]},
            }
            book = requests.post(self.BOOK_URL, headers=self._headers(),
                                 json=book_req, timeout=15).json()
            runners = []
            for rb in (book[0]["runners"] if book else []):
                ex = rb.get("ex", {})
                back = ex.get("availableToBack") or [{}]
                lay = ex.get("availableToLay") or [{}]
                runners.append(Runner(
                    name=names.get(rb["selectionId"], str(rb["selectionId"])),
                    sharp_back=back[0].get("price", 0.0) or 0.0,
                    sharp_lay=lay[0].get("price"),
                ))
            event = market.get("event", {})
            races.append(Race(
                market_id=market["marketId"],
                country=event.get("countryCode", "?"),
                course=event.get("venue", event.get("name", "?")),
                off_time=market.get("marketStartTime", "")[11:16],
                runners=[r for r in runners if r.sharp_back > 1.0],
            ))
        return races
