"""Racing region coverage — GB, Ireland and overseas.

Betfair classifies Horse Racing under eventTypeId "7". Markets are filtered by
ISO-3166 country code. These groups let the scanner pull racing from anywhere
you can bet, not just the UK/IRE cards.
"""

from __future__ import annotations

BETFAIR_HORSE_RACING_EVENT_TYPE_ID = "7"

# ISO country codes by region group.
REGIONS: dict[str, list[str]] = {
    "uk":       ["GB"],
    "ireland":  ["IE"],
    "overseas": [
        "US",  # USA
        "AU",  # Australia
        "FR",  # France
        "ZA",  # South Africa
        "AE",  # UAE (Meydan)
        "HK",  # Hong Kong
        "JP",  # Japan
        "SG",  # Singapore
        "NZ",  # New Zealand
        "CA",  # Canada
    ],
}

# Friendly country names for display.
COUNTRY_NAMES: dict[str, str] = {
    "GB": "UK", "IE": "Ireland", "US": "USA", "AU": "Australia",
    "FR": "France", "ZA": "South Africa", "AE": "UAE", "HK": "Hong Kong",
    "JP": "Japan", "SG": "Singapore", "NZ": "New Zealand", "CA": "Canada",
}


def resolve_countries(regions: list[str]) -> list[str]:
    """Expand region group names (uk/ireland/overseas/all) to ISO country codes."""
    if not regions or "all" in regions:
        return sorted({c for codes in REGIONS.values() for c in codes})
    codes: list[str] = []
    for r in regions:
        r = r.lower()
        if r in REGIONS:
            codes.extend(REGIONS[r])
        elif r.upper() in COUNTRY_NAMES:        # allow a raw country code
            codes.append(r.upper())
        else:
            raise ValueError(f"unknown region/country: {r!r}")
    # de-dupe, preserve order
    seen, out = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def betfair_market_filter(regions: list[str]) -> dict:
    """Build a Betfair listMarketCatalogue marketFilter for the given regions."""
    return {
        "eventTypeIds": [BETFAIR_HORSE_RACING_EVENT_TYPE_ID],
        "marketCountries": resolve_countries(regions),
        "marketTypeCodes": ["WIN"],
    }
