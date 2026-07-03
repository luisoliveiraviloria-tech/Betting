#!/usr/bin/env python3
"""
Bankroll + bet ledger for the grind system. State: bankroll.json, ledger.csv.

Usage:
  python3 ledger.py status
  python3 ledger.py add --match "X vs Y" --sel "Over 2.5" --book "Betfair" \
      --odds 1.85 --stake 3.50 --ev 0.05
  python3 ledger.py settle --id 3 --result win|loss|void
"""
import argparse
import csv
import datetime
import json
import os

from config import STARTING_BANKROLL

DIR = os.path.dirname(__file__)
BANKROLL_F = os.path.join(DIR, "bankroll.json")
LEDGER_F = os.path.join(DIR, "ledger.csv")
FIELDS = ["id", "date", "match", "sel", "book", "odds", "stake", "ev",
          "result", "pnl", "bankroll_after"]


def current_bankroll():
    if os.path.exists(BANKROLL_F):
        with open(BANKROLL_F) as f:
            return json.load(f)["bankroll"]
    return STARTING_BANKROLL


def save_bankroll(v):
    with open(BANKROLL_F, "w") as f:
        json.dump({"bankroll": round(v, 2)}, f)


def rows():
    if not os.path.exists(LEDGER_F):
        return []
    with open(LEDGER_F) as f:
        return list(csv.DictReader(f))


def write(all_rows):
    with open(LEDGER_F, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    a = sub.add_parser("add")
    for k in ("match", "sel", "book"):
        a.add_argument(f"--{k}", required=True)
    a.add_argument("--odds", type=float, required=True)
    a.add_argument("--stake", type=float, required=True)
    a.add_argument("--ev", type=float, default=None)
    s = sub.add_parser("settle")
    s.add_argument("--id", required=True)
    s.add_argument("--result", choices=["win", "loss", "void"], required=True)
    args = ap.parse_args()

    rs = rows()
    bk = current_bankroll()

    if args.cmd == "status":
        pend = [r for r in rs if r["result"] == "pending"]
        settled = [r for r in rs if r["result"] in ("win", "loss")]
        pnl = sum(float(r["pnl"] or 0) for r in settled)
        wins = sum(1 for r in settled if r["result"] == "win")
        print(f"Bankroll £{bk:.2f} (start £{STARTING_BANKROLL:.2f}, "
              f"P&L {pnl:+.2f})")
        print(f"Settled: {len(settled)} ({wins}W-{len(settled)-wins}L) | "
              f"Pending: {len(pend)}")
        for r in pend:
            print(f"  #{r['id']} {r['date']} {r['match']} {r['sel']} "
                  f"@{r['odds']} £{r['stake']}")
    elif args.cmd == "add":
        rid = str(max([int(r["id"]) for r in rs], default=0) + 1)
        rs.append({"id": rid, "date": datetime.date.today().isoformat(),
                   "match": args.match, "sel": args.sel, "book": args.book,
                   "odds": args.odds, "stake": args.stake,
                   "ev": args.ev if args.ev is not None else "",
                   "result": "pending", "pnl": "", "bankroll_after": ""})
        write(rs)
        print(f"Logged bet #{rid}: {args.match} | {args.sel} @{args.odds} "
              f"£{args.stake:.2f} (bankroll £{bk:.2f}, not yet deducted — "
              f"deducted on settle)")
    elif args.cmd == "settle":
        for r in rs:
            if r["id"] == args.id and r["result"] == "pending":
                stake, odds = float(r["stake"]), float(r["odds"])
                pnl = {"win": stake * (odds - 1), "loss": -stake, "void": 0.0}[args.result]
                bk += pnl
                r["result"], r["pnl"] = args.result, f"{pnl:.2f}"
                r["bankroll_after"] = f"{bk:.2f}"
                save_bankroll(bk)
                write(rs)
                print(f"#{r['id']} {args.result.upper()} {pnl:+.2f} -> bankroll £{bk:.2f}")
                return
        print(f"No pending bet with id {args.id}")


if __name__ == "__main__":
    main()
