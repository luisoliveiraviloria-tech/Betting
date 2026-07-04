#!/usr/bin/env python3
"""Download historical ATP/WTA match data for backtesting and weight re-fits.

Two sources (docs/SYSTEM.md section 8):

  sackmann      Jeff Sackmann's GitHub datasets - detailed results plus
                serve/return match stats; the backbone for building surface
                Elo. Tries both master and main branches.
                https://github.com/JeffSackmann/tennis_atp  (and tennis_wta)

  tennisdata    tennis-data.co.uk yearly .xlsx files - results WITH bookmaker
                closing odds (Pinnacle/B365 etc.), essential for backtesting
                edge realism and simulating CLV. HTTP-only site.

Usage:
    python3 fetch_data.py --tour atp --years 2022-2025
    python3 fetch_data.py --tour wta --years 2020-2025 --source tennisdata
"""

import argparse
import os
import sys
import urllib.request

SOURCES = {
    "sackmann": [
        "https://raw.githubusercontent.com/JeffSackmann/tennis_{tour}/master/{tour}_matches_{year}.csv",
        "https://raw.githubusercontent.com/JeffSackmann/tennis_{tour}/main/{tour}_matches_{year}.csv",
    ],
    # ATP lives under /{year}/, WTA under /{year}w/
    "tennisdata": [
        "http://www.tennis-data.co.uk/{year}{w}/{year}.xlsx",
    ],
}


def parse_years(spec: str) -> list:
    if "-" in spec:
        a, b = spec.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(spec)]


def fetch(urls: list, dest: str) -> bool:
    for url in urls:
        try:
            print(f"fetching {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
                out.write(resp.read())
            print(f"  -> {dest} ({os.path.getsize(dest):,} bytes)")
            return True
        except Exception as e:  # noqa: BLE001 - try next mirror
            print(f"  failed: {e}", file=sys.stderr)
            if os.path.exists(dest):
                os.remove(dest)
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tour", choices=["atp", "wta"], required=True)
    ap.add_argument("--years", required=True, help="e.g. 2024 or 2022-2025")
    ap.add_argument("--source", choices=list(SOURCES), default="tennisdata",
                    help="default: tennisdata (includes closing odds)")
    ap.add_argument("--out", default=None, help="output dir (default: data/raw/<source>/<tour>)")
    args = ap.parse_args()

    out_dir = args.out or os.path.join(os.path.dirname(__file__), "raw", args.source, args.tour)
    os.makedirs(out_dir, exist_ok=True)

    ext = "csv" if args.source == "sackmann" else "xlsx"
    failures = 0
    for year in parse_years(args.years):
        urls = [u.format(tour=args.tour, year=year, w="w" if args.tour == "wta" else "")
                for u in SOURCES[args.source]]
        dest = os.path.join(out_dir, f"{args.tour}_matches_{year}.{ext}")
        if os.path.exists(dest):
            print(f"skip {dest} (exists)")
            continue
        if not fetch(urls, dest):
            failures += 1
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
