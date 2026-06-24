#!/usr/bin/env python3
"""
Point-in-time national-team Elo history from eloratings.net's per-team
.tsv files (e.g. https://www.eloratings.net/Brazil.tsv) — one row per match
ever played by that team, with its Elo rating immediately after that match.

elo_world.tsv (used by elo_predict.py) only has *today's* snapshot rating.
calibrate.py used that snapshot as a recency-windowed proxy for match-time
strength, which is a known source of staleness bias. This module fetches
each team's real rating as of any past date instead, removing that bias.

Cached to data/elo_history/<CODE>.tsv after first fetch — delete that
directory (or pass force_refresh=True) to re-pull from eloratings.net.

Usage:
  python3 elo_history.py --team Brazil --date 2023-06-15
"""
import argparse
import datetime
import os
import unicodedata
import urllib.parse
import urllib.request

from elo_predict import DATA_DIR, load_team_codes, load_elo_ratings

HISTORY_DIR = os.path.join(DATA_DIR, "elo_history")
BASE_URL = "https://www.eloratings.net/"


def code_to_name_map():
    """code -> canonical full name (first alias column in elo_teams.tsv),
    used to build the per-team .tsv URL slug."""
    mapping = {}
    with open(os.path.join(DATA_DIR, "elo_teams.tsv"), encoding="utf-8") as f:
        for line in f:
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 2:
                continue
            mapping[fields[0]] = fields[1]
    return mapping


def _cache_path(code):
    return os.path.join(HISTORY_DIR, f"{code}.tsv")


def _slugify(name):
    """eloratings.net's .tsv filenames use plain ASCII (e.g. 'Curacao', not
    'Curaçao') with spaces as underscores."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return urllib.parse.quote(ascii_name.replace(" ", "_"))


def _fetch(name):
    url = f"{BASE_URL}{_slugify(name)}.tsv"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8")


def get_team_history(code, code_to_name=None, force_refresh=False):
    """[(date, elo_after), ...] for every match `code` has played, sorted
    ascending by date. [] if the team can't be resolved or fetch fails."""
    path = _cache_path(code)
    if not force_refresh and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
    else:
        name = (code_to_name or code_to_name_map()).get(code)
        if name is None:
            return []
        try:
            text = _fetch(name)
        except OSError:
            return []
        os.makedirs(HISTORY_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    rows = []
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) < 12:
            continue
        try:
            year, month, day = int(fields[0]), int(fields[1]), int(fields[2])
            home_code, away_code = fields[3], fields[4]
            home_elo_after, away_elo_after = int(fields[10]), int(fields[11])
            # Some pre-1990s matches have month/day "00" placeholders for
            # unknown exact dates — skip them rather than crash; the years
            # this affects predate any plausible calibration window anyway.
            date = datetime.date(year, month, day)
        except ValueError:
            continue
        elo_after = home_elo_after if home_code == code else away_elo_after
        rows.append((date, elo_after))
    rows.sort(key=lambda r: r[0])
    return rows


def elo_as_of(code, date, history=None, code_to_name=None):
    """`code`'s Elo rating as of `date` (rating after its last match on or
    before that date). None if it has no recorded match before `date`."""
    if history is None:
        history = get_team_history(code, code_to_name=code_to_name)
    result = None
    for match_date, elo in history:
        if match_date <= date:
            result = elo
        else:
            break
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team", required=True)
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    alias_to_code = load_team_codes()
    code = alias_to_code.get(args.team.strip().lower())
    if code is None:
        raise SystemExit(f"Could not resolve team {args.team!r}")

    date = datetime.date.fromisoformat(args.date)
    elo = elo_as_of(code, date, code_to_name=code_to_name_map())
    _, current_elo = load_elo_ratings()[code]
    print(f"{args.team} Elo as of {date}: {elo} (current snapshot: {current_elo})")


if __name__ == "__main__":
    main()
