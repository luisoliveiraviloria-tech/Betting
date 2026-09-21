"""
Clean the Kaggle `deltaromeo` raceform CSV into a typed UK+IRE `runs` table.

Design notes (each learned from inspecting the real data, 2026-09-21):
  * Region: course names are NOT reliably tagged. Irish courses appear both as
    "Naas (IRE)" and bare "Naas"; dozens of foreign courses are bare too
    (Aqueduct, Auteuil, Bendigo...). And GB all-weather tracks carry brackets
    ("Kempton (AW)") so "has brackets => foreign" silently drops UK AW racing.
    => classify on the BASE course name against explicit GB / IRE whitelists.
  * '�' in `dist` is a corrupted "1/2" (e.g. "1m7�f" = 1m7.5f).
  * `off` mixes 24h ("16:18") and bare 12h ("1:42", "6:31"); UK/IRE racing
    never starts before ~11:00, so hours 1-9 are afternoon (+12).
  * `sp` has "EvensF" / "Evs" / "EvsJ" and trailing F/J/C favourite flags.
  * `pos` is numeric or a non-finish code (PU, F, UR, BD, RR, DSQ, RO, SU, REF, CO, LFT).

Post-race columns are kept (pos, time, rpr, ts, btn) because they are needed as
PREVIOUS-run features -- features.py must only ever use them shifted.
`comment` is dropped (post-race free text, big, not needed).
"""
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

KAGGLE = Path(__file__).parent / "data" / "kaggle"
RAW = KAGGLE / "form_2015-present" / "form_2015-present" / "raceform.csv"
MINI = KAGGLE / "mini-update.csv"
FORM_DB = Path(__file__).parent / "data" / "form.db"  # separate from racing.db (BSP downloader holds write locks there)

GB_COURSES = {
    "Aintree", "Ascot", "Ayr", "Bangor-on-Dee", "Bath", "Beverley", "Brighton", "Carlisle", "Cartmel",
    "Catterick", "Chelmsford", "Cheltenham", "Chepstow", "Chester", "Doncaster", "Epsom", "Exeter",
    "Fakenham", "Ffos Las", "Folkestone", "Fontwell", "Goodwood", "Hamilton", "Haydock", "Hereford",
    "Hexham", "Huntingdon", "Kelso", "Kempton", "Leicester", "Lingfield", "Ludlow", "Market Rasen",
    "Musselburgh", "Newbury", "Newcastle", "Newmarket", "Newton Abbot", "Nottingham", "Perth",
    "Plumpton", "Pontefract", "Redcar", "Ripon", "Salisbury", "Sandown", "Sedgefield", "Southwell",
    "Stratford", "Taunton", "Thirsk", "Towcester", "Uttoxeter", "Warwick", "Wetherby", "Wincanton",
    "Windsor", "Wolverhampton", "Worcester", "Yarmouth", "York",
}
IRE_COURSES = {
    "Ballinrobe", "Bellewstown", "Clonmel", "Cork", "Curragh", "Down Royal", "Downpatrick", "Dundalk",
    "Fairyhouse", "Galway", "Gowran Park", "Kilbeggan", "Killarney", "Laytown", "Leopardstown",
    "Limerick", "Listowel", "Naas", "Navan", "Punchestown", "Roscommon", "Sligo", "Thurles",
    "Tipperary", "Tramore", "Wexford",
}
_TAG = re.compile(r"\s*\([^)]*\)")

# firmness scale, 1 = fastest .. ~7 = slowest. AW surfaces mapped onto the same axis.
GOING_ORD = {
    "Hard": 1.0, "Firm": 1.5, "Fast": 1.5, "Standard To Fast": 2.0, "Good To Firm": 2.0, "Frozen": 2.0,
    "Good": 3.0, "Standard": 3.0, "Good To Yielding": 3.5, "Standard To Slow": 4.0, "Good To Soft": 4.0,
    "Yielding": 4.0, "Slow": 4.5, "Yielding To Soft": 4.5, "Soft": 5.0, "Muddy": 5.0, "Holding": 5.0,
    "Very Soft": 5.5, "Sloppy": 5.5, "Soft To Heavy": 5.5, "Heavy": 6.0,
}
NON_FINISH = {"PU", "F", "UR", "BD", "RR", "DSQ", "RO", "SU", "REF", "CO", "LFT"}


def base_course(name) -> str:
    return _TAG.sub("", str(name)).strip()


def classify_region(course) -> str | None:
    """'GB' | 'IRE' | None (foreign / unknown)."""
    b = base_course(course)
    if b in IRE_COURSES:
        return "IRE"
    if b in GB_COURSES:
        return "GB"
    return None


def parse_dist_f(s) -> float:
    """'2m3½f' / '1m7�f' / '5f' / '1m1f209y' -> furlongs (float). NaN if unparseable."""
    if not isinstance(s, str):
        return np.nan
    t = s.replace("½", ".5").replace("�", ".5").strip().lower()
    m = re.fullmatch(r"(?:(\d+)m)?(?:(\d*\.?\d*)f)?(?:(\d+)y)?", t)
    if not m or not any(m.groups()):
        return np.nan
    miles, furl, yards = m.groups()
    try:
        return int(miles or 0) * 8 + float(furl or 0) + int(yards or 0) / 220.0
    except ValueError:
        return np.nan


def parse_weight_lb(s) -> float:
    m = re.fullmatch(r"(\d+)-(\d+)", str(s).strip())
    return int(m.group(1)) * 14 + int(m.group(2)) if m else np.nan


def parse_sp_decimal(s) -> float:
    t = str(s).strip()  # trailing F/J/C favourite flags are ignored by the patterns below
    if re.match(r"(?i)^(evens|evs)", t):
        return 2.0
    m = re.match(r"^(\d+)/(\d+)", t)
    return int(m.group(1)) / int(m.group(2)) + 1.0 if m and int(m.group(2)) else np.nan


def parse_off_dt(date: pd.Series, off: pd.Series) -> pd.Series:
    hm = off.astype("string").str.extract(r"^(\d{1,2}):(\d{2})$").astype(float)
    hour = hm[0].where(hm[0] >= 10, hm[0] + 12)  # 1-9 -> 13-21
    return date + pd.to_timedelta(hour.fillna(0) * 60 + hm[1].fillna(0), unit="m")


def parse_time_s(s) -> float:
    m = re.fullmatch(r"(?:(\d+):)?(\d+(?:\.\d+)?)", str(s).strip())
    return (int(m.group(1) or 0) * 60 + float(m.group(2))) if m else np.nan


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype("string").str.replace(r"[^\d.\-]", "", regex=True).replace("", pd.NA),
                         errors="coerce")


def _umap(series: pd.Series, func) -> pd.Series:
    """Apply a scalar parser once per distinct value (dist/sp/wgt/course repeat a lot)."""
    lookup = {v: func(v) for v in series.dropna().unique()}
    return series.map(lookup)


def clean_runs(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    df["region"] = _umap(df["course"], classify_region)
    df = df[df.region.notna()].copy()
    df["surface"] = np.where(df["course"].str.contains(r"\(AW\)", na=False), "AW", "Turf")
    df["course_base"] = _umap(df["course"], base_course)
    df["date"] = pd.to_datetime(df["date"])
    df["race_dt"] = parse_off_dt(df["date"], df["off"])
    # `race_id` is NOT unique in the source: 135 ids (2,703 rows) are reused for races at different
    # courses/dates (e.g. 616900 = a Musselburgh AND a Thurles race). Found by the truncation test.
    df["race_key"] = (df["race_id"].astype(str) + "|" + df["race_dt"].dt.strftime("%Y%m%d%H%M")
                      + "|" + df["course_base"])
    df["dist_f"] = _umap(df["dist"], parse_dist_f)
    df["wgt_lb"] = _umap(df["wgt"], parse_weight_lb)
    df["sp_dec"] = _umap(df["sp"], parse_sp_decimal)
    df["time_s"] = _umap(df["time"], parse_time_s)
    df["going_ord"] = df["going"].map(GOING_ORD)  # dict lookup, already vectorised
    df["class_num"] = _num(df["class"].astype("string").str.extract(r"(\d+)")[0])
    df["is_handicap"] = (df["rating_band"].notna()
                         | df["race_name"].str.contains(r"handicap|hcap", case=False, na=False)).astype(int)
    for c in ("ran", "num", "draw", "age", "prize"):
        df[c] = _num(df[c])
    df["or_"] = _num(df["or"])
    df["rpr"] = _num(df["rpr"])
    df["ts"] = _num(df["ts"])
    df["pos_raw"] = df["pos"].astype("string").str.strip()
    df["pos_num"] = pd.to_numeric(df["pos_raw"], errors="coerce")
    df["finished"] = df["pos_num"].notna().astype(int)  # numeric finishing position exists
    df["win"] = (df["pos_num"] == 1).fillna(False).astype(int)
    df["place3"] = (df["pos_num"] <= 3).fillna(False).astype(int)
    df["horse_key"] = df["horse"].astype(str) + "|" + df["sire"].fillna("").astype(str) + "|" + df["dam"].fillna("").astype(str)
    df = df.drop_duplicates(["race_key", "horse_key"])
    keep = ["race_key", "race_id", "date", "race_dt", "course_base", "region", "surface", "type", "race_name", "is_handicap",
            "class_num", "pattern", "rating_band", "age_band", "sex_rest", "dist_f", "going", "going_ord", "ran",
            "num", "draw", "horse", "horse_key", "age", "sex", "wgt_lb", "hg", "sp_dec", "jockey", "trainer",
            "prize", "or_", "rpr", "ts", "sire", "dam", "damsire", "owner", "pos_raw", "pos_num", "finished",
            "win", "place3", "time_s"]
    out = df[keep].sort_values(["race_dt", "race_key", "num"], kind="mergesort").reset_index(drop=True)
    return out


def load_raw() -> pd.DataFrame:
    usecols = lambda c: c != "comment"  # noqa: E731
    parts = [pd.read_csv(RAW, usecols=usecols, low_memory=False, dtype=str)]
    if MINI.exists():
        parts.append(pd.read_csv(MINI, usecols=usecols, low_memory=False, dtype=str))
    return pd.concat(parts, ignore_index=True)


def main() -> None:
    raw = load_raw()
    print(f"raw rows: {len(raw):,}")
    runs = clean_runs(raw)
    print(f"UK+IRE rows: {len(runs):,}  races: {runs.race_key.nunique():,}  "
          f"{runs.date.min().date()} -> {runs.date.max().date()}")
    print("region:", runs.region.value_counts().to_dict(), "| surface:", runs.surface.value_counts().to_dict())
    for c in ("dist_f", "wgt_lb", "sp_dec", "going_ord", "race_dt"):
        print(f"  unparsed {c}: {runs[c].isna().mean():.3%}")
    FORM_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(FORM_DB)
    runs.to_sql("runs", con, if_exists="replace", index=False, chunksize=100_000)
    con.close()
    print(f"wrote runs -> {FORM_DB}")


if __name__ == "__main__":
    main()
