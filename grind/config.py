"""Grind system settings. Bankroll state lives in bankroll.json (created on
first run of ledger.py); stakes are quarter-Kelly, capped."""

LEAGUES = {
    "soccer_fifa_world_cup": "World Cup",
    "soccer_epl": "EPL",
    "soccer_spain_la_liga": "La Liga",
    "soccer_brazil_campeonato": "Brazil Serie A",
    "soccer_argentina_primera_division": "Argentina Primera",
}

STARTING_BANKROLL = 100.00
MIN_ODDS = 1.50          # user preference
MAX_ODDS = 6.00          # cap variance on a £100 roll
MIN_EV = 0.03            # only bet ≥3% edges vs Pinnacle fair
KELLY_FRACTION = 0.25    # quarter Kelly
MAX_STAKE_PCT = 0.05     # never >5% of bankroll on one bet

# Books we can actually bet (user platforms). Scanner still shows others.
PREFERRED_BOOKS = ("betfair", "betfair_ex_uk", "betfair_sb_uk")

REGIONS = "uk,eu"        # uk = Betfair etc; eu = Pinnacle (the fair-line anchor)
MARKETS = "h2h,totals"   # 4 credits/league/scan; cards via cards-corners/ scanner
