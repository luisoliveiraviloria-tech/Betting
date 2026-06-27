#!/usr/bin/env python3
"""
Opponent-adjusted Poisson models for total CORNERS and total CARDS, estimated
in a single no-lookahead chronological pass (each match is predicted from only
what was known before it, then folds into the running stats).

Corners: a team has an attack factor (corners won) and a defence factor
(corners conceded), both relative to the league average and shrunk toward it
for small samples. Expected home/away corners multiply the league venue
baseline by the relevant factors; the total is Poisson(sum).

Cards: a team has a "receive" factor (own cards) and an "induce" factor
(cards the opponent gets when facing them). Expected cards combine the two
sides, then multiply by a REFEREE factor (the ref's cards/game vs league,
shrunk) where a referee is known. Referee is the headline signal and is only
present for the English leagues in the data.

Shrinkage everywhere uses pseudo-counts toward the (running) league mean so
early-season / small-sample teams and refs don't produce wild factors.
"""
import csv
import datetime
import math
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# shrinkage pseudo-counts (games) toward the league mean
PRIOR_TEAM = 8.0
PRIOR_REF = 12.0
# seed league means before enough data has accrued
SEED_CORNERS = 10.0
SEED_CARDS = 4.0
# Final-lambda shrink toward the league mean. Raw predictions are over-dispersed
# (the model thinks it can separate matches more than it really can, so the
# reliability curve fans out at the extremes). <1 pulls predictions toward the
# mean; tuned by backtest.py to best-calibrate log-loss. 1.0 = no shrink.
SHRINK_CORNERS = 0.55
SHRINK_CARDS = 0.70


def parse_date(s):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def load_matches():
    rows = []
    with open(os.path.join(DATA_DIR, "matches.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = parse_date(r["Date"])
            if d is None:
                continue
            try:
                rec = {
                    "date": d, "div": r["Div"], "home": r["HomeTeam"], "away": r["AwayTeam"],
                    "ref": r["Referee"].strip(),
                    "hc": int(r["HC"]), "ac": int(r["AC"]),
                    "cards": int(r["HY"]) + int(r["AY"]) + int(r["HR"]) + int(r["AR"]),
                    "hcards": int(r["HY"]) + int(r["HR"]),
                    "acards": int(r["AY"]) + int(r["AR"]),
                }
            except (ValueError, KeyError):
                continue
            rows.append(rec)
    rows.sort(key=lambda r: r["date"])
    return rows


def poisson_sf(lam, n):
    """P(X >= n) for X ~ Poisson(lam)."""
    if n <= 0:
        return 1.0
    cdf = 0.0
    term = math.exp(-lam)
    for k in range(n):
        if k > 0:
            term *= lam / k
        cdf += term
    return max(0.0, 1.0 - cdf)


def p_over(lam, line):
    """P(total > line) for a .5 line, e.g. line=9.5 -> P(X>=10)."""
    return poisson_sf(lam, int(math.floor(line)) + 1)


def _shrunk(total, games, prior_n, prior_rate):
    return (total + prior_n * prior_rate) / (games + prior_n)


class StatsEngine:
    """Running, no-lookahead league/team/referee statistics."""

    def __init__(self):
        self.lg = {}    # div -> running sums
        self.team = {}  # (div,team) -> sums
        self.ref = {}   # ref -> sums

    def _lg(self, div):
        return self.lg.setdefault(div, {"g": 0, "hc": 0, "ac": 0, "cards": 0})

    def _team(self, div, t):
        return self.team.setdefault((div, t), {
            "g": 0, "cf": 0, "ca": 0, "recv": 0, "induce": 0})

    def _ref(self, name):
        return self.ref.setdefault(name, {"g": 0, "cards": 0})

    # ---- league running means (pre-match) ----
    def lg_means(self, div):
        l = self._lg(div)
        if l["g"] == 0:
            return {"home_c": SEED_CORNERS / 2, "away_c": SEED_CORNERS / 2,
                    "team_c": SEED_CORNERS / 2, "cards": SEED_CARDS}
        return {
            "home_c": l["hc"] / l["g"], "away_c": l["ac"] / l["g"],
            "team_c": (l["hc"] + l["ac"]) / (2 * l["g"]),
            "cards": l["cards"] / l["g"],
        }

    # ---- predictions (use only pre-match state) ----
    def predict_corners(self, div, home, away):
        m = self.lg_means(div)
        H, A = self._team(div, home), self._team(div, away)
        att_h = _shrunk(H["cf"], H["g"], PRIOR_TEAM, m["team_c"]) / m["team_c"]
        att_a = _shrunk(A["cf"], A["g"], PRIOR_TEAM, m["team_c"]) / m["team_c"]
        def_h = _shrunk(H["ca"], H["g"], PRIOR_TEAM, m["team_c"]) / m["team_c"]
        def_a = _shrunk(A["ca"], A["g"], PRIOR_TEAM, m["team_c"]) / m["team_c"]
        exp_home = m["home_c"] * att_h * def_a
        exp_away = m["away_c"] * att_a * def_h
        lam = exp_home + exp_away
        mean = m["home_c"] + m["away_c"]
        return mean + SHRINK_CORNERS * (lam - mean)

    def predict_cards(self, div, home, away, ref, use_ref=True):
        m = self.lg_means(div)
        team_c = m["cards"] / 2
        H, A = self._team(div, home), self._team(div, away)
        recv_h = _shrunk(H["recv"], H["g"], PRIOR_TEAM, team_c)
        recv_a = _shrunk(A["recv"], A["g"], PRIOR_TEAM, team_c)
        ind_h = _shrunk(H["induce"], H["g"], PRIOR_TEAM, team_c)
        ind_a = _shrunk(A["induce"], A["g"], PRIOR_TEAM, team_c)
        exp_home = recv_h * ind_a / team_c
        exp_away = recv_a * ind_h / team_c
        lam = exp_home + exp_away
        if use_ref and ref:
            R = self._ref(ref)
            ref_fac = _shrunk(R["cards"], R["g"], PRIOR_REF, m["cards"]) / m["cards"]
            lam *= ref_fac
        return m["cards"] + SHRINK_CARDS * (lam - m["cards"])

    # ---- fold a played match into running state ----
    def update(self, r):
        l = self._lg(r["div"])
        l["g"] += 1
        l["hc"] += r["hc"]
        l["ac"] += r["ac"]
        l["cards"] += r["cards"]
        H, A = self._team(r["div"], r["home"]), self._team(r["div"], r["away"])
        H["g"] += 1
        H["cf"] += r["hc"]
        H["ca"] += r["ac"]
        H["recv"] += r["hcards"]
        H["induce"] += r["acards"]
        A["g"] += 1
        A["cf"] += r["ac"]
        A["ca"] += r["hc"]
        A["recv"] += r["acards"]
        A["induce"] += r["hcards"]
        if r["ref"]:
            R = self._ref(r["ref"])
            R["g"] += 1
            R["cards"] += r["cards"]
