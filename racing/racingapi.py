"""
The Racing API client — racecards (today's declarations) and results (form history).

Why this module exists
----------------------
The model is trained on the Kaggle form archive, which ends 2026-06-03. To bet
today we need two live streams:

  1. TODAY'S RACECARD — course, going, distance, class, draw, weight, official
     rating, jockey, trainer, sire/dam, for every declared runner.
  2. YESTERDAY'S RESULTS — finishing position and, critically, **RPR**, because
     `rp_rpr_last` (last run's Racing Post rating) is the single strongest feature
     and it is a PREVIOUS-run value. Today's card does not need today's RPR; it
     needs the RPR of each runner's last race. Without a rolling results feed the
     archive goes stale and `rp_rpr_minus_or` degrades to NaN for any horse that
     has run since 2026-06-03.

What the plans actually give (read from api.theracingapi.com/openapi.json,
version 1.4.4, checked 2026-09-22 — this corrects RECON.md s.14, which recorded
that official rating and draw needed the £27.99 Basic plan):

    endpoint                     plan      what it has that we need
    /v1/racecards/free           FREE      course, date, off_time, race_class,
                                           rating_band, type, going, surface,
                                           distance_f, field_size, prize, and per
                                           runner: number, DRAW, lbs, OFR, sire,
                                           dam, damsire, sex, age, headgear,
                                           jockey, trainer. Today + tomorrow.
    /v1/results/today/free       FREE      today's results: position, or, rpr,
                                           tsr, sp_dec, bsp, btn, ovr_btn, time
    /v1/racecards/basic          BASIC     adds rpr/ts/performance_rating on the
                                           CARD (we do not need these) plus
                                           going_detailed, comment, spotlight
    /v1/results                  STANDARD  historic results, last 12 months —
                                           the only way to backfill the
                                           2026-06-03 -> today form gap

So the free tier covers the daily loop, and a Standard subscription is needed
only to backfill the gap once (or to re-backfill after an outage). Whether the
free results feed still POPULATES `rpr` is the open question — RECON.md s.14
recorded it returning empty since June 2026. `probe()` answers that in one call
with a real key; do not take either answer on faith.

Auth is HTTP Basic (username = API key, password = secret), from
RACING_API_USERNAME / RACING_API_PASSWORD. Rate limits are per the spec: 1 req/s
on free endpoints, 5 req/s on /v1/results.
"""
import argparse
import json
import os
import time
from pathlib import Path

import requests

BASE = "https://api.theracingapi.com"
UK_IRE = ("gb", "ire")

# rate limits published in the OpenAPI description for each endpoint
RATE_LIMIT_S = {
    "/v1/racecards/free": 1.0,
    "/v1/racecards/basic": 0.5,
    "/v1/racecards/standard": 0.5,
    "/v1/racecards/pro": 0.5,
    "/v1/results/today/free": 1.0,
    "/v1/results/today": 0.5,
    "/v1/results": 0.2,
    "/v1/courses": 1.0,
}


class RacingAPIError(RuntimeError):
    pass


class RacingAPI:
    def __init__(self, username: str = None, password: str = None, timeout: int = 60):
        self.username = username or os.environ.get("RACING_API_USERNAME", "")
        self.password = password or os.environ.get("RACING_API_PASSWORD", "")
        if not self.username or not self.password:
            raise RacingAPIError(
                "Set RACING_API_USERNAME and RACING_API_PASSWORD (the API key and "
                "secret from theracingapi.com). A free account is enough for the "
                "daily loop; /v1/results needs Standard.")
        self.timeout = timeout
        self.s = requests.Session()
        self.s.auth = (self.username, self.password)
        self._last_call = {}

    def _throttle(self, path: str) -> None:
        gap = RATE_LIMIT_S.get(path, 1.0)
        last = self._last_call.get(path)
        if last is not None:
            wait = gap - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_call[path] = time.monotonic()

    def get(self, path: str, **params) -> dict:
        self._throttle(path)
        r = self.s.get(BASE + path, params={k: v for k, v in params.items() if v is not None},
                       timeout=self.timeout)
        if r.status_code == 401:
            raise RacingAPIError("401 — credentials rejected. Check RACING_API_USERNAME/PASSWORD.")
        if r.status_code == 403:
            raise RacingAPIError(f"403 — {path} is not on your plan. Body: {r.text[:200]}")
        if r.status_code == 429:
            raise RacingAPIError(f"429 — rate limited on {path}; slow down.")
        if not r.ok:
            raise RacingAPIError(f"{r.status_code} on {path}: {r.text[:300]}")
        return r.json()

    # per-endpoint page size: the spec's own default limit, which is also the
    # largest value each endpoint is documented to accept.
    PAGE = {"/v1/racecards/free": 500, "/v1/racecards/basic": 500, "/v1/racecards/standard": 500,
            "/v1/racecards/pro": 500, "/v1/results": 50, "/v1/results/today": 50,
            "/v1/results/today/free": 50}

    def _paged(self, path: str, key: str, **params) -> list[dict]:
        """Walk limit/skip until `total` is exhausted. Falls back to 'a short page
        means the end' for any endpoint that omits `total`."""
        out, skip = [], 0
        page_size = self.PAGE.get(path, 50)
        while True:
            js = self.get(path, limit=page_size, skip=skip, **params)
            rows = js.get(key) or []
            out.extend(rows)
            total = js.get("total")
            skip += page_size
            if not rows or (total is not None and skip >= total) or (total is None and len(rows) < page_size):
                return out

    # ---- endpoints we use -------------------------------------------------
    def racecards(self, day: str = "today", region_codes=UK_IRE, plan: str = "free") -> list[dict]:
        """Declared runners for `day` ('today' or 'tomorrow'). `plan` picks the endpoint.

        The free endpoint carries every field the feature builder needs; richer
        plans are accepted so a subscriber is not forced back down to free.
        """
        path = f"/v1/racecards/{plan}"
        params = {"region_codes": list(region_codes) if region_codes else None}
        # /v1/racecards/pro names the parameter `date` and takes YYYY-MM-DD
        params["date" if plan == "pro" else "day"] = day
        return self._paged(path, "racecards", **params)

    def results_today(self, region_codes=UK_IRE, free: bool = True) -> list[dict]:
        path = "/v1/results/today/free" if free else "/v1/results/today"
        # this endpoint takes `region` (singular) per the spec
        return self._paged(path, "results", region=list(region_codes) if region_codes else None)

    def results(self, start_date: str, end_date: str, region=UK_IRE) -> list[dict]:
        """Historic results (Standard plan, last 12 months). Used to backfill the
        gap between the Kaggle archive's last day and today."""
        return self._paged("/v1/results", "results", start_date=start_date,
                           end_date=end_date, region=list(region) if region else None)

    def courses(self, region_codes=UK_IRE) -> list[dict]:
        return self.get("/v1/courses", region_codes=list(region_codes) if region_codes else None).get("courses", [])


# ---- probe: what does THIS key actually return? ---------------------------
CARD_FIELDS = ["number", "draw", "lbs", "ofr", "sire", "dam", "damsire", "sex_code",
               "age", "headgear", "jockey", "trainer", "horse", "rpr", "ts"]
RESULT_FIELDS = ["position", "or", "rpr", "tsr", "sp_dec", "bsp", "btn", "ovr_btn",
                 "time", "weight_lbs", "draw", "number", "sire", "dam", "prize",
                 "performance_rating", "speed_rating"]


def _coverage(runners: list[dict], fields: list[str]) -> dict:
    n = len(runners)
    if not n:
        return {}
    out = {}
    for f in fields:
        filled = sum(1 for r in runners if str(r.get(f, "") or "").strip() not in ("", "-", "None"))
        out[f] = filled / n
    return out


def probe(api: RacingAPI) -> dict:
    """Report which endpoints this key can reach and how well each field is populated.

    The decisive number is `rpr` on the results feed. `rp_rpr_minus_or` carries
    100% of the measured edge (RECON.md s.14); if rpr comes back empty the daily
    loop cannot keep the feature alive and the strategy must be re-validated
    against whatever rating the feed does supply.
    """
    report = {}
    for name, call in (
        ("racecards/free", lambda: api.racecards(plan="free")),
        ("racecards/basic", lambda: api.racecards(plan="basic")),
        ("results/today/free", lambda: api.results_today(free=True)),
    ):
        try:
            races = call()
        except RacingAPIError as e:
            report[name] = {"error": str(e)}
            continue
        runners = [r for race in races for r in (race.get("runners") or [])]
        fields = RESULT_FIELDS if "results" in name else CARD_FIELDS
        report[name] = {"races": len(races), "runners": len(runners),
                        "coverage": _coverage(runners, fields)}
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", action="store_true",
                    help="report which endpoints this key reaches and how well each field is filled")
    ap.add_argument("--racecards", metavar="DAY", help="'today' or 'tomorrow'")
    ap.add_argument("--results-today", action="store_true")
    ap.add_argument("--results", nargs=2, metavar=("START", "END"), help="YYYY-MM-DD YYYY-MM-DD (Standard plan)")
    ap.add_argument("--plan", default="free", choices=["free", "basic", "standard", "pro"])
    ap.add_argument("--out", type=Path, help="write the raw JSON here")
    args = ap.parse_args()

    api = RacingAPI()
    if args.probe:
        rep = probe(api)
        for name, r in rep.items():
            print(f"\n{name}")
            if "error" in r:
                print(f"  unavailable: {r['error']}")
                continue
            print(f"  {r['races']} races, {r['runners']} runners")
            for f, c in sorted(r["coverage"].items(), key=lambda kv: -kv[1]):
                flag = "  <-- carries the edge" if f == "rpr" else ""
                print(f"    {f:<20} {c:6.1%}{flag}")
        rpr = rep.get("results/today/free", {}).get("coverage", {}).get("rpr")
        if rpr is not None:
            print(f"\nRPR on the results feed: {rpr:.1%} populated.")
            print("  >=80%  the daily loop can keep rp_rpr_last alive — proceed." if rpr >= 0.8 else
                  "  <80%   rp_rpr_minus_or will decay. Re-validate before betting live.")
        return

    data = None
    if args.racecards:
        data = api.racecards(day=args.racecards, plan=args.plan)
    elif args.results_today:
        data = api.results_today(free=args.plan == "free")
    elif args.results:
        data = api.results(*args.results)
    else:
        ap.error("pick one of --probe / --racecards / --results-today / --results")

    runners = sum(len(r.get("runners") or []) for r in data)
    print(f"{len(data)} races, {runners} runners")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(data, indent=1), encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
