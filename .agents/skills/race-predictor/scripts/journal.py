#!/usr/bin/env python3
"""Bet journal: record every prediction, settle results, measure performance.

Pure standard library. Append-only CSV so it is easy to open in a spreadsheet
and hard to corrupt. The journal is the single most important piece of the whole
setup: without an honest record of predictions vs. outcomes you cannot tell
whether the model has a real edge or you are just remembering the wins.

Default journal file: <git repo root>/journal/bets.csv (override with --file).

Commands
--------
    journal.py log --track "Fairmount Park" --time 21:10 --race 5 \
        --selection "Steampunk" --model-prob 0.48 --odds 7/5 --stake 5 \
        [--edge 0.15] [--kelly-fraction 0.25] [--date 2026-07-28] [--notes "..."]

    journal.py settle --id 3 --result win [--closing-odds 6/4]
    journal.py settle --id 3 --result lose
    journal.py list [--open]
    journal.py report

`report` prints strike rate, staked/returned, profit, ROI, average edge,
closing-line value (CLV), and a calibration check (did things the model called
~40% actually win ~40% of the time?).
"""

import argparse
import csv
import os
import sys
from datetime import date as _date

FIELDS = ["id", "date", "track", "time", "race", "selection", "model_prob",
          "odds_frac", "odds_dec", "edge", "stake", "kelly_fraction", "status",
          "result", "closing_odds_dec", "payout", "pnl", "clv", "notes"]


def parse_odds(value):
    if value is None or value == "":
        return None
    s = str(value).strip().lower()
    if s in ("evs", "evens", "even"):
        return 2.0
    for sep in ("/", "-"):
        if sep in s:
            num, den = s.split(sep, 1)
            return 1.0 + float(num) / float(den)
    return float(s)


def git_root(start=None):
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.path.abspath(start or os.getcwd())
        d = parent


def default_file():
    return os.path.join(git_root(), "journal", "bets.csv")


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def write_rows(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def next_id(rows):
    ids = [int(r["id"]) for r in rows if r.get("id", "").isdigit()]
    return (max(ids) + 1) if ids else 1


def cmd_log(args):
    path = args.file or default_file()
    rows = read_rows(path)
    dec = parse_odds(args.odds)
    row = {
        "id": next_id(rows),
        "date": args.date or _date.today().isoformat(),
        "track": args.track,
        "time": args.time,
        "race": args.race or "",
        "selection": args.selection,
        "model_prob": f"{args.model_prob:.4f}" if args.model_prob is not None else "",
        "odds_frac": args.odds,
        "odds_dec": f"{dec:.3f}" if dec else "",
        "edge": f"{args.edge:.4f}" if args.edge is not None else "",
        "stake": f"{args.stake:.2f}",
        "kelly_fraction": f"{args.kelly_fraction:g}" if args.kelly_fraction is not None else "",
        "status": "open",
        "result": "", "closing_odds_dec": "", "payout": "", "pnl": "", "clv": "",
        "notes": args.notes or "",
    }
    rows.append(row)
    write_rows(path, rows)
    print(f"Logged bet #{row['id']}: {args.stake:.2f} on {args.selection} "
          f"@ {args.odds} ({args.track} {args.time}) -> {path}")


def cmd_settle(args):
    path = args.file or default_file()
    rows = read_rows(path)
    hit = False
    for r in rows:
        if r.get("id") == str(args.id):
            hit = True
            dec = float(r["odds_dec"]) if r.get("odds_dec") else parse_odds(r.get("odds_frac"))
            stake = float(r["stake"])
            won = args.result.lower() in ("win", "won", "w", "1")
            payout = stake * dec if won else 0.0
            pnl = payout - stake
            r["status"] = "settled"
            r["result"] = "win" if won else "lose"
            r["payout"] = f"{payout:.2f}"
            r["pnl"] = f"{pnl:+.2f}"
            closing = parse_odds(args.closing_odds) if args.closing_odds else None
            if closing:
                r["closing_odds_dec"] = f"{closing:.3f}"
                # CLV: how much better your taken price was than the closing price.
                # Positive = you beat the closing line (a durable edge signal).
                r["clv"] = f"{((dec - 1.0) / (closing - 1.0) - 1.0) * 100:+.1f}%"
            print(f"Settled #{args.id}: {r['result'].upper()} pnl {r['pnl']}"
                  + (f", CLV {r['clv']}" if r.get('clv') else ""))
    if not hit:
        print(f"No bet with id {args.id}", file=sys.stderr)
        return 1
    write_rows(path, rows)
    return 0


def cmd_list(args):
    path = args.file or default_file()
    rows = read_rows(path)
    if args.open:
        rows = [r for r in rows if r.get("status") == "open"]
    if not rows:
        print("No entries.")
        return 0
    for r in rows:
        tail = (f"  [{r['status']}]" if r.get("status") != "settled"
                else f"  {r['result']} pnl {r.get('pnl','')}")
        print(f"#{r['id']:>3} {r['date']} {r['track']} {r['time']} "
              f"R{r.get('race','')} {r['selection']} @ {r['odds_frac']} "
              f"stake {r['stake']}{tail}")
    return 0


def cmd_report(args):
    path = args.file or default_file()
    rows = read_rows(path)
    settled = [r for r in rows if r.get("status") == "settled"]
    open_bets = [r for r in rows if r.get("status") == "open"]
    print(f"Journal: {path}")
    print(f"Total logged: {len(rows)}   settled: {len(settled)}   open: {len(open_bets)}")
    if settled:
        n = len(settled)
        wins = sum(1 for r in settled if r["result"] == "win")
        staked = sum(float(r["stake"]) for r in settled)
        returned = sum(float(r.get("payout") or 0) for r in settled)
        profit = returned - staked
        roi = profit / staked * 100 if staked else 0.0
        edges = [float(r["edge"]) for r in settled if r.get("edge")]
        clvs = [float(r["clv"].rstrip("%")) for r in settled if r.get("clv")]
        print(f"\nStrike rate : {wins}/{n} = {wins/n*100:.1f}%")
        print(f"Staked      : {staked:.2f}")
        print(f"Returned    : {returned:.2f}")
        print(f"Profit      : {profit:+.2f}")
        print(f"ROI         : {roi:+.1f}%")
        if edges:
            print(f"Avg edge    : {sum(edges)/len(edges)*100:+.1f}%")
        if clvs:
            print(f"Avg CLV     : {sum(clvs)/len(clvs):+.1f}%  "
                  f"(beating the closing line is the strongest sign of a real edge)")
        # Calibration: bucket by model probability, compare to actual win rate.
        buckets = [(0, 20), (20, 35), (35, 50), (50, 100)]
        cal = []
        for lo, hi in buckets:
            grp = [r for r in settled if r.get("model_prob")
                   and lo <= float(r["model_prob"]) * 100 < hi]
            if grp:
                w = sum(1 for r in grp if r["result"] == "win")
                pred = sum(float(r["model_prob"]) for r in grp) / len(grp) * 100
                cal.append((lo, hi, len(grp), pred, w / len(grp) * 100))
        if cal:
            print("\nCalibration (model said vs. actually won):")
            for lo, hi, cnt, pred, act in cal:
                print(f"  {lo:>2}-{hi:<3}%  n={cnt:<3}  predicted {pred:4.1f}%  "
                      f"actual {act:4.1f}%")
    if open_bets:
        print(f"\nOpen bets ({len(open_bets)}):")
        for r in open_bets:
            print(f"  #{r['id']} {r['date']} {r['track']} {r['time']} "
                  f"{r['selection']} @ {r['odds_frac']} stake {r['stake']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bet journal.")
    ap.add_argument("--file", help="journal CSV path (default: <repo>/journal/bets.csv)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    lg = sub.add_parser("log")
    lg.add_argument("--track", required=True)
    lg.add_argument("--time", required=True)
    lg.add_argument("--race")
    lg.add_argument("--selection", required=True)
    lg.add_argument("--odds", required=True)
    lg.add_argument("--stake", type=float, required=True)
    lg.add_argument("--model-prob", type=float)
    lg.add_argument("--edge", type=float)
    lg.add_argument("--kelly-fraction", type=float)
    lg.add_argument("--date")
    lg.add_argument("--notes")
    lg.set_defaults(func=cmd_log)

    st = sub.add_parser("settle")
    st.add_argument("--id", type=int, required=True)
    st.add_argument("--result", required=True)
    st.add_argument("--closing-odds")
    st.set_defaults(func=cmd_settle)

    ls = sub.add_parser("list")
    ls.add_argument("--open", action="store_true")
    ls.set_defaults(func=cmd_list)

    rp = sub.add_parser("report")
    rp.set_defaults(func=cmd_report)

    args = ap.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
