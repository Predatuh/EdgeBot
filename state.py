"""Persistence: Elo tables, compact game history (for form / H2H / rest days),
the pick log and a stats.json analytics summary. All live in data/v2/ and get
committed back to the repo by the Action."""
import csv
import json
import os
import datetime as dt

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "v2")
LOG = os.path.join(DATA, "picks_log.csv")
STATS = os.path.join(DATA, "stats.json")
TIERS = ("EDGE", "LEAN")
LOG_FIELDS = [
    "date", "time_utc", "league", "event_id", "matchup", "pick", "side", "tier",
    "model_raw",     # model probability before blending with the market
    "model_prob",    # blended probability the pick was made on
    "market_prob",   # de-vigged Kalshi probability at pick time
    "edge", "price", "units",
    "elo_pick", "elo_opp", "conf", "notes",
    "research",       # one-paragraph web research brief (research.py)
    "research_lean",  # -3..+3: how the research moved us on the pick
    "research_adj",   # Elo points applied to the pick from that lean
    "research_flag",  # red flags found (an EDGE with a flag is demoted to LEAN)
    "result",        # W / L, filled when Kalshi settles
    "close_prob",    # last traded Kalshi price for the pick (closing line)
    "clv",           # close_prob - price paid: positive = beat the close
    "profit",        # units won/lost
]


def _p(name):
    os.makedirs(DATA, exist_ok=True)
    return os.path.join(DATA, name)


def _load(name, default):
    path = _p(name)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def _save(name, obj):
    with open(_p(name), "w") as f:
        json.dump(obj, f)


def _fl(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _ts(day):
    return int(dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc).timestamp())


# ---- Elo ----
def load_elo(key):
    return _load(f"elo_{key}.json", {})


def save_elo(key, ratings):
    _save(f"elo_{key}.json", ratings)


def top_ratings(ratings, n=10):
    teams = [(k, v) for k, v in ratings.items() if not k.startswith("_")]
    return [{"name": k, "elo": round(v, 1)} for k, v in sorted(teams, key=lambda kv: -kv[1])[:n]]


# ---- processed-event dedupe (load once per league, save once) ----
def load_seen(key):
    return set(_load(f"seen_{key}.json", []))


def save_seen(key, seen):
    _save(f"seen_{key}.json", sorted(seen)[-20000:])


# ---- game history: {"d":date,"a":away,"b":home,"w":winner|"Tie"} ----
def load_history(key):
    return _load(f"hist_{key}.json", [])


def save_history(key, hist):
    _save(f"hist_{key}.json", hist[-6000:])


def history_since(hist, league, back_days=5):
    """Unix ts to start the next settled-results pull from: a few days before
    the newest rated game, and never after the oldest still-ungraded pick.
    None = no history yet, pull everything."""
    dates = [g["d"] for g in hist if g.get("d")]
    if not dates:
        return None                     # no ratings yet: always pull the full history
    try:
        start = dt.date.fromisoformat(max(dates)) - dt.timedelta(days=back_days)
        pend = [r["date"] for r in read_log() if r["league"] == league and not r["result"] and r["date"]]
        if pend:
            start = min(start, dt.date.fromisoformat(min(pend)) - dt.timedelta(days=1))
    except ValueError:
        return None
    return _ts(start)


def form(hist, name, n=5):
    """Last n results for a competitor -> (wins, losses, ties, streak_str, rest_days, games_seen)."""
    allg = [g for g in hist if name in (g["a"], g["b"])]
    w = l = t = 0
    streak = ""
    for g in allg[-n:]:
        if g["w"] == "Tie":
            t += 1; streak += "D"
        elif g["w"] == name:
            w += 1; streak += "W"
        else:
            l += 1; streak += "L"
    rest = None
    if allg:
        try:
            rest = (dt.date.today() - dt.date.fromisoformat(allg[-1]["d"])).days
        except ValueError:
            pass
    return w, l, t, streak, rest, len(allg)


def h2h(hist, a, b):
    ga = [g for g in hist if {g["a"], g["b"]} == {a, b}]
    wa = sum(1 for g in ga if g["w"] == a)
    wb = sum(1 for g in ga if g["w"] == b)
    return wa, wb, len(ga)


# ---- pick log ----
def read_log():
    if not os.path.exists(LOG):
        return []
    with open(LOG, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in LOG_FIELDS:
            r.setdefault(k, "")
    return rows


def write_log(rows):
    os.makedirs(DATA, exist_ok=True)
    with open(LOG, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def logged_pick(event_id):
    """The pick already on record for this event (first run of the day wins), or None."""
    return next((r for r in read_log() if r["event_id"] == event_id), None)


def append_pick(row):
    rows = read_log()
    if any(r["event_id"] == row["event_id"] for r in rows):
        return False
    rows.append(row)
    write_log(rows)
    return True


def grade_pending(winners, closes=None):
    """winners: {event_id: winner_name|'Tie'}; closes: {event_id: {side: close_prob}}.
    Returns (w, l) graded now."""
    rows = read_log()
    w = l = 0
    for r in rows:
        if r["result"]:
            continue
        win = winners.get(r["event_id"])
        if win is None:
            continue
        units, price = _fl(r["units"]), _fl(r["price"])
        cp = (closes or {}).get(r["event_id"], {}).get(r["pick"])
        if cp is not None:
            r["close_prob"] = round(cp, 3)
            if price:
                r["clv"] = round(cp - price, 3)
        if win == r["pick"]:
            r["result"] = "W"
            r["profit"] = round(units * (1 - price) / price, 3) if price > 0 and units > 0 else 0
            w += 1
        else:
            r["result"] = "L"
            r["profit"] = -units if units else 0
            l += 1
    if w or l:
        write_log(rows)
    return w, l


# ---- analytics ----
def _stats(rows):
    g = [r for r in rows if r["result"] in ("W", "L")]
    w = sum(1 for r in g if r["result"] == "W")
    units = sum(_fl(r["profit"]) for r in g)
    risked = sum(_fl(r["units"]) for r in g)
    clv = [_fl(r["clv"]) for r in g if r["clv"] != ""]
    bm = [(_fl(r["model_prob"]) - (r["result"] == "W")) ** 2 for r in g if r["model_prob"] != ""]
    bk = [(_fl(r["market_prob"]) - (r["result"] == "W")) ** 2 for r in g if r["market_prob"] != ""]
    return {
        "w": w, "l": len(g) - w, "n": len(g),
        "pending": sum(1 for r in rows if not r["result"]),
        "units": round(units, 2), "risked": round(risked, 2),
        "roi": round(units / risked * 100, 1) if risked else 0.0,
        "avg_edge": round(sum(_fl(r["edge"]) for r in g) / len(g), 4) if g else None,
        "avg_clv": round(sum(clv) / len(clv), 4) if clv else None,
        "clv_n": len(clv),
        # Brier score of the pick's win probability (lower = better calibrated).
        # If brier_model stays above brier_market, the model isn't adding information.
        "brier_model": round(sum(bm) / len(bm), 4) if bm else None,
        "brier_market": round(sum(bk) / len(bk), 4) if bk else None,
    }


def record_summary():
    rows = read_log()
    week = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    out = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "picks_logged": len(rows),
        "overall": {t: _stats([r for r in rows if r["tier"] == t]) for t in TIERS},
        "last_7_days": {t: _stats([r for r in rows if r["tier"] == t and r["date"] >= week]) for t in TIERS},
        "by_league": {},
    }
    for lg in sorted({r["league"] for r in rows}):
        out["by_league"][lg] = {t: _stats([r for r in rows if r["league"] == lg and r["tier"] == t])
                                for t in TIERS}
    # Did research help? Compare graded picks by how the research leaned.
    def lean_bucket(r):
        if r["research"] == "" and r["research_lean"] == "":
            return "not_researched"
        v = _fl(r["research_lean"])
        return "for_pick" if v > 0 else ("against_pick" if v < 0 else "neutral")
    out["by_research"] = {b: _stats([r for r in rows if lean_bucket(r) == b])
                          for b in ("for_pick", "neutral", "against_pick", "not_researched")}
    out["by_research"]["flagged"] = _stats([r for r in rows if r["research_flag"]])
    return out


def write_stats(summary, ratings_top=None):
    summary = dict(summary)
    if ratings_top:
        summary["top_ratings"] = ratings_top
    with open(_p("stats.json"), "w") as f:
        json.dump(summary, f, indent=1)
