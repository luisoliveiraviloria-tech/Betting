#!/usr/bin/env bash
# Refreshes the free, no-API-key data sources used by elo_predict.py.
set -euo pipefail
cd "$(dirname "$0")/data"

curl -fsSL "https://raw.githubusercontent.com/martj42/international_results/master/results.csv" -o international_results.csv
curl -fsSL "https://www.eloratings.net/World.tsv" -o elo_world.tsv
curl -fsSL "https://www.eloratings.net/en.teams.tsv" -o elo_teams.tsv

echo "Updated: $(date -u +%Y-%m-%dT%H:%M:%SZ)" > LAST_UPDATED.txt
echo "Done. $(wc -l < international_results.csv) match rows, $(wc -l < elo_world.tsv) Elo-rated teams."
