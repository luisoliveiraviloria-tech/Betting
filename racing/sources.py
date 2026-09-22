"""
Racecard and result adapters — turn any feed into the exact frame `clean.py` parses.

Design rule: **never re-implement a parser.** Every adapter here produces a frame
with the Kaggle archive's RAW column names and hands it to `clean.clean_runs()`,
so a live runner is parsed by byte-identical code to the 1.35M training rows.
Distance, weight, going, handicap detection, course/region classification and
`horse_key` construction therefore cannot drift between training and production —
which is the failure mode that silently kills a live model.

Two adapters:

  `racecard_to_raw(races)`  The Racing API /v1/racecards/* JSON -> raw frame with
                            post-race columns (pos, rpr, ts, time, btn, sp) NULL.
  `results_to_raw(results)` The Racing API /v1/results* JSON -> raw frame with the
                            post-race columns filled, for extending the archive.

  `card_from_file(path)`    A CSV or JSON in the canonical schema below, for a feed
                            we have no adapter for, or for typing a card by hand.

Canonical card schema (one row per declared runner; * = required):
    date*, off*, course*, race_id*, race_name, type, race_class, rating_band,
    pattern, age_band, sex_rest, dist*, going*, surface, field_size,
    prize, horse*, sire, dam, damsire, owner, age, sex, number, draw, lbs,
    headgear, jockey, trainer, ofr

`sire` and `dam` matter more than they look: `clean.horse_key` is
`horse|sire|dam`, so a feed that omits them makes every runner a first-timer with
no history. `live.resolve_identities()` repairs that, but supplying them is better.
"""
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from racing.clean import GOING_ORD, clean_runs

# the raw columns clean_runs() reads. Anything missing is created as NA.
RAW_COLUMNS = ["course", "date", "off", "race_id", "race_name", "type", "class", "pattern",
               "rating_band", "age_band", "sex_rest", "dist", "going", "ran", "num", "draw",
               "horse", "sire", "dam", "damsire", "owner", "age", "sex", "wgt", "hg",
               "jockey", "trainer", "prize", "or", "rpr", "ts", "sp", "time", "pos",
               "btn", "ovr_btn"]
POST_RACE = ["sp", "time", "pos", "btn", "ovr_btn", "rpr", "ts"]

# race `type` in the archive; anything else is left alone and will simply not
# match the is_jumps / h_type slices.
TYPE_ALIASES = {"bumper": "NH Flat", "nh flat": "NH Flat", "national hunt flat": "NH Flat",
                "flat": "Flat", "hurdle": "Hurdle", "chase": "Chase",
                "steeplechase": "Chase", "aw": "Flat"}
AW_SURFACES = {"aw", "all weather", "all-weather", "polytrack", "tapeta", "fibresand", "sand"}
_PAREN = re.compile(r"\s*\([^)]*\)")


def normalise_going(v) -> str:
    """'Good To Soft (Good in places)' -> 'Good To Soft'; 'good/yielding' -> 'Good To Yielding'.

    Feeds decorate the going with places-qualifiers and slashes; `GOING_ORD` is a
    strict dict lookup, so an undecorated string is the difference between a
    populated `going_ord` and a NaN one for the whole card.
    """
    if not isinstance(v, str) or not v.strip():
        return None
    t = _PAREN.sub("", v).strip()
    t = t.replace("/", " To ").replace(" to ", " To ")
    t = " ".join(w.capitalize() if w.lower() != "to" else "To" for w in t.split())
    if t in GOING_ORD:
        return t
    # last resort: case-insensitive match
    for k in GOING_ORD:
        if k.lower() == t.lower():
            return k
    return t  # unknown -> going_ord NaN, which the model treats as missing


def normalise_dist(v) -> str:
    """The Racing API gives `distance_f` as bare furlongs ('7.0'); the archive's
    `dist` is '7f' / '1m2f'. parse_dist_f understands the latter."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return f"{s}f"
    return s


def normalise_type(v) -> str:
    if not isinstance(v, str):
        return None
    return TYPE_ALIASES.get(v.strip().lower(), v.strip())


def tag_all_weather(course: str, surface) -> str:
    """`clean.clean_runs` reads the surface off the course string ('Kempton (AW)').
    Feeds carry it in a separate field, so fold it back in."""
    c = str(course or "").strip()
    if isinstance(surface, str) and surface.strip().lower() in AW_SURFACES and "(aw)" not in c.lower():
        c = f"{c} (AW)"
    return c


def _empty_raw(n: int) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series([None] * n, dtype=object) for c in RAW_COLUMNS})


def _s(v):
    """None/'' -> None, everything else -> str (the Kaggle loader reads dtype=str)."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v).strip()
    return s or None


def racecard_to_raw(races: list[dict]) -> pd.DataFrame:
    """The Racing API racecards JSON -> raw frame, post-race columns NULL.

    `prize` deserves a note. In the archive it is the money the RUNNER won, and
    the only feature built from it is `race_prize` = the SUM over the race (the
    per-runner value is a post-race outcome and is banned — see RECON.md s.13).
    A racecard advertises one race-level prize, so we split it evenly across the
    field: the race sum then reproduces the advertised figure and no runner is
    distinguished by it. That assumes the archive's race-sum is the same quantity
    as the advertised fund. `live.prize_scale_report()` checks that assumption
    against the archive on every card rather than trusting it.
    """
    rows = []
    for race in races:
        runners = race.get("runners") or []
        n = len(runners) or 1
        prize = _prize_to_number(race.get("prize"))
        per_runner_prize = (prize / n) if prize is not None else None
        for r in runners:
            rows.append({
                "course": tag_all_weather(race.get("course"), race.get("surface")),
                "date": _s(race.get("date")),
                "off": _s(race.get("off_time") or race.get("off")),
                "race_id": _s(race.get("race_id")),
                "race_name": _s(race.get("race_name")),
                "type": normalise_type(race.get("type")),
                "class": _s(race.get("race_class") or race.get("class")),
                "pattern": _s(race.get("pattern")),
                "rating_band": _s(race.get("rating_band")),
                "age_band": _s(race.get("age_band")),
                "sex_rest": _s(race.get("sex_restriction") or race.get("sex_rest")),
                "dist": normalise_dist(race.get("distance_f") or race.get("dist_f") or race.get("distance")),
                "going": normalise_going(race.get("going")),
                "ran": _s(race.get("field_size") or len(runners)),
                "num": _s(r.get("number")),
                "draw": _s(r.get("draw")),
                "horse": _s(r.get("horse")),
                "sire": _s(r.get("sire")),
                "dam": _s(r.get("dam")),
                "damsire": _s(r.get("damsire")),
                "owner": _s(r.get("owner")),
                "age": _s(r.get("age")),
                "sex": _s(r.get("sex_code") or r.get("sex")),
                "wgt": _lbs_to_stone_lb(r.get("lbs")) or _s(r.get("weight")),
                "hg": _s(r.get("headgear")),
                "jockey": _s(r.get("jockey")),
                "trainer": _s(r.get("trainer")),
                "prize": None if per_runner_prize is None else f"{per_runner_prize:.0f}",
                "or": _s(r.get("ofr") or r.get("or")),
            })
    df = _empty_raw(len(rows))
    got = pd.DataFrame(rows)
    for c in got.columns:
        df[c] = got[c].values
    for c in POST_RACE:
        df[c] = None
    return df


def results_to_raw(results: list[dict]) -> pd.DataFrame:
    """The Racing API results JSON -> raw frame with post-race columns filled.

    Used to extend the form archive past its 2026-06-03 end, which is what keeps
    `rp_rpr_last` alive for horses that have run since. Field names follow the
    /v1/results schema (`or`, `rpr`, `tsr`, `position`, `weight_lbs`).
    """
    rows = []
    for race in results:
        runners = race.get("runners") or []
        for r in runners:
            rows.append({
                "course": tag_all_weather(race.get("course"), race.get("surface")),
                "date": _s(race.get("date")),
                "off": _s(race.get("off") or race.get("off_time")),
                "race_id": _s(race.get("race_id")),
                "race_name": _s(race.get("race_name")),
                "type": normalise_type(race.get("type")),
                "class": _s(race.get("class") or race.get("race_class")),
                "pattern": _s(race.get("pattern")),
                "rating_band": _s(race.get("rating_band")),
                "age_band": _s(race.get("age_band")),
                "sex_rest": _s(race.get("sex_rest") or race.get("sex_restriction")),
                "dist": normalise_dist(race.get("dist") or race.get("dist_f")),
                "going": normalise_going(race.get("going")),
                "ran": _s(len(runners)),
                "num": _s(r.get("number")),
                "draw": _s(r.get("draw")),
                "horse": _s(r.get("horse")),
                "sire": _s(r.get("sire")),
                "dam": _s(r.get("dam")),
                "damsire": _s(r.get("damsire")),
                "owner": _s(r.get("owner")),
                "age": _s(r.get("age")),
                "sex": _s(r.get("sex")),
                "wgt": _lbs_to_stone_lb(r.get("weight_lbs")) or _s(r.get("weight")),
                "hg": _s(r.get("headgear")),
                "jockey": _s(r.get("jockey")),
                "trainer": _s(r.get("trainer")),
                "prize": _s(_prize_to_number(r.get("prize"))),
                "or": _s(r.get("or")),
                "rpr": _s(r.get("rpr")),
                "ts": _s(r.get("tsr") or r.get("ts")),
                "sp": _s(r.get("sp")),
                "time": _s(r.get("time")),
                "pos": _s(r.get("position")),
                "btn": _s(r.get("btn")),
                "ovr_btn": _s(r.get("ovr_btn")),
            })
    df = _empty_raw(len(rows))
    got = pd.DataFrame(rows)
    for c in got.columns:
        df[c] = got[c].values
    return df


def _prize_to_number(v):
    """'£12,450' / '12450' -> 12450.0; None for anything unparseable."""
    if v is None:
        return None
    t = re.sub(r"[^\d.]", "", str(v))
    try:
        return float(t) if t else None
    except ValueError:
        return None


def _lbs_to_stone_lb(v):
    """The archive's `wgt` is 'st-lb' ('9-7'); feeds give total pounds."""
    if v is None:
        return None
    try:
        lb = int(float(str(v).strip()))
    except (ValueError, TypeError):
        return None
    return f"{lb // 14}-{lb % 14}"


def card_from_file(path: Path) -> pd.DataFrame:
    """Read a card in the canonical schema (CSV, or JSON in racecards shape) -> raw frame."""
    path = Path(path)
    if path.suffix.lower() == ".json":
        js = json.loads(path.read_text(encoding="utf-8"))
        races = js.get("racecards") if isinstance(js, dict) else js
        return racecard_to_raw(races)
    flat = pd.read_csv(path, dtype=str)
    ren = {"race_class": "class", "sex_restriction": "sex_rest", "off_time": "off",
           "distance_f": "dist", "distance": "dist", "field_size": "ran",
           "number": "num", "lbs": "wgt", "headgear": "hg", "ofr": "or", "surface": "_surface"}
    flat = flat.rename(columns={k: v for k, v in ren.items() if k in flat.columns})
    df = _empty_raw(len(flat))
    for c in RAW_COLUMNS:
        if c in flat.columns:
            df[c] = flat[c].values
    if "_surface" in flat.columns:
        df["course"] = [tag_all_weather(c, s) for c, s in zip(df["course"], flat["_surface"])]
    df["dist"] = df["dist"].map(normalise_dist)
    df["going"] = df["going"].map(normalise_going)
    df["type"] = df["type"].map(normalise_type)
    if "wgt" in flat.columns and flat["wgt"].notna().any():
        # accept either '9-7' or bare pounds
        df["wgt"] = [w if (isinstance(w, str) and "-" in w) else _lbs_to_stone_lb(w) for w in df["wgt"]]
    for c in POST_RACE:
        df[c] = None
    return df


def to_runs(raw: pd.DataFrame) -> pd.DataFrame:
    """Raw frame -> the typed `runs` schema, through the SAME parser as training."""
    if raw.empty:
        return clean_runs(_empty_raw(0))
    return clean_runs(raw)
