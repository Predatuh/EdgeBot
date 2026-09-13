"""Rebuild a league's Elo table from ESPN's historical scores.

WHY THIS EXISTS
---------------
Ratings have been trained only on Kalshi's settled results, which reach back weeks
and carry no scores. College football sat at a median of TWO rated games per team,
so every team was still near 1500 and every "edge" the model found was an artefact
of that. Kalshi also only says who won, which is why margin of victory was off.

ESPN has both: seasons of history, with scores. This rebuilds the table from that
and hands the running bot a set of ratings that have actually separated.

Names are the joint. The Elo table is keyed by KALSHI's names ("Ohio St.") while
ESPN says "Ohio State Buckeyes", so each ESPN name is matched against the Kalshi
vocabulary already on disk. Where several Kalshi names match, the most specific
wins - "Ohio State Buckeyes" has to land on "Ohio St." and not on "Ohio".

    python backfill_elo.py --league ncaaf --seasons 3
"""
import argparse
import datetime as dt
import json
import os
import sys

import elo
import espn
import state

GROUPS = {"college-football": 80}        # all FBS, not just the ranked teams


def kalshi_vocabulary(key):
    """Every team name the bot has actually seen on Kalshi for this league."""
    names = set()
    for g in state.load_history(key):
        names.update((g.get("a", ""), g.get("b", "")))
    names.update(k for k in state.load_elo(key) if not k.startswith("_"))
    return {n for n in names if n}


def build_name_map(espn_names, vocab):
    """ESPN displayName -> Kalshi name, where one can be identified.

    Returns (mapping, ambiguous). An ESPN name with no Kalshi counterpart keeps its
    own name: those teams still train their opponents' ratings, they just never get
    bet on. An ambiguous one is left out and reported rather than guessed.
    """
    mapping, ambiguous = {}, {}
    for en in espn_names:
        hits = [kn for kn in vocab if espn.name_match(kn, en)]
        if not hits:
            continue
        best = max(len(espn._tokens(h)) for h in hits)
        top = [h for h in hits if len(espn._tokens(h)) == best]
        if len(top) == 1:
            mapping[en] = top[0]
        else:
            ambiguous[en] = sorted(top)
    return mapping, ambiguous


def fetch(sport, league, start, end, step_days=7):
    """Completed games between two dates, pulled a week at a time."""
    games, day = [], start
    while day <= end:
        upto = min(day + dt.timedelta(days=step_days - 1), end)
        try:
            got = espn.scores_range(sport, league, day, upto, GROUPS.get(league))
        except Exception as e:
            print(f"[backfill] {day}..{upto}: {type(e).__name__}: {str(e)[:70]}")
            got = []
        if got:
            print(f"[backfill] {day}..{upto}: {len(got)} completed games")
        games += got
        day = upto + dt.timedelta(days=1)
    seen, uniq = set(), []
    for g in games:                       # a range request can repeat a game
        sig = (g["d"], g["away"], g["home"])
        if sig in seen:
            continue
        seen.add(sig)
        uniq.append(g)
    uniq.sort(key=lambda g: g["d"])
    return uniq


def rebuild(key, games, name_map, k, home_adv):
    """Train a fresh rating table over the games, oldest first, with MOV on."""
    ratings = {}
    for g in games:
        a = name_map.get(g["away"], g["away"])
        b = name_map.get(g["home"], g["home"])
        if a == b:
            continue
        elo.update(ratings, a, b, g["sa"], g["sb"], k=k, use_mov=True,
                   draw=g["sa"] == g["sb"],
                   home_adv_b=0.0 if g.get("neutral") else home_adv)
    ratings["_games"] = len(games)
    ratings["_espn_through"] = games[-1]["d"] if games else ""
    return ratings


def separation(ratings):
    vals = sorted(v for k, v in ratings.items() if not k.startswith("_"))
    if len(vals) < 2:
        return 0.0, 0.0, len(vals)
    mean = sum(vals) / len(vals)
    sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
    return sd, vals[-1] - vals[0], len(vals)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rebuild Elo from ESPN historical scores")
    ap.add_argument("--league", required=True, help="config.yaml league key, e.g. ncaaf")
    ap.add_argument("--seasons", type=float, default=3.0, help="years of history to pull")
    ap.add_argument("--from", dest="start", default="", help="start date, overrides --seasons")
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    a = ap.parse_args(argv)

    import yaml
    cfg = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")))
    lg = (cfg.get("leagues") or {}).get(a.league)
    if not lg or not lg.get("espn"):
        print(f"[backfill] {a.league} has no espn: [sport, league] in config.yaml")
        return 1
    sport, league = lg["espn"]
    end = dt.date.today()
    start = (dt.date.fromisoformat(a.start) if a.start
             else end - dt.timedelta(days=int(365 * a.seasons)))
    print(f"[backfill] {a.league}: {sport}/{league}, {start} .. {end}")

    games = fetch(sport, league, start, end)
    if not games:
        print("[backfill] no completed games found; nothing to do")
        return 1
    espn_names = {g["away"] for g in games} | {g["home"] for g in games}
    vocab = kalshi_vocabulary(a.league)
    name_map, ambiguous = build_name_map(espn_names, vocab)
    print(f"[backfill] {len(games)} games, {len(espn_names)} ESPN teams, "
          f"{len(vocab)} Kalshi names on file -> {len(name_map)} matched")
    if ambiguous:
        print(f"[backfill] {len(ambiguous)} ambiguous, left unmapped: "
              + ", ".join(f"{k} -> {v}" for k, v in list(ambiguous.items())[:5]))
    unmatched = sorted(vocab - set(name_map.values()))
    if unmatched:
        print(f"[backfill] {len(unmatched)} Kalshi names got no ESPN history "
              f"(they keep their current rating): {unmatched[:8]}")

    old = state.load_elo(a.league)
    ratings = rebuild(a.league, games, name_map, lg.get("k", 24), lg.get("home_adv", 0))
    # a team we bet on but ESPN never covered keeps whatever it had
    for n in unmatched:
        ratings.setdefault(n, old.get(n, elo.BASE_RATING))

    sd_o, rng_o, n_o = separation(old)
    sd_n, rng_n, n_n = separation(ratings)
    print(f"[backfill] before: {n_o} teams, sd {sd_o:.1f}, range {rng_o:.0f} "
          f"({old.get('_games', 0)} games)")
    print(f"[backfill] after : {n_n} teams, sd {sd_n:.1f}, range {rng_n:.0f} "
          f"({ratings['_games']} games, through {ratings['_espn_through']})")
    top = sorted(((k, v) for k, v in ratings.items() if not k.startswith("_")),
                 key=lambda kv: -kv[1])[:8]
    print("[backfill] top: " + ", ".join(f"{k} {v:.0f}" for k, v in top))

    # form and H2H read the same history file and were seeing ~2 games per team
    hist = state.load_history(a.league)
    have = {(g["d"], g["a"], g["b"]) for g in hist}
    added = 0
    for g in games:
        aw = name_map.get(g["away"], g["away"])
        hm = name_map.get(g["home"], g["home"])
        sig = (g["d"], aw, hm)
        if sig in have:
            continue
        have.add(sig)
        hist.append({"d": g["d"], "a": aw, "b": hm,
                     "w": "Tie" if g["sa"] == g["sb"] else (aw if g["sa"] > g["sb"] else hm)})
        added += 1
    hist.sort(key=lambda g: g["d"])

    if a.dry_run:
        print(f"[backfill] dry run: would write {len(ratings)} ratings and add {added} history rows")
        return 0
    state.save_elo(a.league, ratings)
    state.save_history(a.league, hist)
    print(f"[backfill] wrote elo_{a.league}.json and added {added} rows to hist_{a.league}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
