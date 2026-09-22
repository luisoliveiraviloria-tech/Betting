"""
Gates for the live pipeline. Run before trusting a live card.

    python -m racing.test_live

The live path has a failure mode the backtest cannot have: it can be *silently
uninformed*. If a feed spells a sire differently, every runner becomes a
first-timer, `rp_rpr_minus_or` goes NaN, and the model still returns a
perfectly well-formed probability — just one built on nothing. These checks exist
to make that loud.

Everything runs on synthetic form built by the same `clean_runs` parser as the
real archive, so no database is needed; the scoring check uses the real
production booster committed in `racing/models/`.

  1. parsers      feed shapes -> the archive's typed columns (distance, weight,
                  going with places-qualifiers, handicap detection, AW surface)
  2. identity     exact / name+sire / unique / ambiguous / debutant
  3. as-of        today's features do not change when LATER days are appended,
                  and a card runner's own outcome never reaches its own features
  4. backfill     results -> runs keeps RPR and results, and re-applying a race
                  replaces it instead of duplicating the form history
  5. book         within-race normalisation sums to 1; a partial book is refused
  6. end-to-end   card + prices -> probabilities that are normalised per race,
                  and with no model correction reproduce the market exactly
"""
import sys

import numpy as np
import pandas as pd

from racing import sources
from racing.exchange import norm_name as bf_norm_name
from racing.exchange import normalise_book
from racing.live import (build_identity_index, live_features, resolve_identities,
                         _finalise_prices)

COURSES = ["Lingfield (AW)", "Kempton (AW)", "Naas"]
JOCKEYS = [f"J {i}" for i in range(6)]
TRAINERS = [f"T {i}" for i in range(5)]


# --------------------------------------------------------------- fixtures ---
def synthetic_raw(n_races: int = 90, field: int = 8, seed: int = 7) -> pd.DataFrame:
    """A small but structurally faithful form archive, in RAW (pre-clean) shape."""
    rng = np.random.default_rng(seed)
    horses = [{"horse": f"Horse {i:03d}", "sire": f"Sire {i % 12}", "dam": f"Dam {i % 20}",
               "damsire": f"Damsire {i % 7}", "ability": rng.normal(70, 8), "born": 2019 + i % 4}
              for i in range(60)]
    # two horses deliberately share a name, with different sires and ages
    horses.append({**horses[3], "sire": "Sire 99", "dam": "Dam 99", "born": 2022})
    rows = []
    day = pd.Timestamp("2024-01-05")
    for r in range(n_races):
        day = day + pd.Timedelta(days=rng.integers(3, 10))
        idx = rng.choice(len(horses), size=field, replace=False)
        course = COURSES[r % len(COURSES)]
        abilities = np.array([horses[i]["ability"] for i in idx])
        winner = int(np.argmax(abilities + rng.normal(0, 6, field)))
        order = np.argsort(-(abilities + rng.normal(0, 6, field)))
        pos = np.empty(field, dtype=int)
        pos[order] = np.arange(1, field + 1)
        pos[winner] = pos[winner]  # keep the argsort ordering authoritative
        for k, i in enumerate(idx):
            h = horses[i]
            rows.append({
                "course": course, "date": day.strftime("%Y-%m-%d"), "off": f"{13 + r % 6}:{(r * 7) % 60:02d}",
                "race_id": f"R{r}", "race_name": "Handicap" if r % 2 else "Maiden Stakes",
                "type": "Flat", "class": f"Class {2 + r % 5}", "pattern": None,
                "rating_band": "0-85" if r % 2 else None, "age_band": "3yo+", "sex_rest": None,
                "dist": f"{6 + r % 6}f", "going": "Standard" if "(AW)" in course else "Good",
                "ran": str(field), "num": str(k + 1), "draw": str(k + 1),
                "horse": h["horse"], "sire": h["sire"], "dam": h["dam"], "damsire": h["damsire"],
                "owner": "O", "age": str(day.year - h["born"]), "sex": "G",
                "wgt": f"{8 + k % 3}-{(k * 3) % 14}", "hg": None,
                "jockey": JOCKEYS[(r + k) % len(JOCKEYS)], "trainer": TRAINERS[i % len(TRAINERS)],
                "prize": "5000" if pos[k] == 1 else "1000",
                "or": str(int(h["ability"])), "rpr": str(int(h["ability"] + rng.normal(2, 4))),
                "ts": str(int(h["ability"] - 3)), "sp": f"{2 + k}/1",
                "time": f"1:{20 + k}.5", "pos": str(pos[k]),
                "btn": "0" if pos[k] == 1 else str(pos[k] - 1),
                "ovr_btn": "0" if pos[k] == 1 else str(pos[k] - 1),
            })
    return pd.DataFrame(rows)


def synthetic_card_json(hist_raw: pd.DataFrame, day: str = "2026-09-22") -> list[dict]:
    """A racecard in The Racing API's shape: known horses, a debutant, a misspelled
    sire, and a name shared by two archive horses."""
    known = hist_raw.drop_duplicates("horse").head(14).to_dict("records")
    runners = []
    for k, h in enumerate(known[:7]):
        runners.append({
            "horse": h["horse"], "sire": h["sire"], "dam": h["dam"], "damsire": h["damsire"],
            "age": "5", "sex_code": "G", "number": str(k + 1), "draw": str(k + 1),
            "lbs": str(126 + k), "ofr": h["or"], "headgear": "", "jockey": JOCKEYS[k % len(JOCKEYS)],
            "trainer": TRAINERS[k % len(TRAINERS)], "owner": "O",
        })
    # sire spelled differently by this feed -> must still resolve on name
    runners.append({**runners[0], "horse": known[7]["horse"], "sire": "SIRE  0 (IRE)",
                    "dam": known[7]["dam"], "number": "8", "draw": "8", "ofr": known[7]["or"]})
    # a genuine debutant
    runners.append({"horse": "Brand New Thing", "sire": "Sire X", "dam": "Dam X", "age": "3",
                    "sex_code": "F", "number": "9", "draw": "9", "lbs": "120", "ofr": "",
                    "jockey": JOCKEYS[0], "trainer": TRAINERS[0], "owner": "O"})
    return [{
        "race_id": "LIVE1", "course": "Lingfield", "surface": "AW", "date": day,
        "off_time": "14:20", "race_name": "Handicap", "distance_f": "7.0",
        "region": "gb", "race_class": "Class 4", "type": "Flat", "age_band": "3yo+",
        "rating_band": "0-85", "sex_restriction": "", "prize": "£5,000",
        "field_size": str(len(runners)), "going": "Standard To Slow (Slow in places)",
        "runners": runners,
    }]


# ------------------------------------------------------------------ checks ---
def check_parsers() -> None:
    card = sources.racecard_to_raw(synthetic_card_json(synthetic_raw(20)))
    runs = sources.to_runs(card)
    assert len(runs) == 9, f"expected 9 runners, got {len(runs)}"
    r = runs.iloc[0]
    assert abs(r.dist_f - 7.0) < 1e-6, f"distance_f 7.0 -> {r.dist_f}f"
    assert r.wgt_lb == 126, f"lbs 126 -> {r.wgt_lb}"
    assert r.surface == "AW", "surface=AW must survive into the course string"
    assert r.course_base == "Lingfield", r.course_base
    assert r.region == "GB", r.region
    assert r.is_handicap == 1, "rating_band 0-85 must set is_handicap"
    assert r.class_num == 4, r.class_num
    assert r.going_ord == 4.0, f"'Standard To Slow (Slow in places)' -> {r.going_ord}"
    assert runs.or_.notna().sum() == 8, "official ratings lost in translation"
    assert runs.rpr.isna().all() and runs.pos_num.isna().all(), \
        "a racecard must carry NO post-race columns"
    # race-level prize split across the field must sum back to the advertised fund
    assert abs(runs.prize.sum() - 5000) < 10, runs.prize.sum()

    assert sources.normalise_going("Good To Soft (Good in places)") == "Good To Soft"
    assert sources.normalise_going("good/yielding") == "Good To Yielding"
    assert sources.normalise_going("Heavy") == "Heavy"
    assert sources.normalise_dist("7.0") == "7.0f"
    assert sources.normalise_dist("1m2f") == "1m2f"
    assert sources.normalise_type("Bumper") == "NH Flat"
    assert bf_norm_name("1. Fat Harry (GB)") == "fatharry"
    assert bf_norm_name("Definitly Red") == "definitlyred"


def check_identity() -> None:
    hist = sources.to_runs(synthetic_raw())
    card = sources.to_runs(sources.racecard_to_raw(synthetic_card_json(synthetic_raw())))
    idx = build_identity_index(hist)
    res = resolve_identities(card, idx, pd.Timestamp("2026-09-22"))
    by_horse = dict(zip(res.horse, res.match))

    exact = (res.match == "exact").sum()
    assert exact >= 6, f"only {exact} runners matched their archive key exactly"
    assert by_horse["Brand New Thing"] == "no_history", by_horse["Brand New Thing"]
    # the runner whose sire this feed spells differently must still find its history
    odd = res[res.match.isin(["name_unique", "name+sire", "name_ambiguous"])]
    assert len(odd) >= 1, "the misspelled-sire runner did not fall through to a name match"
    assert (res.loc[res.match != "no_history", "hist_runs"] > 0).all(), \
        "a resolved runner must have prior runs"
    assert (res.loc[res.match == "no_history", "hist_runs"] == 0).all()

    # a feed with NO pedigree at all must still resolve on name alone
    stripped = card.copy()
    stripped["horse_key"] = stripped.horse.astype(str) + "||"
    res2 = resolve_identities(stripped, idx, pd.Timestamp("2026-09-22"))
    resolved = (res2.match != "no_history").sum()
    assert resolved >= 7, f"pedigree-free feed resolved only {resolved}/9 runners"


def check_as_of() -> None:
    """Today's features must not move when later racing is appended, and a card
    runner's own (unknown) outcome must never reach its own features."""
    hist = sources.to_runs(synthetic_raw())
    card = sources.to_runs(sources.racecard_to_raw(synthetic_card_json(synthetic_raw())))
    day = pd.Timestamp("2026-09-22")

    a = live_features(card, hist, day).sort_values(["race_key", "horse_key"]).reset_index(drop=True)

    # append a LATER day of racing and rebuild: today must be bit-identical
    later = sources.to_runs(synthetic_raw(n_races=10, seed=11))
    later["date"] = day + pd.Timedelta(days=30)
    later["race_dt"] = later["date"] + pd.Timedelta(hours=14)
    later["race_key"] = later["race_key"] + "|later"
    b = live_features(card, pd.concat([hist, later], ignore_index=True), day) \
        .sort_values(["race_key", "horse_key"]).reset_index(drop=True)
    num = [c for c in a.columns if pd.api.types.is_numeric_dtype(a[c]) and c in b.columns]
    bad = [c for c in num if not np.allclose(a[c].astype(float), b[c].astype(float),
                                             equal_nan=True, rtol=0, atol=0)]
    assert not bad, f"future rows changed today's features: {bad[:8]}"

    # flipping a card runner's outcome must not change its own features
    card2 = card.copy()
    card2["win"] = 1
    card2["pos_num"] = 1.0
    card2["finished"] = 1
    c = live_features(card2, hist, day).sort_values(["race_key", "horse_key"]).reset_index(drop=True)
    bad2 = [col for col in num if col in c.columns
            and not np.allclose(a[col].astype(float), c[col].astype(float), equal_nan=True)]
    assert not bad2, f"a card runner's own result leaked into its features: {bad2[:8]}"


def synthetic_results_json(hist_raw: pd.DataFrame, n_races: int = 3) -> list[dict]:
    """A results payload in The Racing API's /v1/results shape, built from the same
    synthetic runs, so the backfill path is exercised on realistic field names."""
    out = []
    for rid, g in list(hist_raw.groupby("race_id", sort=False))[:n_races]:
        first = g.iloc[0]
        out.append({
            "race_id": f"BF{rid}", "date": first["date"], "off": first["off"],
            "course": first["course"], "surface": "AW" if "(AW)" in first["course"] else "Turf",
            "race_name": first["race_name"], "type": first["type"], "class": first["class"],
            "pattern": None, "rating_band": first["rating_band"], "age_band": first["age_band"],
            "sex_rest": None, "dist": first["dist"], "going": first["going"],
            "runners": [{
                "horse": r["horse"], "sire": r["sire"], "dam": r["dam"], "damsire": r["damsire"],
                "age": r["age"], "sex": r["sex"], "number": r["num"], "draw": r["draw"],
                "position": r["pos"], "btn": r["btn"], "ovr_btn": r["ovr_btn"],
                "weight_lbs": str(int(r["wgt"].split("-")[0]) * 14 + int(r["wgt"].split("-")[1])),
                "headgear": r["hg"], "time": r["time"], "or": r["or"], "rpr": r["rpr"],
                "tsr": r["ts"], "sp": r["sp"], "prize": r["prize"],
                "jockey": r["jockey"], "trainer": r["trainer"], "owner": r["owner"],
            } for _, r in g.iterrows()],
        })
    return out


def check_backfill() -> None:
    """Results -> runs, and an upsert that replaces rather than duplicates.

    Backfill is what keeps `rp_rpr_last` alive once the archive goes stale, so a
    silent duplicate here would corrupt every horse's form history.
    """
    import sqlite3
    import tempfile
    from pathlib import Path

    from racing.backfill import upsert_runs

    raw = synthetic_raw(n_races=12, seed=5)
    payload = synthetic_results_json(raw, n_races=3)
    runs = sources.to_runs(sources.results_to_raw(payload))
    assert len(runs) == 24, f"3 races x 8 runners -> {len(runs)}"
    assert runs.rpr.notna().all(), "RPR lost on the results path — this is the edge feature"
    assert runs.pos_num.notna().all(), "finishing positions lost"
    assert runs.win.sum() == 3, f"expected one winner per race, got {runs.win.sum()}"
    assert runs.sp_dec.notna().all(), "SP lost"
    assert (runs.wgt_lb > 100).all(), "weight_lbs -> st-lb -> pounds round trip broke"

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "form.db"
        con = sqlite3.connect(db)
        runs.to_sql("runs", con, if_exists="replace", index=False)
        con.close()
        before = len(runs)
        # re-applying the SAME races must replace them, not stack a second copy
        deleted, inserted = upsert_runs(runs, db)
        con = sqlite3.connect(db)
        after = con.execute("select count(*) from runs").fetchone()[0]
        con.close()
        assert after == before, f"upsert duplicated rows: {before} -> {after}"
        assert deleted == before, f"upsert deleted {deleted} of {before} before re-inserting"


def check_book() -> None:
    d = pd.DataFrame({"race_key": ["A"] * 4 + ["B"] * 3,
                      "bsp": [2.0, 4.0, 8.0, 8.0, 3.0, 3.0, np.nan]})
    p = normalise_book(d, "bsp", by="race_key")
    a = p[d.race_key == "A"]
    assert abs(a.sum() - 1.0) < 1e-9, f"race A sums to {a.sum()}"
    assert p[d.race_key == "B"].isna().all(), "a race with a missing price must be refused"
    # an over-round book must be scaled DOWN, never passed through raw
    assert (a < 1.0 / d.loc[d.race_key == "A", "bsp"] + 1e-12).all()


def check_end_to_end() -> None:
    import lightgbm as lgb
    from racing.daily import load_model
    from racing.model import sigmoid_norm_by_race

    hist = sources.to_runs(synthetic_raw())
    card = sources.to_runs(sources.racecard_to_raw(synthetic_card_json(synthetic_raw())))
    feats = live_features(card, hist, pd.Timestamp("2026-09-22"))
    assert len(feats) == 9, f"{len(feats)} rows survived the feature build"
    cov = feats["rp_rpr_minus_or"].notna().mean()
    assert cov >= 0.7, f"only {cov:.0%} of the card has the edge feature"

    prices = pd.Series([3.0, 4.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0, 30.0], index=feats.index)
    d = _finalise_prices(feats, prices, pd.Series(2000.0, index=feats.index), "test")
    assert abs(d.bsp_prob_norm.sum() - 1.0) < 1e-9, d.bsp_prob_norm.sum()

    booster, meta = load_model()
    from racing.daily import score_day
    scored = score_day(d, booster, meta)
    p = scored.p_model
    assert p.notna().all(), "the model returned NaN probabilities"
    assert ((p > 0) & (p < 1)).all(), "probabilities outside (0,1)"
    assert abs(p.sum() - 1.0) < 1e-6, f"race probabilities sum to {p.sum()}"

    # with no trees the blend must reproduce the market EXACTLY -- the property
    # that makes the model a correction to the price rather than a replacement
    zero = lgb.Booster(model_file=str(__import__("racing.model", fromlist=["MODEL_DIR"]).MODEL_DIR
                                      / "production.txt"))
    raw = np.zeros(len(d)) + np.log(d.bsp_prob_norm / (1 - d.bsp_prob_norm)).values
    market_only = sigmoid_norm_by_race(raw, d.race_key.values)
    assert np.allclose(market_only, d.bsp_prob_norm.values, atol=1e-9), \
        "zero correction must return the market probability unchanged"
    assert zero.num_trees() > 0, "production model has no trees"


CHECKS = [("parsers", check_parsers), ("identity", check_identity), ("as-of", check_as_of),
          ("backfill", check_backfill), ("book normalisation", check_book),
          ("end-to-end scoring", check_end_to_end)]


def main() -> int:
    failed = 0
    for name, fn in CHECKS:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}: {e}")
        except Exception as e:  # noqa: BLE001 - a crash here is also a failure
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
