# Dashboard source

`racecard.html` is the source of the "Racecard Picks" artifact
(https://claude.ai/artifact/Vk4hPhNN7aukiAZbxx2ijV). It reads everything from the
artifact's `db` (see "Daily picks system" in the repo's CLAUDE.md for the document
layout). To change the page: edit this file, publish it with the Artifact tool using
`url` = the link above (capabilities carry forward), then commit.

For a self-hosted site later, swap the `claude.use("db")` reads for `fetch()` of
`racing/live/<date>/day.json` and `racing/live/ledger.json` (see "Website plan").
