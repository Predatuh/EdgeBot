"""Persistence: Elo tables, compact game history (for form / H2H / rest days)
and the pick log. All live in data/ and get committed back by the Action."""
import csv
import json
import os
import datetime as dt

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "v2")
LOG = os.path.join(DATA, "picks_log.csv")
LOG_FIELDS = ["date", "league", "event_id", "matchup", "pick", "tier",
              "model_prob", "market_prob", "edge", "price", "units", "result", "profit"]


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


# ---- Elo ----
def load_elo(key):
    return _load(f"elo_{key}.json", {})


def save_elo(key, ratings):
    _save(f"elo_{key}.json", ratings)


# ---- processed-event dedupe ----
def seen_event(key, event_id):
    seen = set(_load(f"seen_{key}.json", []))
    hit = event_id in seen
    if not hit:
        seen.add(event_id)
        _save(f"seen_{key}.json", list(seen)[-6000:])
    return hit


# ---- game history: {"d":date,"a":name,"b":name,"w":winner|"Tie"} ----
def load_history(key):
    return _load(f"hist_{key}.json", [])


def save_history(key, hist):
    _save(f"hist_{key}.json", hist[-4000:])


def form(hist, name, n=5):
    """Last n results for a competitor -> (wins, losses, ties, streak_str, rest_days)."""
    games = [g for g in hist if name in (g["a"], g["b"])]
    games = games[-n:]
    w = l = t = 0
    streak = ""
    for g in games:
        if g["w"] == "Tie":
            t += 1; streak += "D"
        elif g["w"] == name:
            w += 1; streak += "W"
        else:
            l += 1; streak += "L"
    rest = None
    allg = [g for g in hist if name in (g["a"], g["b"])]
    if allg:
        try:
            last = dt.date.fromisoformat(allg[-1]["d"])
            rest = (dt.date.today() - last).days
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
    with open(LOG) as f:
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


def append_pick(row):
    rows = read_log()
    if any(r["event_id"] == row["event_id"] and r["pick"] == row["pick"] for r in rows):
        return  # already logged this pick (second run of the day)
    rows.append(row)
    write_log(rows)


def grade_pending(winners):
    """winners: {event_id: winner_name|'Tie'}. Returns (w, l) graded now."""
    rows = read_log()
    w = l = 0
    for r in rows:
        if r["result"]:
            continue
        win = winners.get(r["event_id"])
        if win is None:
            continue
        units = float(r["units"] or 0)
        price = float(r["price"] or 0)
        if win == r["pick"]:
            r["result"] = "W"
            r["profit"] = round(units * ((1 - price) / price), 3) if price > 0 and units > 0 else 0
            w += 1
        else:
            r["result"] = "L"
            r["profit"] = -units
            l += 1
    write_log(rows)
    return w, l


def record_summary():
    rows = read_log()
    out = {}
    for tier in ("EDGE", "LEAN"):
        rs = [r for r in rows if r["tier"] == tier and r["result"]]
        w = sum(1 for r in rs if r["result"] == "W")
        l = sum(1 for r in rs if r["result"] == "L")
        units = sum(float(r["profit"] or 0) for r in rs)
        risked = sum(float(r["units"] or 0) for r in rs)
        out[tier] = {"w": w, "l": l, "units": round(units, 2),
                     "roi": round(units / risked * 100, 1) if risked else 0.0}
    return out
