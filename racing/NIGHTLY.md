# Daily picks run — runbook for scheduled sessions

A scheduled Routine fires this twice a day in a fresh cloud session:

| UTC (cron) | UK in summer (BST) | What it does |
|---|---|---|
| 05:00 | 06:00 | settle past days, load today's cards, **provisional** picks |
| 10:00 | 11:00 | refresh odds (bookmakers are up by now), **lock** today's picks |

The dashboard is the artifact **https://claude.ai/artifact/Vk4hPhNN7aukiAZbxx2ijV**
("Racecard Picks"). It reads everything from its own database; this run is the only
writer apart from the "placed / price taken" controls on the page.

## Steps

1. Work in the Betting repo on branch `claude/horse-racing-data-betfair-kq1sny`:
   `git fetch origin claude/horse-racing-data-betfair-kq1sny && git checkout claude/horse-racing-data-betfair-kq1sny && git pull`.
2. Pull the bets the user marked on the dashboard: `ArtifactData` action `list`,
   collection `placed`, `out_dir` = `racing/live/_placed` (an empty result is normal).
3. Run the pipeline (add `--lock` when the UK time is 10:00 or later):
   `python -m racing.live_card run --placed racing/live/_placed/placed`
   It exits 1 when the source failed or returned no UK/IRE races; still do steps 4–5,
   because the status document carries the error to the dashboard.
4. Push to the dashboard: read `racing/live/_manifest.json` (a list of
   `{op, collection, doc_id, file_path}`). The store **refuses to overwrite an
   existing document unless the write carries its current version**, and one refused
   entry fails the whole batch. So first `ArtifactData` `list` every collection the
   manifest touches (`meta`, `days`, `days/<date>/meetings` for each date in it) to get
   each document's `version`, add `if_version: <that version>` to every entry whose
   document already exists (new documents need none), then send one `ArtifactData`
   `batch` (chunks of 50 if longer). On a version_mismatch, re-list and resend once.
5. Commit and push `racing/live/` (day files and the ledger; run artefacts are
   gitignored): `git add racing/live && git commit -m "Daily picks run <date> <time>" && git push`.
6. Final message: today's picks (time, course, horse, price) and bank, or the error.

## Rules the run must not break

- Never place bets. Picks are for the user to place manually.
- Never edit `racing/live/<date>/day.json` by hand. If a pick looks wrong, report it
  instead. Locked picks are final for the day.
- If Sporting Life changes layout (`SourceError: no __NEXT_DATA__`), report it; do not
  switch sources silently. Racing Post and Betfair are blocked from cloud IPs.
