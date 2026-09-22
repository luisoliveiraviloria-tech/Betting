"""
Daily live-card pipeline: today's UK & Irish cards -> market probabilities ->
picks for a manual-betting bankroll -> settle yesterday -> JSON for the dashboard.

Source: sportinglife.com. It is the only racecard/result source reachable by a
script from the cloud sandbox (Racing Post 406, Betfair 403, BHA/Timeform/ATR
403 -- probed 2026-09-22). Each page embeds its data as Next.js `__NEXT_DATA__`
JSON, so nothing is scraped from markup. Constraints found live:
  * only TODAY's card is served (`/racing/racecards/<date>` and `/tomorrow`
    307-redirect to today), so this must run on the race day itself;
  * race `time` fields are UTC, converted to Europe/London here;
  * a race page after the off carries `finish_position` and the final price in
    `betting.current_odds` -- the same URL serves the card and the result.

Picks ("mkt-fav-v0"). The trained model (`racing/model.py`) needs the archive
DBs, which are not available to a cloud run, so v0 is the market baseline the
repo's own research says to measure everything else against: best bookmaker
price -> implied probability -> normalised per race; bet the race favourite if
its price is <= MAX_PRICE, at most MAX_BETS a day, highest probability first.
Expected ROI is NEGATIVE: favourites-only was -3.9% at BSP after commission
over 2017+ (RECON.md s.193). This is a measured baseline, not an edge.
Stakes follow `models/production_strategy.json` for a GBP100 bank: GBP2 flat.

    python -m racing.live_card run              # settle past days, build today (provisional)
    python -m racing.live_card run --lock       # same, then freeze today's picks
    python -m racing.live_card run --placed placed.json   # use bets actually taken
"""
import argparse
import csv
import datetime as dt
import json
import re
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE = "https://www.sportinglife.com"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0 Safari/537.36", "Accept-Language": "en-GB,en;q=0.9"}
UK = ZoneInfo("Europe/London")
UTC = ZoneInfo("UTC")
UK_IRE = {"England", "Scotland", "Wales", "Northern Ireland", "Ireland", "Eire"}
LIVE_DIR = Path(__file__).parent / "live"

SYSTEM = "mkt-fav-v0"
BANK_START = 100.0
STAKE = 2.0          # production_strategy.json: bankroll 100 -> stake 2
MAX_PRICE = 4.0      # production_strategy.json gates.max_price
MAX_BETS = 5
STOP_BANK = 20.0     # stop proposing bets if the bank falls this low
EXPECTED_ROI = -0.039


class SourceError(RuntimeError):
    pass


def uk_today() -> dt.date:
    return dt.datetime.now(UK).date()


def _next_data(url: str, tries: int = 3) -> tuple[dict, str]:
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code == 200:
                m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
                if not m:
                    raise SourceError(f"no __NEXT_DATA__ at {url} (page layout changed?)")
                return json.loads(m.group(1))["props"]["pageProps"], r.text
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)
        time.sleep(2 * (i + 1))
    raise SourceError(f"{url}: {last}")


def frac_to_dec(s) -> float | None:
    if not s or not isinstance(s, str):
        return None
    s = s.strip().lower()
    if s in ("evs", "evens", "ev"):
        return 2.0
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", s)
    return round(1 + int(m.group(1)) / int(m.group(2)), 3) if m else None


def uk_time(date: str, utc_hhmm: str) -> str:
    t = dt.datetime.fromisoformat(f"{date}T{utc_hhmm[:5]}").replace(tzinfo=UTC)
    return t.astimezone(UK).strftime("%H:%M")


def list_race_urls(date: dt.date) -> list[str]:
    pp, _ = _next_data(f"{BASE}/racing/racecards")
    days = {m["meeting_summary"]["date"] for m in pp.get("meetings") or []}
    if date.isoformat() not in days:
        raise SourceError(f"Sporting Life is serving {sorted(days)}, not {date} -- it only serves "
                          "today's card, so this has to run on the race day")
    urls = []
    for m in pp["meetings"]:
        ms = m["meeting_summary"]
        if ms["date"] != date.isoformat() or ms["course"]["country"]["long_name"] not in UK_IRE:
            continue
        course = _slug(ms["course"]["name"])
        # the index only links a subset of races; the site resolves any slug by race id
        urls += [f"{BASE}/racing/racecards/{date}/{course}/racecard/{r['race_summary_reference']['id']}/race"
                 for r in m.get("races") or []]
    return urls


def _verdict_top(html: str | None) -> str | None:
    m = re.search(r"<li><b>(.*?)</b></li>", html or "")
    return m.group(1).strip().title() if m else None


def _forecast(s: str | None) -> dict[str, float]:
    return {n.strip().lower(): frac_to_dec(o)
            for n, o in re.findall(r"([^,(]+)\(([^)]+)\)", s or "") if frac_to_dec(o)}


def parse_race(url: str) -> dict:
    pp, _ = _next_data(url)
    race = pp["race"]
    rs = race["race_summary"]
    fc = _forecast(race.get("betting_forecast"))
    runners = []
    for r in race.get("rides") or []:
        h = r.get("horse") or {}
        bet = r.get("betting") or {}
        books = [b.get("decimalOdds") for b in r.get("bookmakerOdds") or []
                 if b.get("outcome") == "Win" and b.get("decimalOdds")]
        runners.append({
            "no": r.get("cloth_number"), "horse": h.get("name"), "age": h.get("age"),
            "sex": (h.get("sex") or {}).get("type"), "jockey": (r.get("jockey") or {}).get("name"),
            "trainer": (r.get("trainer") or {}).get("name"), "wgt": r.get("handicap"),
            "or": r.get("official_rating"), "form": (h.get("formsummary") or {}).get("display_text"),
            "draw": r.get("draw_number") or None,
            "hg": "".join(x.get("symbol", "") for x in r.get("headgear") or []) or None,
            "days": h.get("last_ran_days"), "ts": r.get("timeform_stars"),
            "status": r.get("ride_status"),
            "odds": max(books) if books else None,
            "odds_frac": bet.get("current_odds"),
            "fc": fc.get((h.get("name") or "").lower()),
            "fav": (bet.get("favourite") or {}).get("betting_favourite"),
            "pos": r.get("finish_position") or None,
            "note": (r.get("commentary") or "")[:220] or None,
        })
    finished = any(x["pos"] for x in runners)
    for x in runners:
        # price used for probabilities: live best book price, else the published forecast
        x["price"] = x["odds"] or x["fc"] or frac_to_dec(x["odds_frac"])
        if finished:
            x["sp"] = frac_to_dec(x["odds_frac"])
    active = [x for x in runners if x["status"] == "RUNNER" and x["price"]]
    book = sum(1 / x["price"] for x in active)
    for x in runners:
        x["p"] = round((1 / x["price"]) / book, 4) if x in active and book else None
    return {
        "id": rs["race_summary_reference"]["id"], "url": url, "date": rs["date"],
        "off": uk_time(rs["date"], rs["time"]), "course": rs["course_name"], "name": rs["name"],
        "dist": rs.get("distance"), "going": rs.get("going"), "cls": rs.get("race_class"),
        "age": rs.get("age"), "hcap": rs.get("has_handicap"),
        "surface": (rs.get("course_surface") or {}).get("surface"),
        "stage": rs.get("race_stage"), "finished": finished,
        "price_src": "books" if any(x["odds"] for x in active) else ("forecast" if active else None),
        "book_pct": round(book * 100, 1) if book else None,
        "book_full": all(x["price"] for x in runners if x["status"] == "RUNNER"),
        "verdict_top": _verdict_top(rs.get("verdict")),
        "verdict": (rs.get("verdict_text") or "")[:400] or None,
        "runners": runners,
    }


def favourite(race: dict) -> dict | None:
    act = [x for x in race["runners"] if x.get("p")]
    return max(act, key=lambda x: x["p"]) if act else None


def build_picks(races: list[dict], bank: float) -> list[dict]:
    if bank < STOP_BANK:
        return []
    cands = []
    for r in races:
        f = favourite(r)
        if not f or r["finished"] or f["price"] > MAX_PRICE:
            continue
        cands.append({"race_id": r["id"], "off": r["off"], "course": r["course"], "race": r["name"],
                      "horse": f["horse"], "no": f["no"], "price": f["price"], "p": f["p"],
                      "price_src": r["price_src"], "stake": STAKE})
    cands.sort(key=lambda c: -c["p"])
    return sorted(cands[:MAX_BETS], key=lambda c: c["off"])


def settle(day: dict, placed: dict) -> dict:
    """Mark each pick won/lost/void from the race pages; P&L at the price taken if the
    user recorded one (placed.json), else the pick price. Bookmaker bets: no commission."""
    races = {r["id"]: r for r in day["races"]}
    staked = returned = 0.0
    for pk in day["picks"]:
        r = races.get(pk["race_id"])
        rec = placed.get(str(pk["race_id"])) or {}
        pk["placed"] = bool(rec.get("placed", True))
        pk["taken"] = rec.get("price") or pk["price"]
        if not r or not r["finished"]:
            pk["result"] = "pending"
            continue
        x = next((x for x in r["runners"] if x["horse"] == pk["horse"]), None)
        pk["pos"], pk["sp"] = (x or {}).get("pos"), (x or {}).get("sp")
        if not pk["placed"]:
            pk["result"], pk["pnl"] = "skipped", 0.0
            continue
        if not x or x["status"] != "RUNNER":
            pk["result"], pk["pnl"] = "void", 0.0
            continue
        staked += pk["stake"]
        if x["pos"] == 1:
            ret = pk["stake"] * pk["taken"]
            returned += ret
            pk["result"], pk["pnl"] = "won", round(ret - pk["stake"], 2)
        else:
            pk["result"], pk["pnl"] = "lost", -pk["stake"]
    day["summary"] = {"staked": round(staked, 2), "returned": round(returned, 2),
                      "pnl": round(returned - staked, 2),
                      "settled": all(p["result"] != "pending" for p in day["picks"])}
    return day


def _day_path(d: str) -> Path:
    return LIVE_DIR / d / "day.json"


def _load(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def _save(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def refresh_results(day: dict) -> dict:
    for i, r in enumerate(day["races"]):
        if not r["finished"]:
            try:
                day["races"][i] = parse_race(r["url"])
            except SourceError as e:
                day.setdefault("errors", []).append(str(e))
    return day


def rebuild_ledger() -> dict:
    hist, bank, n, won = [], BANK_START, 0, 0
    for p in sorted(LIVE_DIR.glob("*/day.json")):
        d = json.loads(p.read_text())
        if d.get("test") or not d.get("summary"):
            continue
        bank = round(bank + d["summary"]["pnl"], 2)
        bets = [x for x in d["picks"] if x.get("result") in ("won", "lost")]
        n += len(bets)
        won += sum(x["result"] == "won" for x in bets)
        hist.append({"date": d["date"], "pnl": d["summary"]["pnl"], "bank": bank,
                     "bets": len(bets), "settled": d["summary"]["settled"]})
    staked = sum(h["bets"] for h in hist) * STAKE
    led = {"system": SYSTEM, "start": BANK_START, "bank": bank, "bets": n, "won": won,
           "staked": staked, "roi": round((bank - BANK_START) / staked, 4) if staked else None,
           "expected_roi": EXPECTED_ROI, "history": hist}
    _save(LIVE_DIR / "ledger.json", led)
    with open(LIVE_DIR / "ledger.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "bets", "pnl", "bank", "settled"])
        w.writeheader()
        w.writerows(hist)
    return led


def load_placed(src: str | None) -> dict:
    """Bets the user marked on the dashboard: {date: {race_id: {placed, price}}}.
    Accepts one JSON file in that shape, or a directory dumped from the dashboard's
    `placed` collection (one `<date>.json` per day, bare body or {data: body})."""
    if not src:
        return {}
    p = Path(src)
    if p.is_file():
        return json.loads(p.read_text())
    out = {}
    for f in sorted(p.rglob("*.json")):
        body = json.loads(f.read_text())
        body = body.get("data", body) if isinstance(body.get("data"), dict) else body
        out[f.stem] = {k: v for k, v in body.items() if isinstance(v, dict)}
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _manifest_entry(path: str, f: Path) -> dict:
    coll, doc_id = path.rsplit("/", 1)
    return {"op": "set", "collection": coll, "doc_id": doc_id, "file_path": str(f.resolve())}


def export_meta(led: dict, status: dict) -> list[dict]:
    out = []
    for path, body in (("meta/ledger", led), ("meta/status", status)):
        f = LIVE_DIR / "_meta" / (path.replace("/", "__") + ".json")
        _save(f, body)
        out.append(_manifest_entry(path, f))
    return out


def export_db(day: dict) -> Path:
    """Write the dashboard's database documents as files, one per document, so a run
    can push them with one batch. A day is split per meeting because a busy day's
    full card overflows the store's 256 KiB-per-document limit.
      days/<date>                      picks, summary, meeting index
      days/<date>/meetings/<course>    that meeting's races and runners
    (meta/ledger and meta/status come from export_meta.)
    """
    out = LIVE_DIR / day["date"] / "db"
    for old in out.glob("*.json"):
        old.unlink()
    meetings = {}
    for r in day["races"]:
        meetings.setdefault(r["course"], []).append(r)
    index = [{"course": c, "slug": _slug(c), "races": len(rs), "going": rs[0]["going"],
              "surface": rs[0]["surface"], "first": rs[0]["off"]} for c, rs in meetings.items()]
    head = {k: v for k, v in day.items() if k != "races"}
    head["meetings"] = sorted(index, key=lambda m: m["first"])
    head.pop("placed_seen", None)
    docs = {f"days/{day['date']}": head}
    for c, rs in meetings.items():
        docs[f"days/{day['date']}/meetings/{_slug(c)}"] = {"course": c, "races": rs}
    manifest = []
    for path, body in docs.items():
        f = out / (path.replace("/", "__") + ".json")
        _save(f, body)
        manifest.append(_manifest_entry(path, f))
    _save(out / "_manifest.json", manifest)
    return out


def run(lock: bool = False, placed_file: str | None = None, date: dt.date | None = None,
        test: bool = False) -> dict:
    date = date or uk_today()
    placed_all = load_placed(placed_file)
    status = {"ran_at": dt.datetime.now(UK).isoformat(timespec="minutes"), "date": date.isoformat(),
              "errors": []}

    # 1. settle every past day that isn't settled yet (and re-settle any day whose
    #    placed-bets record changed on the dashboard)
    touched = []
    for p in sorted(LIVE_DIR.glob("*/day.json")):
        d = json.loads(p.read_text())
        if d["date"] >= date.isoformat():
            continue
        settled = (d.get("summary") or {}).get("settled")
        if settled and d.get("placed_seen") == placed_all.get(d["date"], {}):
            continue
        d = settle(refresh_results(d) if not settled else d, placed_all.get(d["date"], {}))
        d["placed_seen"] = placed_all.get(d["date"], {})
        _save(p, d)
        touched.append(d)

    # 2. today's card
    try:
        urls = list_race_urls(date)
    except SourceError as e:
        status["errors"].append(str(e))
        urls = []
    races = []
    for u in urls:
        try:
            races.append(parse_race(u))
        except (SourceError, KeyError) as e:
            status["errors"].append(f"{u}: {e}")
    races.sort(key=lambda r: (r["off"], r["course"]))

    path = _day_path(date.isoformat())
    prev = _load(path, {})
    led = rebuild_ledger()
    day = None
    if races or prev:                     # never write an empty day when the source failed
        picks = prev["picks"] if prev.get("locked") else build_picks(races, led["bank"])
        day = {"date": date.isoformat(), "system": SYSTEM, "test": test or prev.get("test", False),
               "locked": bool(prev.get("locked") or lock), "updated": status["ran_at"],
               "races": races or prev.get("races", []), "picks": picks,
               "rules": {"stake": STAKE, "max_price": MAX_PRICE, "max_bets": MAX_BETS,
                         "bank_start": BANK_START, "expected_roi": EXPECTED_ROI}}
        day = settle(day, placed_all.get(date.isoformat(), {}))
        day["placed_seen"] = placed_all.get(date.isoformat(), {})
        _save(path, day)

    led = rebuild_ledger()
    status.update({"races": len(races), "picks": len(day["picks"]) if day else 0,
                   "locked": bool(day and day["locked"]), "bank": led["bank"],
                   "ok": not status["errors"] and bool(races)})
    _save(LIVE_DIR / "status.json", status)
    manifest = export_meta(led, status)
    for d in touched + ([day] if day else []):
        manifest += json.loads((export_db(d) / "_manifest.json").read_text())
    # one manifest for the whole run: every dashboard document this run changed
    seen, uniq = set(), []
    for w in reversed(manifest):          # last write per document wins
        k = (w["collection"], w["doc_id"])
        if k not in seen:
            seen.add(k)
            uniq.append(w)
    _save(LIVE_DIR / "_manifest.json", uniq[::-1])
    status["db_writes"] = len(uniq)
    return status


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--lock", action="store_true", help="freeze today's picks after this run")
    ap.add_argument("--placed", help="JSON {date: {race_id: {placed, price}}} of bets actually taken")
    ap.add_argument("--test", action="store_true", help="mark today's day as a test (kept out of the ledger)")
    a = ap.parse_args()
    st = run(lock=a.lock, placed_file=a.placed, test=a.test)
    print(json.dumps(st, indent=1))
    if not st["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
