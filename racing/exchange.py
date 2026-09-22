"""
Betfair Exchange live prices for today's UK & Irish WIN markets.

The model is trained and validated against **BSP** (Betfair Starting Price), which
does not exist until the race is off. A live card therefore has to be priced from
the pre-off book, and the honest statement of what that costs is in RECON.md s.12:
the morning price removes 11.2% of the no-information log loss against BSP's
13.4%, so betting earlier is betting against a worse-informed price. Two
consequences the caller must live with:

  * Prefer placing at SP where the market allows it (Betfair "Take SP"): that is
    the price the backtest measured. What this module reports is the best price
    available to back RIGHT NOW, and the card's `BET DOWN TO` line is the guard
    that stops a quote going stale between the card and the bet.
  * The book is not normalised to 1.0 pre-off (BSP's is, at 1.0018). We normalise
    explicitly, because `bsp_prob_norm` — the model's `init_score` prior — is a
    normalised probability. Feeding a raw 1/price into it would systematically
    over-state every runner's chance and manufacture fake edges.

Liquidity: the production gate is `pptradedvol >= £500`. `listMarketBook` returns
`totalMatched` per market with a live key; the **free delayed key does not**
(RECON.md s.14). Where it is missing we substitute the depth actually available to
back at the top three price levels, which is the quantity the gate is really
about — can this bet be filled — and label it so the card does not pretend
otherwise.

Auth reuses `quant.betfair_auth`. Note its standing caveat: the interactive login
is blocked by Betfair's Cloudflare edge from datacenter IPs, so run this from the
machine you bet on, or register a client certificate and use `login_cert`.
"""
import argparse
import re
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

import os

from quant.betfair_auth import BetfairSession, login_cert, login_cert_from_env, login_interactive

BETTING_API = "https://api.betfair.com/exchange/betting/rest/v1.0"
HORSE_RACING_EVENT_TYPE = "7"
# listMarketBook is metered by "data points"; 40 markets per call with
# EX_BEST_OFFERS is the size Betfair's own docs use and stays inside the weight.
BOOK_CHUNK = 40
_NUM_PREFIX = re.compile(r"^\s*\d+\.\s*")
_NON_ALNUM = re.compile(r"[^a-z0-9]")


def norm_name(s) -> str:
    """'1. Fat Harry (GB)' -> 'fatharry'. Betfair prefixes the saddle-cloth number
    and suffixes the country; the form archive does neither."""
    t = _NUM_PREFIX.sub("", str(s or ""))
    t = re.sub(r"\s*\([A-Z]{2,3}\)\s*$", "", t.strip())
    return _NON_ALNUM.sub("", t.lower())


def _post(session: BetfairSession, method: str, payload: dict, timeout: int = 30):
    r = requests.post(f"{BETTING_API}/{method}/", json=payload,
                      headers={**session.headers, "Content-Type": "application/json"},
                      timeout=timeout)
    if not r.ok:
        raise RuntimeError(f"{method} -> HTTP {r.status_code}: {r.text[:400]}")
    return r.json()


def login(cert: str = None, key: str = None) -> BetfairSession:
    """Cert file paths > BETFAIR_CLIENT_CERT_B64/KEY_B64 env vars > interactive.
    The env-var path is what makes this work unattended from an ephemeral container."""
    if cert and key:
        return login_cert(cert, key)
    if os.environ.get("BETFAIR_CLIENT_CERT_B64") and os.environ.get("BETFAIR_CLIENT_KEY_B64"):
        return login_cert_from_env()
    return login_interactive()


def list_win_markets(session: BetfairSession, date: str, countries=("GB", "IE")) -> pd.DataFrame:
    """Every UK/IRE horse-racing WIN market with an off time on `date` (local UK day).

    Returns one row per market: market_id, venue, market_start (UTC), and the
    runners with their selection ids.
    """
    day = pd.Timestamp(date).tz_localize("Europe/London") if pd.Timestamp(date).tzinfo is None \
        else pd.Timestamp(date)
    start = day.tz_convert("UTC").to_pydatetime()
    end = (day + timedelta(days=1)).tz_convert("UTC").to_pydatetime()
    js = _post(session, "listMarketCatalogue", {
        "filter": {
            "eventTypeIds": [HORSE_RACING_EVENT_TYPE],
            "marketCountries": list(countries),
            "marketTypeCodes": ["WIN"],
            "marketStartTime": {"from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "to": end.strftime("%Y-%m-%dT%H:%M:%SZ")},
        },
        "marketProjection": ["EVENT", "MARKET_START_TIME", "RUNNER_DESCRIPTION"],
        "sort": "FIRST_TO_START",
        "maxResults": 1000,
    })
    rows = []
    for m in js:
        ev = m.get("event") or {}
        for r in m.get("runners") or []:
            rows.append({
                "market_id": m["marketId"],
                "market_name": m.get("marketName"),
                "venue": ev.get("venue") or ev.get("name"),
                "market_start": pd.to_datetime(m.get("marketStartTime"), utc=True),
                "selection_id": r["selectionId"],
                "runner_name": r.get("runnerName"),
                "name_key": norm_name(r.get("runnerName")),
            })
    return pd.DataFrame(rows)


def market_prices(session: BetfairSession, market_ids: list[str]) -> pd.DataFrame:
    """Best back/lay and available depth per runner, plus market totalMatched."""
    rows = []
    for i in range(0, len(market_ids), BOOK_CHUNK):
        chunk = market_ids[i:i + BOOK_CHUNK]
        js = _post(session, "listMarketBook", {
            "marketIds": chunk,
            "priceProjection": {"priceData": ["EX_BEST_OFFERS"], "virtualise": True},
        })
        for mb in js:
            for r in mb.get("runners") or []:
                ex = r.get("ex") or {}
                backs = ex.get("availableToBack") or []
                lays = ex.get("availableToLay") or []
                rows.append({
                    "market_id": mb["marketId"],
                    "market_status": mb.get("status"),
                    "inplay": bool(mb.get("inplay")),
                    "total_matched": mb.get("totalMatched"),
                    "selection_id": r["selectionId"],
                    "runner_status": r.get("status"),
                    "last_traded": r.get("lastPriceTraded"),
                    "back": backs[0]["price"] if backs else None,
                    "lay": lays[0]["price"] if lays else None,
                    # money you could actually get on at the top 3 back levels
                    "back_depth_gbp": sum(b.get("size", 0) for b in backs[:3]) or None,
                })
    return pd.DataFrame(rows)


def live_prices(session: BetfairSession, date: str, countries=("GB", "IE")) -> pd.DataFrame:
    """One row per runner: venue, off time, name key, back price and liquidity."""
    cat = list_win_markets(session, date, countries)
    if cat.empty:
        return cat
    book = market_prices(session, sorted(cat.market_id.unique().tolist()))
    df = cat.merge(book, on=["market_id", "selection_id"], how="left")
    df = df[df.runner_status.isna() | df.runner_status.eq("ACTIVE")]  # drop non-runners
    df["price"] = df["back"]
    # depth stands in for traded volume when the (delayed) key withholds totalMatched
    df["liquidity_gbp"] = df["total_matched"].fillna(df["back_depth_gbp"])
    df["liquidity_source"] = df["total_matched"].notna().map({True: "totalMatched", False: "back_depth"})
    return df


def normalise_book(df: pd.DataFrame, price_col: str = "price",
                   by: str = "market_id") -> pd.Series:
    """1/price renormalised so each market sums to 1 — the same quantity as
    `bsp_prob_norm`, which is what the model expects as its prior.

    Returns NaN for any market with a missing price, exactly as `market.py` does
    (`book_full`): a partial book cannot be normalised without biasing every
    runner in it.
    """
    inv = 1.0 / df[price_col]
    full = df[price_col].notna().groupby(df[by]).transform("all")
    tot = inv.groupby(df[by]).transform("sum")
    return (inv / tot).where(full)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--cert", help="client certificate .crt for the bot login")
    ap.add_argument("--key", help="client certificate .key for the bot login")
    ap.add_argument("--out", help="write horse,price CSV here (feeds racing.live --prices)")
    args = ap.parse_args()

    s = login(args.cert, args.key)
    df = live_prices(s, args.date)
    if df.empty:
        print(f"no UK/IRE WIN markets found for {args.date}")
        return
    print(f"{df.market_id.nunique()} markets, {len(df)} active runners")
    print(f"liquidity from: {df.liquidity_source.value_counts().to_dict()}")
    if args.out:
        out = df[["runner_name", "price", "venue", "market_start", "liquidity_gbp"]].copy()
        out = out.rename(columns={"runner_name": "horse"})
        out.to_csv(args.out, index=False)
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
