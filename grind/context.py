#!/usr/bin/env python3
"""
Match context: referee, stadium, and kickoff weather for a fixture, plus
what (if anything) each is known to shift. Uses API-Football for fixture
metadata and open-meteo (free, keyless) for weather.

This is CONTEXT, not a signal generator. Our tested rule stands: only bet a
soft price above Pinnacle fair. Context is for (a) vetoing a bet the numbers
like but conditions argue against, (b) explaining a line that looks off.
Known effects, roughly, from the literature and our own cards work:
  - Referee card-rate is the one real, persistent context signal (our E0/E1
    backtest: with-ref beats no-ref every line).
  - Strong wind (>~25 km/h) and heavy rain depress goals slightly and push
    corner counts around; effects are small and mostly already in the line.
  - Stadium/altitude matters mainly for specific venues (e.g. altitude in
    South America); treat as a flag, not a number.

Usage:
  python3 context.py --team "Mexico" --date 2026-07-06
  python3 context.py --team "Boca Juniors"            # defaults to today
"""
import argparse
import datetime
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "predictor"))
from env_config import require  # noqa: E402

AF = "https://v3.football.api-sports.io"


def af_get(path, params, key):
    url = f"{AF}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"x-apisports-key": key})
    with urllib.request.urlopen(req, timeout=25) as r:
        d = json.load(r)
    if d.get("errors"):
        # e.g. free plan only serves a narrow rolling date window
        print(f"API-Football: {d['errors']}")
    return d["response"]


def meteo(city, when):
    """Hourly forecast near kickoff for a city name. Returns None quietly on
    any failure - weather is nice-to-have, never blocking."""
    try:
        g = json.load(urllib.request.urlopen(
            "https://geocoding-api.open-meteo.com/v1/search?name="
            + urllib.parse.quote(city) + "&count=1", timeout=15))
        loc = g["results"][0]
        day = when.date().isoformat()
        f = json.load(urllib.request.urlopen(
            f"https://api.open-meteo.com/v1/forecast?latitude={loc['latitude']}"
            f"&longitude={loc['longitude']}&hourly=temperature_2m,precipitation,"
            f"wind_speed_10m&start_date={day}&end_date={day}", timeout=15))
        hh = f["hourly"]
        idx = min(range(len(hh["time"])),
                  key=lambda i: abs(datetime.datetime.fromisoformat(hh["time"][i])
                                    - when.replace(tzinfo=None)))
        return {"temp": hh["temperature_2m"][idx],
                "rain_mm": hh["precipitation"][idx],
                "wind_kmh": hh["wind_speed_10m"][idx],
                "place": f"{loc['name']}, {loc.get('country_code','')}",
                "alt_m": loc.get("elevation")}
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--team", required=True)
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    args = ap.parse_args()
    key = require("API_FOOTBALL_KEY")

    fixtures = af_get("/fixtures", {"date": args.date}, key)
    fx = [f for f in fixtures
          if args.team.lower() in (f["teams"]["home"]["name"] + " "
                                   + f["teams"]["away"]["name"]).lower()]
    if not fx:
        print(f"No fixture for {args.team!r} on {args.date}")
        return
    for f in fx:
        home, away = f["teams"]["home"]["name"], f["teams"]["away"]["name"]
        ko = datetime.datetime.fromisoformat(f["fixture"]["date"])
        ref = f["fixture"]["referee"] or "TBA"
        venue = f["fixture"]["venue"] or {}
        vname, vcity = venue.get("name") or "?", venue.get("city") or ""
        print(f"{home} vs {away}  [{f['league']['name']}]  KO {ko}")
        print(f"  referee: {ref}")
        print(f"  stadium: {vname}" + (f", {vcity}" if vcity else ""))
        if ref != "TBA":
            print("  -> referee is the one context signal with tested predictive "
                  "value for CARDS; check their card avg before any cards bet.")
        w = meteo(vcity or vname, ko)
        if w:
            flags = []
            if w["wind_kmh"] and w["wind_kmh"] > 25:
                flags.append("WINDY - slight unders/chaos lean")
            if w["rain_mm"] and w["rain_mm"] > 1:
                flags.append("WET - slippery, marginal card/corner noise")
            if w.get("alt_m") and w["alt_m"] > 2000:
                flags.append(f"ALTITUDE {w['alt_m']:.0f}m - real factor in S.America")
            print(f"  weather ~KO ({w['place']}): {w['temp']}°C, "
                  f"rain {w['rain_mm']}mm/h, wind {w['wind_kmh']} km/h"
                  + ("  [" + "; ".join(flags) + "]" if flags else ""))
        print()


if __name__ == "__main__":
    main()
