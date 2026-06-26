#!/usr/bin/env bash
# Refreshes ATP/WTA match history used by elo_predict.py to build ratings.
#
# Source: JeffSackmann's original tennis_atp/tennis_wta repos are no longer
# publicly reachable (account shows 1 public repo as of 2026). This mirror
# (LuckyLoser91/TennisCourtLog) carries the same schema and is actively
# synced (pushed same-day as of this writing) - re-verify periodically that
# it's still being kept current, and switch sources if it goes stale.
set -euo pipefail
cd "$(dirname "$0")/data"

BASE="https://raw.githubusercontent.com/LuckyLoser91/TennisCourtLog/main"
YEARS=$(seq 2015 2026)

for tour in atp wta; do
  : > "${tour}_matches.csv"
  first=1
  for year in $YEARS; do
    url="$BASE/tennis_${tour}/${tour}_matches_${year}.csv"
    tmp=$(mktemp)
    if curl -fsSL "$url" -o "$tmp"; then
      if [ "$first" = 1 ]; then
        cat "$tmp" >> "${tour}_matches.csv"
        first=0
      else
        tail -n +2 "$tmp" >> "${tour}_matches.csv"
      fi
    else
      echo "warning: failed to fetch ${tour} ${year}, skipping" >&2
    fi
    rm -f "$tmp"
  done
  echo "${tour}: $(wc -l < "${tour}_matches.csv") rows"
done

echo "Updated: $(date -u +%Y-%m-%dT%H:%M:%SZ)" > LAST_UPDATED.txt
echo "Done."
