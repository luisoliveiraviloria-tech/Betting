# Getting a racecard from a time + place

The user gives a **time and a place** (e.g. "21:10 Fairmount Park"). Turn that
into a structured card: runners, current odds, and recent form/speed figures.

## Time zones — read this first

UK/Irish racing portals list **US** evening cards in **UK local time**. So
"21:10 Fairmount Park" is the UK-time label for a US track. Fairmount Park is in
Illinois (US Central). Convert to confirm you have the right race, but you can
usually just use the UK-time label directly on At The Races.

- UK-time label (At The Races, Sporting Life, Timeform, Oddschecker).
- US local post time (Horse Racing Nation, Equibase, track site). 21:10 UK ≈
  15:10 Central during BST/CDT.

Always cross-check the **race number, distance, surface and field size** across
two sources so you know you're reading the same race.

## Sources, in rough order of usefulness

1. **At The Races** — `attheraces.com/racecard/<Track>/<DD-Month-YYYY>/<HHMM>`
   e.g. `.../racecard/Fairmount-Park/28-July-2026/2110`. Clean runner list,
   jockeys, draw; often forecast odds. Good for resolving the race.
2. **Horse Racing Nation entries** —
   `entries.horseracingnation.com/entries-results/<track>/<YYYY-MM-DD>`.
   Morning-line odds and speed/last-race figures for US tracks. Best single
   source for the numbers the model needs.
3. **Timeform / Sporting Life** — form strings, ratings, verdicts (UK-facing).
4. **Oddschecker** — live market prices across bookmakers; use for the odds you
   would actually take and, after the off, the closing price for CLV.
5. **Equibase / official track site** — authoritative US results and past
   performances if you need to dig.

## What to extract for each runner

Fill as much of this as the sources give you; only `name` and `odds` are
required by `rate.py`.

| Field   | Meaning | Where |
|---------|---------|-------|
| `name`  | horse name | any |
| `odds`  | current price you could take, decimal or fractional | Oddschecker / ML |
| `speed` | most recent speed figure (Beyer/Timeform-style) | HRN / Timeform |
| `form`  | recent finishing positions, most recent first, e.g. `[1,2,1]` | any form string |
| `days`  | days since last run | HRN / Equibase |

## Honesty rules when gathering

- **Never invent runners, figures, or odds.** If a source doesn't give a
  number, leave it out — `rate.py` handles missing fields. A guessed speed
  figure is worse than none.
- Note when the field is **incomplete** (some runners missing figures/odds); it
  weakens the probabilities and the user should know.
- Prefer **live/current odds** over morning-line for the actual bet; morning
  line is fine for a first read but drifts.
- Record the **surface** (dirt/turf) and class — they matter to the user's
  turf-focused strategy even though the model doesn't use them yet.
