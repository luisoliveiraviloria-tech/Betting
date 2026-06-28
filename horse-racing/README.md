# Racing Value Scanner — GB, Ireland & overseas

Finds +EV horse-racing bets by treating the **Betfair Exchange as the sharp
reference** (the de-vigged win market = true probabilities) and flagging:

- **BACK value** — a soft bookmaker priced *above* true probability.
- **LAY value** — a lay price (another exchange) implying *less* than true
  probability, net of commission.

It deliberately does **not** try to out-predict the market. The companion
football project proved that a predictive model cannot beat the closing line
(`../prophitbet-predictor/STRATEGY.md`); this uses the sharp market as truth
and hunts for prices elsewhere that beat it — the only approach with a real
positive-EV foundation.

## Why racing (vs football)
- **Betfair Exchange**: ~2–5% commission and you can **lay** — far better than a
  bookmaker's 5–7% overround, and lay-only strategies become possible.
- **Richer data** and **softer minor markets** than the Premier League.
- Caveat: the **tote/pari-mutuel takeout is 15–25%** — only the exchange makes
  the maths viable, so this tool is built around it.

## Coverage — bet anywhere
`racing/regions.py` maps region groups to Betfair country codes:
- `uk` → GB, `ireland` → IE
- `overseas` → US, AU, FR, ZA, AE, HK, JP, SG, NZ, CA
- `all` → everything; or pass raw ISO codes (`--regions GB IE US AU`).

## Run

```bash
cd horse-racing

# Offline sample (works now, no credentials):
python -m racing.scan --source sample --regions all --min-ev 0.02

# UK + Ireland only, 5% minimum edge:
python -m racing.scan --source sample --regions uk ireland --min-ev 0.05

# Tests:
python tests/test_value.py
```

Example output:
```
     EV  SIDE RUNNER          VENUE       ODDS  TRUE%     STAKE  RACE
 +7.3%  LAY  Red Centre       Betfair     4.50 20.2% £ 18.68(liab.)  Flemington 04:00 [Australia]
 +6.8%  BACK Thunder Lad      Bet365      2.30 46.4% £ 13.07(stake)  Ascot 14:30 [UK]
 ...
```

## Going live
The maths is identical for sample or live prices — only the feed changes.

1. Install/keep the **`betfair`** skill (already installed). Use it to mint a
   `BETFAIR_APP_KEY` and `BETFAIR_SESSION_TOKEN`.
2. Export both, ensure outbound access to `api.betfair.com`, then:
   ```bash
   python -m racing.scan --source betfair --regions all --min-ev 0.02
   ```
3. The `betfair` skill also **places** the back/lay orders this scanner
   recommends (`/bf back …`, `/bf lay …`).

### Honest limitations
- **Soft-bookmaker odds aren't wired to a live source yet.** The live feed
  returns sharp exchange prices; to surface BACK value you must merge in
  bookmaker odds (an odds API or the `betting-odds-tracker` skill). Until then
  live mode finds cross-exchange LAY value only.
- Edges are small (1–6%) and vanish fast; this must run continuously near the
  off, not once.
- Profit depends on **book/exchange access** (and not getting limited), not on
  the maths. No tool can guarantee profit — but this one only ever bets when the
  expected value is positive against a sharp reference.

## Layout
```
racing/value.py    de-vig, back/lay EV, Kelly sizing (pure math, tested)
racing/regions.py  GB / IRE / overseas country mapping + Betfair filter
racing/feed.py     Race model, sample loader, live Betfair adapter
racing/scan.py     CLI scanner across regions
data/              illustrative sample races (GB, IE, AU, US)
tests/             value-engine + region tests
```
