"""python -m pytest racing/test_live_card.py -q  (or run this file directly)"""
from racing import live_card as lc


def _race(rid, off, prices, finished=False, pos=None, status=None):
    runners = []
    book = sum(1 / p for p in prices)
    for i, p in enumerate(prices):
        runners.append({"no": i + 1, "horse": f"H{rid}-{i}", "price": p, "p": (1 / p) / book,
                        "status": (status or {}).get(i, "RUNNER"), "pos": (pos or {}).get(i)})
    return {"id": rid, "off": off, "course": "X", "name": f"R{rid}", "finished": finished,
            "price_src": "books", "runners": runners}


def test_frac_to_dec():
    assert lc.frac_to_dec("11/4") == 3.75
    assert lc.frac_to_dec("Evs") == 2.0
    assert lc.frac_to_dec("10/11") == 1.909
    assert lc.frac_to_dec(None) is None and lc.frac_to_dec("SP") is None


def test_uk_time_converts_utc():
    assert lc.uk_time("2026-09-22", "12:30") == "13:30"      # BST
    assert lc.uk_time("2026-12-01", "12:30") == "12:30"      # GMT


def test_picks_respect_max_price_cap_and_order():
    races = [_race(1, "15:00", [5.0, 6.0, 3.0]),              # fav 3.0 -> ok
             _race(2, "14:00", [4.5, 5.0, 6.0]),              # fav 4.5 > MAX_PRICE -> skip
             _race(3, "13:00", [1.5, 4.0, 9.0]),              # fav 1.5 -> ok, strongest
             _race(4, "12:00", [2.0, 3.0], finished=True)]    # already run -> skip
    picks = lc.build_picks(races, bank=100)
    assert [p["race_id"] for p in picks] == [3, 1]            # sorted by off time
    assert all(p["stake"] == lc.STAKE for p in picks)
    assert lc.build_picks(races, bank=lc.STOP_BANK - 1) == []


def test_picks_capped_at_max_bets():
    races = [_race(i, f"{12 + i}:00", [2.0, 3.0, 6.0]) for i in range(lc.MAX_BETS + 3)]
    assert len(lc.build_picks(races, bank=100)) == lc.MAX_BETS


def test_settle_won_lost_void_and_taken_price():
    r1 = _race(1, "13:00", [2.0, 4.0], finished=True, pos={0: 1, 1: 2})
    r2 = _race(2, "14:00", [2.0, 4.0], finished=True, pos={0: 2, 1: 1})
    r3 = _race(3, "15:00", [2.0, 4.0], finished=True, pos={1: 1}, status={0: "NONRUNNER"})
    picks = [{"race_id": i, "horse": f"H{i}-0", "price": 2.0, "stake": 2.0} for i in (1, 2, 3)]
    day = lc.settle({"races": [r1, r2, r3], "picks": picks}, {"1": {"placed": True, "price": 2.5}})
    res = {p["race_id"]: (p["result"], p["pnl"]) for p in day["picks"]}
    assert res == {1: ("won", 3.0), 2: ("lost", -2.0), 3: ("void", 0.0)}
    assert day["summary"] == {"staked": 4.0, "returned": 5.0, "pnl": 1.0, "settled": True}


def test_settle_skipped_and_pending():
    r1 = _race(1, "13:00", [2.0, 4.0], finished=True, pos={0: 1})
    r2 = _race(2, "14:00", [2.0, 4.0])
    picks = [{"race_id": 1, "horse": "H1-0", "price": 2.0, "stake": 2.0},
             {"race_id": 2, "horse": "H2-0", "price": 2.0, "stake": 2.0}]
    day = lc.settle({"races": [r1, r2], "picks": picks}, {"1": {"placed": False}})
    assert [p["result"] for p in day["picks"]] == ["skipped", "pending"]
    assert day["summary"]["pnl"] == 0 and day["summary"]["settled"] is False


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
